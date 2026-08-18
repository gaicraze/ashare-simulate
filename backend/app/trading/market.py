"""交易时段判断 + 盘面快照 + 实时行情（腾讯直连，盘中拉取）。

A 股交易时段（北京时间）：
- 周一至周五 09:30–11:30、13:00–15:00 为连续竞价（盘中）；
- 其余时间为休市 / 盘前 / 午休 / 收盘后。

盘中时，本模块尽量拉取「有关数据」的实时快照（指数 + 指定个股），
网络失败或非盘中时自动降级到本地数据湖的最新交易日日线，绝不抛异常。
"""
from __future__ import annotations

import json
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Any

from ..core import config
from ..data import lake, sources

try:
    from zoneinfo import ZoneInfo

    _TZ: timezone | Any = ZoneInfo("Asia/Shanghai")
except Exception:  # noqa: BLE001
    _TZ = timezone(timedelta(hours=8))  # 兜底：固定 UTC+8

# 常用指数（名称 → 腾讯 symbol）
_INDEX_SYMBOLS = {
    "沪深300": "sh000300",
    "上证指数": "sh000001",
    "深证成指": "sz399001",
    "创业板指": "sz399006",
}


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v


def beijing_now() -> datetime:
    return datetime.now(_TZ)


def trading_session(now: datetime | None = None) -> dict:
    """返回当前盘面时钟信息：是否盘中、所处时段、北京时刻。"""
    now = now or beijing_now()
    wd = now.weekday()
    t = now.time()
    morning = dtime(9, 30) <= t <= dtime(11, 30)
    afternoon = dtime(13, 0) <= t <= dtime(15, 0)
    is_weekday = wd < 5
    is_trading = is_weekday and (morning or afternoon)

    if not is_weekday:
        session = "周末休市"
    elif t < dtime(9, 30):
        session = "开盘前"
    elif morning:
        session = "上午盘中"
    elif dtime(11, 30) < t < dtime(13, 0):
        session = "午间休市"
    elif afternoon:
        session = "下午盘中"
    else:
        session = "收盘后"

    return {
        "beijing_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "weekday": wd,
        "is_trading": is_trading,
        "session": session,
    }


def latest_daily_snapshot() -> dict:
    """最新交易日 + 市场状态 + 涨跌/涨跌停/成交额（来自本地数据湖）。"""
    conn = lake.get_connection(config.DB_PATH, read_only=True)
    try:
        row = conn.execute("SELECT MAX(trade_date) FROM daily").fetchone()
        latest = str(row[0]) if row and row[0] else None
        if not latest:
            return {"latest_trade_date": None, "regime": "未知", "snapshot": None}
        snap = conn.execute(
            """
            SELECT COUNT(*) AS total,
              SUM(CASE WHEN pct_change > 0 THEN 1 ELSE 0 END) AS up,
              SUM(CASE WHEN pct_change < 0 THEN 1 ELSE 0 END) AS down,
              SUM(CASE WHEN pct_change >= 9.8 THEN 1 ELSE 0 END) AS limit_up,
              SUM(CASE WHEN pct_change <= -9.8 THEN 1 ELSE 0 END) AS limit_down,
              ROUND(AVG(pct_change), 3) AS avg_pct,
              ROUND(SUM(amount), 0) AS total_amount
            FROM daily WHERE trade_date = ?
            """,
            [latest],
        ).fetchone()
        sr = conn.execute(
            """
            SELECT close,
              AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS ma20,
              AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) AS ma60
            FROM indices WHERE code='000300' AND trade_date <= ?
            ORDER BY trade_date DESC LIMIT 1
            """,
            [latest],
        ).fetchone()
        regime = "未知"
        if sr and sr[0] and sr[1] and sr[2]:
            close, ma20, ma60 = sr[0], sr[1], sr[2]
            if close > ma20 > ma60:
                regime = "牛市"
            elif close < ma20 < ma60:
                regime = "熊市"
            else:
                regime = "震荡市"
        return {
            "latest_trade_date": latest,
            "regime": regime,
            "index_close": _f(sr[0]) if sr else None,
            "ma20": _f(sr[1]) if sr else None,
            "ma60": _f(sr[2]) if sr else None,
            "snapshot": {
                "total": snap[0],
                "up": snap[1],
                "down": snap[2],
                "limit_up": snap[3],
                "limit_down": snap[4],
                "avg_pct": snap[5],
                "total_amount": snap[6],
            },
        }
    finally:
        conn.close()


def fetch_live_index() -> dict:
    """盘中拉取常用指数实时快照（现价 / 昨收 / 涨跌幅）。失败返回空 dict。"""
    try:
        q = ",".join(_INDEX_SYMBOLS.values())
        with sources._client(10.0) as client:  # noqa: SLF001
            r = client.get(sources.TENCENT_QUOTE_URL.format(q=q))
            text = r.content.decode("gbk", errors="ignore")
    except Exception:  # noqa: BLE001
        return {}

    out: dict[str, dict] = {}
    name_by_symbol = {v: k for k, v in _INDEX_SYMBOLS.items()}
    for line in text.strip().split(";"):
        line = line.strip()
        if "=" not in line:
            continue
        symbol = line.split("=", 1)[0].replace("v_", "").strip()
        payload = line.split("=", 1)[1].strip().strip(";").strip('"')
        parts = payload.split("~")
        if len(parts) < 6:
            continue
        price = _f(parts[3])
        prev = _f(parts[4])
        pct = _f(parts[32]) if len(parts) > 32 else None
        if pct is None and price and prev:
            pct = (price / prev - 1) * 100
        name = name_by_symbol.get(symbol, parts[1] if len(parts) > 1 else symbol)
        out[name] = {
            "name": name,
            "symbol": symbol,
            "price": price,
            "prev_close": prev,
            "pct_change": _f(round(pct, 3)) if pct is not None else None,
        }
    return out


def fetch_live_quotes(codes: list[str]) -> dict[str, dict]:
    """盘中拉取指定个股实时快照，返回 {code: quote}。失败返回空 dict。"""
    codes = [c for c in (codes or []) if len(c) == 6 and c.isdigit()]
    if not codes:
        return {}
    try:
        quotes = sources.fetch_quotes(codes)
    except Exception:  # noqa: BLE001
        return {}
    return {q["code"]: q for q in quotes if q.get("code")}


def market_context(pull_intraday: bool = True) -> dict:
    """汇总「盘面环境」：时钟 + 最新交易日快照 +（盘中）实时指数。"""
    clock = trading_session()
    daily = latest_daily_snapshot()
    live_index: dict = {}
    data_mode = "eod"
    if pull_intraday and clock["is_trading"]:
        live_index = fetch_live_index()
        if live_index:
            data_mode = "intraday"
    notes: list[str] = []
    if clock["is_trading"] and not live_index:
        notes.append("当前为盘中，但实时行情拉取失败，已降级为最新交易日日线数据")
    return {
        "clock": clock,
        "latest_trade_date": daily.get("latest_trade_date"),
        "regime": daily.get("regime"),
        "index_close": daily.get("index_close"),
        "ma20": daily.get("ma20"),
        "ma60": daily.get("ma60"),
        "snapshot": daily.get("snapshot"),
        "live_index": live_index,
        "data_mode": data_mode,
        "notes": notes,
    }


def market_context_jsonable(ctx: dict) -> dict:
    """确保 market_context 结果可 JSON 序列化（数值已为原生类型，这里兜底转换）。"""
    return json.loads(json.dumps(ctx, ensure_ascii=False, default=str))
