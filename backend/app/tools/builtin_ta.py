"""内置工具·技术指标类（Mi姐等趋势策略所需，点内时，防未来函数）。

覆盖策略里常见、但旧工具集缺失的量化信号：
- 周线/月线/日线多周期均线（月线定周期、周线定方向、日线定仓位）
- MACD（DIF/DEA/红柱、金叉/顶背离辅助判断）
- RSI（超买超卖）
- 布林带（阶段高位）
- 流通市值排名（市值门槛 / 板块内市值排名）
- 行业/板块涨幅排名（主线识别）

所有查询都只用 ``trade_date <= date`` 的数据，杜绝未来数据泄漏。
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from typing import Any

from . import db
from .base import Tool


# ---------------------------------------------------------------------------
# 纯 Python 指标计算（点内时：输入为截止 date 的升序行情）
# ---------------------------------------------------------------------------
def _sma(vals: list[float], n: int) -> float | None:
    if len(vals) < n:
        return None
    return round(sum(vals[-n:]) / n, 4)


def _std(vals: list[float], n: int) -> float | None:
    if len(vals) < n:
        return None
    window = vals[-n:]
    m = sum(window) / n
    var = sum((x - m) ** 2 for x in window) / n
    return round(var ** 0.5, 4)


def _rsi_wilder(closes: list[float], n: int) -> float | None:
    """Wilder 平滑 RSI。"""
    if len(closes) < n + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains[:n]) / n
    avg_loss = sum(losses[:n]) / n
    for i in range(n, len(gains)):
        avg_gain = (avg_gain * (n - 1) + gains[i]) / n
        avg_loss = (avg_loss * (n - 1) + losses[i]) / n
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def _ema_series(vals: list[float], n: int) -> list[float]:
    if not vals:
        return []
    k = 2 / (n + 1)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _macd_series(closes: list[float]) -> tuple[list[float], list[float], list[float]]:
    """返回 (dif, dea, hist) 三条序列，与 closes 等长；hist=(dif-dea)*2（A股惯例）。"""
    ema12 = _ema_series(closes, 12)
    ema26 = _ema_series(closes, 26)
    dif = [a - b for a, b in zip(ema12, ema26)]
    dea = _ema_series(dif, 9)
    hist = [(d - e) * 2 for d, e in zip(dif, dea)]
    return dif, dea, hist


def _resample(rows: list[dict], period: str) -> list[dict]:
    """把日线 rows（升序）聚合成周线/月线；daily 原样返回。"""
    if period == "daily":
        return rows
    groups: OrderedDict[tuple, list[dict]] = OrderedDict()
    for r in rows:
        d = datetime.strptime(r["trade_date"], "%Y-%m-%d").date()
        key = (d.isocalendar()[0], d.isocalendar()[1]) if period == "weekly" else (d.year, d.month)
        groups.setdefault(key, []).append(r)
    out: list[dict] = []
    for g in groups.values():
        highs = [x["high"] for x in g if x.get("high") is not None]
        lows = [x["low"] for x in g if x.get("low") is not None]
        out.append(
            {
                "trade_date": g[-1]["trade_date"],
                "open": g[0].get("open"),
                "high": max(highs) if highs else None,
                "low": min(lows) if lows else None,
                "close": g[-1].get("close"),
                "volume": sum(x.get("volume") or 0 for x in g),
                "amount": sum(x.get("amount") or 0 for x in g),
            }
        )
    return out


def _fetch_daily(code: str, date: str, scope: str, days: int) -> list[dict]:
    table = "indices" if scope == "index" else "daily"
    rows = db.rows(
        f"""
        SELECT * FROM (
          SELECT trade_date, open, high, low, close, volume, amount
          FROM {table} WHERE code = ? AND trade_date <= ?
          ORDER BY trade_date DESC LIMIT ?
        ) ORDER BY trade_date ASC
        """,
        [code, date, days],
    )
    for r in rows:
        r["trade_date"] = str(r["trade_date"])
    return rows


def _ta_block(rows: list[dict], period: str, with_macd: bool, bars_limit: int) -> dict:
    """把一段（已 resample 的）行情算成 TA 指标块。"""
    bars = _resample(rows, period)
    closes = [b["close"] for b in bars if b.get("close") is not None]
    if not closes:
        return {"error": "数据不足", "period": period}

    last = bars[-1]
    out: dict[str, Any] = {
        "period": period,
        "date": last["trade_date"],
        "close": closes[-1],
        "ma5": _sma(closes, 5),
        "ma10": _sma(closes, 10),
        "ma20": _sma(closes, 20),
        "ma60": _sma(closes, 60),
        "rsi6": _rsi_wilder(closes, 6),
        "rsi12": _rsi_wilder(closes, 12),
        "rsi24": _rsi_wilder(closes, 24),
    }
    mid = _sma(closes, 20)
    sd = _std(closes, 20)
    out["boll_mid"] = mid
    out["boll_upper"] = round(mid + 2 * sd, 4) if mid is not None and sd is not None else None
    out["boll_lower"] = round(mid - 2 * sd, 4) if mid is not None and sd is not None else None

    # 均线金叉/死叉（最近一期 vs 上一期）
    if len(closes) >= 21:
        prev_ma5 = _sma(closes[:-1], 5)
        prev_ma20 = _sma(closes[:-1], 20)
        out["ma5_cross_up_ma20"] = (out["ma5"] is not None and out["ma20"] is not None
                                    and prev_ma5 is not None and prev_ma20 is not None
                                    and prev_ma5 <= prev_ma20 and out["ma5"] > out["ma20"])
        out["ma5_cross_down_ma20"] = (out["ma5"] is not None and out["ma20"] is not None
                                      and prev_ma5 is not None and prev_ma20 is not None
                                      and prev_ma5 >= prev_ma20 and out["ma5"] < out["ma20"])

    # 日线 MA20 斜率（近5期 MA20 递增）
    ma20_series = [_sma(closes[: i + 1], 20) for i in range(len(closes))]
    ma20_series = [x for x in ma20_series if x is not None]
    if len(ma20_series) >= 5:
        out["ma20_slope_up"] = all(ma20_series[-5:][j] < ma20_series[-5:][j + 1] for j in range(4))

    if with_macd and len(closes) >= 35:
        dif, dea, hist = _macd_series(closes)
        out["macd_dif"] = round(dif[-1], 4)
        out["macd_dea"] = round(dea[-1], 4)
        out["macd_hist"] = round(hist[-1], 4)
        out["macd_dif_prev"] = round(dif[-2], 4)
        out["macd_dea_prev"] = round(dea[-2], 4)
        out["macd_hist_prev"] = round(hist[-2], 4)
        # 零轴上金叉：DIF 上穿 DEA 且 DIF>0
        out["macd_golden_cross"] = (dif[-2] <= dea[-2] and dif[-1] > dea[-1] and dif[-1] > 0)
        out["macd_death_cross"] = (dif[-2] >= dea[-2] and dif[-1] < dea[-1])
        # 红柱由缩短转为放大（放量确认信号）
        out["macd_hist_expanding"] = (hist[-1] > hist[-2] > 0 and hist[-2] <= hist[-3])

    # 供 LLM 判断金叉/斜率的多期序列（最近 bars_limit 期，各期含 close/ma5/ma20/ma60）
    out["bars"] = []
    for i in range(max(0, len(bars) - bars_limit), len(bars)):
        segc = [x["close"] for x in bars[: i + 1] if x.get("close") is not None]
        out["bars"].append(
            {
                "trade_date": bars[i]["trade_date"],
                "close": bars[i].get("close"),
                "ma5": _sma(segc, 5),
                "ma20": _sma(segc, 20),
                "ma60": _sma(segc, 60),
            }
        )
    return out


# ---------------------------------------------------------------------------
# 工具定义
# ---------------------------------------------------------------------------
class GetIndexTrend(Tool):
    """指数多周期趋势（月线定周期 / 周线定方向 / 日线定仓位）。"""

    name = "get_index_trend"
    description = (
        "查询指数（默认沪深300=000300）的周线/月线/日线趋势与指标：收盘价、5/10/20/60 均线、"
        "MA20 斜率、MA5/MA20 金叉死叉、RSI(6/12/24)、布林带上下轨。"
        "period=daily 看日线（定仓位），weekly 看周线（定方向），monthly 看月线（定周期）。"
        "用于 Mi姐等策略的「月线定周期、周线定方向、日线定仓位」三层择时与阶段高位判断。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "period": {"type": "string", "enum": ["daily", "weekly", "monthly"], "description": "周期，默认 daily"},
            "date": {"type": "string", "description": "截止日期 YYYY-MM-DD（回测必须传决策日）"},
            "code": {"type": "string", "description": "指数代码，默认 000300（沪深300）"},
        },
        "required": [],
    }

    def execute(self, period: str = "daily", date: str | None = None, code: str = "000300", **kwargs: Any) -> dict[str, Any]:
        period = period if period in ("daily", "weekly", "monthly") else "daily"
        if date is None:
            r = db.one("SELECT MAX(trade_date) AS d FROM indices")
            date = str(r["d"]) if r and r["d"] else None
        if not date:
            return {"error": "数据湖为空"}
        rows = _fetch_daily(code or "000300", date, "index", 1250)
        block = _ta_block(rows, period, with_macd=False, bars_limit=8)
        block["code"] = code or "000300"
        return block


class GetStockTA(Tool):
    """个股技术指标（MACD / RSI / 布林 / 多周期均线）。"""

    name = "get_stock_ta"
    description = (
        "查询个股的日线/周线/月线技术指标：收盘价、5/10/20/60 均线、MA5/MA20 金叉死叉、"
        "MACD（DIF/DEA/红柱、零轴上金叉、红柱放大）、RSI(6/12/24)、布林带上下轨。"
        "用于判断买点（MACD零轴上金叉+放量）与止损/止盈（MA5/MA20、MACD顶背离、RSI超买）。"
        "period=weekly 可看周线 MA20 生命线。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "6位股票代码"},
            "period": {"type": "string", "enum": ["daily", "weekly", "monthly"], "description": "周期，默认 daily"},
            "date": {"type": "string", "description": "截止日期 YYYY-MM-DD（回测必须传决策日）"},
        },
        "required": ["code"],
    }

    def execute(self, code: str, period: str = "daily", date: str | None = None, **kwargs: Any) -> dict[str, Any]:
        period = period if period in ("daily", "weekly", "monthly") else "daily"
        if date is None:
            r = db.one("SELECT MAX(trade_date) AS d FROM daily")
            date = str(r["d"]) if r and r["d"] else None
        if not date:
            return {"code": code, "error": "数据湖为空"}
        rows = _fetch_daily(code, date, "stock", 750)
        if not rows:
            return {"code": code, "error": "无数据"}
        block = _ta_block(rows, period, with_macd=True, bars_limit=6)
        block["code"] = code
        return block


class RankFloatMktcap(Tool):
    """流通市值排名（市值门槛 / 板块内市值排名）。"""

    name = "rank_float_mktcap"
    description = (
        "按流通市值对全市场股票排名（某交易日），可设最小流通市值门槛（元）。"
        "返回 code/name/close/float_mktcap/industry/rank/industry_rank。"
        "用于「板块内市值前5 / 流通市值≥200亿」这类龙头市值条件。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "交易日 YYYY-MM-DD（回测必须传决策日）"},
            "min_mktcap": {"type": "number", "description": "最小流通市值（元），如 200亿=20000000000，可选"},
            "top_n": {"type": "integer", "description": "返回前 N 只，默认30，最大100"},
        },
        "required": [],
    }

    def execute(self, date: str | None = None, min_mktcap: float | None = None, top_n: int = 30, **kwargs: Any) -> dict[str, Any]:
        if date is None:
            r = db.one("SELECT MAX(trade_date) AS d FROM daily")
            date = str(r["d"]) if r and r["d"] else None
        top_n = min(max(int(top_n), 1), 100)
        conds = ["d.trade_date = ?", "d.float_mktcap IS NOT NULL"]
        params: list[Any] = [date]
        if min_mktcap is not None:
            conds.append("d.float_mktcap >= ?")
            params.append(float(min_mktcap))
        rows = db.rows(
            f"""
            SELECT d.code, s.name, d.close, d.float_mktcap, s.industry,
                   ROW_NUMBER() OVER (ORDER BY d.float_mktcap DESC) AS rank,
                   ROW_NUMBER() OVER (PARTITION BY s.industry ORDER BY d.float_mktcap DESC) AS industry_rank
            FROM daily d LEFT JOIN stocks s ON s.code = d.code
            WHERE {' AND '.join(conds)}
            ORDER BY d.float_mktcap DESC LIMIT ?
            """,
            params + [top_n],
        )
        for r in rows:
            if r.get("float_mktcap") is not None:
                r["float_mktcap_yi"] = round(r["float_mktcap"] / 1e8, 2)
        return {"rows": rows, "count": len(rows), "date": date}


class GetIndustryPerformance(Tool):
    """行业/板块涨幅排名（主线识别）。"""

    name = "get_industry_performance"
    description = (
        "按行业（stocks.industry）聚合成员股，计算近 N 个交易日行业平均累计涨幅并排名，"
        "同时给出近 N 日行业成交额、占全市场比例及与前一 N 日的环比。"
        "用于「近20日板块涨幅排名前10、板块成交额占比环比上升」的主线识别。"
        "注意：行业字段目前约覆盖半数股票，结果反映已标注行业的相对强弱。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "截止日期 YYYY-MM-DD（回测必须传决策日）"},
            "days": {"type": "integer", "description": "统计窗口交易日数，默认20"},
            "top_n": {"type": "integer", "description": "返回涨幅排名前 N 的行业，默认20，最大50"},
        },
        "required": [],
    }

    def execute(self, date: str | None = None, days: int = 20, top_n: int = 20, **kwargs: Any) -> dict[str, Any]:
        if date is None:
            r = db.one("SELECT MAX(trade_date) AS d FROM daily")
            date = str(r["d"]) if r and r["d"] else None
        days = min(max(int(days), 3), 120)
        top_n = min(max(int(top_n), 1), 50)

        # 近 days 与 前 days 的窗口结束日
        dates = [r["trade_date"] for r in db.rows(
            "SELECT DISTINCT trade_date FROM daily WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT ?",
            [date, days * 2 + 1],
        )]
        if not dates:
            return {"error": "无数据", "date": date}
        t0 = dates[min(days, len(dates) - 1)]          # days 个交易日前
        t1 = dates[min(days * 2, len(dates) - 1)]      # 2*days 个交易日前

        # 各股票在 date 与 t0 的收盘价（用于累计涨幅）
        closes = {}
        for d in (date, t0):
            for r in db.rows(
                "SELECT code, close, amount FROM daily WHERE trade_date = ?", [d]
            ):
                closes[(r["code"], d)] = r

        ind = {r["code"]: r["industry"] for r in db.rows(
            "SELECT code, industry FROM stocks WHERE industry IS NOT NULL AND industry != ''"
        )}

        agg: dict[str, dict[str, Any]] = {}
        for code, industry in ind.items():
            cur = closes.get((code, date))
            prev = closes.get((code, t0))
            if not cur or not prev or not cur.get("close") or not prev.get("close"):
                continue
            ret = (cur["close"] / prev["close"] - 1) * 100
            g = agg.setdefault(industry, {"industry": industry, "ret_sum": 0.0, "n": 0, "amount": 0.0})
            g["ret_sum"] += ret
            g["n"] += 1
            g["amount"] += cur.get("amount") or 0

        total_amount = sum(g["amount"] for g in agg.values()) or 1
        # 前一窗口成交额（用于占比环比）
        for code, industry in ind.items():
            cur = closes.get((code, t0))
            prev = closes.get((code, t1))
            if cur and prev and cur.get("amount") is not None:
                agg.get(industry, {}).setdefault("prev_amount", 0.0)
                agg[industry]["prev_amount"] = agg[industry].get("prev_amount", 0.0) + (cur["amount"] or 0)

        rows = []
        for g in agg.values():
            if g["n"] < 2:
                continue
            avg_ret = round(g["ret_sum"] / g["n"], 2)
            amt = g["amount"]
            share = round(amt / total_amount * 100, 2)
            prev_amt = g.get("prev_amount") or 0
            share_change = round((amt / (prev_amt or 1) - 1) * 100, 2) if prev_amt else None
            rows.append(
                {
                    "industry": g["industry"],
                    "avg_return_pct": avg_ret,
                    "member_count": g["n"],
                    "amount_yi": round(amt / 1e8, 2),
                    "amount_share_pct": share,
                    "amount_share_change_pct": share_change,
                }
            )
        rows.sort(key=lambda x: x["avg_return_pct"], reverse=True)
        return {"rows": rows[:top_n], "count": len(rows), "date": date, "days": days}
