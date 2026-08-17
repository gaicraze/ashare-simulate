"""个股深度分析：从本地数据湖采集多维数据，交给 LLM 生成结构化研报。

与回测执行（agent 逐日决策）不同，这里是「当下时点」的一次性深度分析，
数据取最新交易日，不涉及未来数据泄漏问题。
"""
from __future__ import annotations

import json
from typing import Any

from ..core import config
from ..data import lake, query
from ..llm.gateway import LLMGateway
from ..tools import db


def _f(v: Any, nd: int = 2) -> float | None:
    """把数值四舍五入到 nd 位小数，None 原样返回。"""
    if v is None:
        return None
    try:
        return round(float(v), nd)
    except (TypeError, ValueError):
        return None


def _fmt_yi(v: Any) -> float | None:
    """元 → 亿元，保留 4 位小数。"""
    if v is None:
        return None
    try:
        return round(float(v) / 1e8, 4)
    except (TypeError, ValueError):
        return None


def resolve_stock(q: str) -> dict | None:
    """把代码或名称解析为一只股票。"""
    q = (q or "").strip()
    if not q:
        return None
    if q.isdigit() and len(q) == 6:
        row = db.one("SELECT code, name, industry, list_date, status FROM stocks WHERE code = ?", [q])
        if row:
            return row
    # 按名称子串 / 代码前缀搜索，取最匹配
    rows = query.search_stocks(config.DB_PATH, q, limit=5)
    if not rows:
        return None
    # 名称完全匹配优先
    for r in rows:
        if (r.get("name") or "").replace(" ", "") == q.replace(" ", ""):
            return db.one(
                "SELECT code, name, industry, list_date, status FROM stocks WHERE code = ?",
                [r["code"]],
            )
    return db.one(
        "SELECT code, name, industry, list_date, status FROM stocks WHERE code = ?",
        [rows[0]["code"]],
    )


def _collect_quote(code: str) -> tuple[dict, str | None]:
    """最新一根日线（含估值），返回 (quote, latest_date)。"""
    row = db.one(
        """
        SELECT trade_date, open, high, low, close, volume, amount,
               pct_change, turnover, float_mktcap, pe_ttm, pb_mrq
        FROM daily WHERE code = ? ORDER BY trade_date DESC LIMIT 1
        """,
        [code],
    )
    if not row:
        return {}, None
    latest_date = str(row["trade_date"])
    # 最新一根 PE/PB 可能为空，回退取最近非空估值
    pe, pb = row["pe_ttm"], row["pb_mrq"]
    if pe is None or pb is None:
        val = db.one(
            """
            SELECT pe_ttm, pb_mrq FROM daily
            WHERE code = ? AND trade_date <= ? AND (pe_ttm IS NOT NULL OR pb_mrq IS NOT NULL)
            ORDER BY trade_date DESC LIMIT 1
            """,
            [code, latest_date],
        )
        if val:
            pe = pe if pe is not None else val["pe_ttm"]
            pb = pb if pb is not None else val["pb_mrq"]
    quote = {
        "trade_date": latest_date,
        "open": _f(row["open"]),
        "high": _f(row["high"]),
        "low": _f(row["low"]),
        "close": _f(row["close"]),
        "pct_change": _f(row["pct_change"]),
        "volume": int(row["volume"]) if row["volume"] is not None else None,
        "amount_yi": _fmt_yi(row["amount"]),
        "turnover": _f(row["turnover"]),
        "float_mktcap_yi": _fmt_yi(row["float_mktcap"]),
        "pe_ttm": _f(pe),
        "pb_mrq": _f(pb),
    }
    return quote, latest_date


def _collect_daily_series(code: str, limit: int = 250) -> list[dict]:
    """取近 limit 根日线（升序），用于技术指标计算与前端绘图。"""
    rows = db.rows(
        """
        SELECT * FROM (
            SELECT trade_date, open, high, low, close, volume, amount, pct_change, turnover
            FROM daily WHERE code = ? ORDER BY trade_date DESC LIMIT ?
        ) ORDER BY trade_date ASC
        """,
        [code, limit],
    )
    return rows


def _ema(values: list[float], n: int) -> list[float]:
    """指数移动平均序列（首值用 SMA 初始化）。"""
    if not values:
        return []
    k = 2 / (n + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _technical(rows: list[dict]) -> dict:
    if not rows:
        return {}
    closes = [r["close"] for r in rows if r["close"] is not None]
    volumes = [r["volume"] or 0 for r in rows]
    last = rows[-1]
    close = closes[-1]

    def ma(n: int) -> float | None:
        if len(closes) < n:
            return None
        return _f(sum(closes[-n:]) / n)

    def pct_over(n: int) -> float | None:
        if len(closes) < n + 1:
            return None
        return _f((closes[-1] / closes[-n - 1] - 1) * 100)

    # RSI(14) — Wilder 平滑
    rsi = None
    if len(closes) >= 15:
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[:14]) / 14
        avg_loss = sum(losses[:14]) / 14
        for i in range(14, len(gains)):
            avg_gain = (avg_gain * 13 + gains[i]) / 14
            avg_loss = (avg_loss * 13 + losses[i]) / 14
        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = _f(100 - 100 / (1 + rs))

    # MACD(12,26,9)
    macd = None
    if len(closes) >= 35:
        e12 = _ema(closes, 12)
        e26 = _ema(closes, 26)
        dif = [a - b for a, b in zip(e12, e26)]
        dea = _ema(dif, 9)
        macd = {
            "dif": _f(dif[-1]),
            "dea": _f(dea[-1]),
            "hist": _f(2 * (dif[-1] - dea[-1])),
        }

    # 52 周高低（近 250 根）
    high_250 = max(closes)
    low_250 = min(closes)
    ma20 = ma(20)
    vol_ratio = None
    if len(volumes) >= 21 and sum(volumes[-21:-1]) > 0:
        vol_ratio = _f(volumes[-1] / (sum(volumes[-21:-1]) / 20))

    return {
        "date": str(last["trade_date"]),
        "close": _f(close),
        "ma5": ma(5),
        "ma10": ma(10),
        "ma20": ma20,
        "ma60": ma(60),
        "ma120": ma(120),
        "close_vs_ma20_pct": _f((close / ma20 - 1) * 100) if ma20 else None,
        "pct_5d": pct_over(5),
        "pct_20d": pct_over(20),
        "pct_60d": pct_over(60),
        "pct_120d": pct_over(120),
        "rsi14": rsi,
        "macd": macd,
        "high_250": _f(high_250),
        "low_250": _f(low_250),
        "distance_from_high_pct": _f((close / high_250 - 1) * 100) if high_250 else None,
        "volume_ratio": vol_ratio,
    }


def _collect_fundamentals(code: str) -> list[dict]:
    rows = db.rows(
        """
        SELECT report_date, revenue, net_profit, roe, gross_margin,
               net_profit_margin, eps_ttm, yoy_net_profit, yoy_eps
        FROM finances WHERE code = ? ORDER BY report_date DESC LIMIT 6
        """,
        [code],
    )
    out = []
    for r in rows:
        out.append(
            {
                "report_date": str(r["report_date"]),
                "revenue_yi": _fmt_yi(r["revenue"]),
                "net_profit_yi": _fmt_yi(r["net_profit"]),
                "roe_pct": _f(r["roe"] * 100) if r["roe"] is not None else None,
                "gross_margin_pct": _f(r["gross_margin"] * 100) if r["gross_margin"] is not None else None,
                "net_margin_pct": _f(r["net_profit_margin"] * 100) if r["net_profit_margin"] is not None else None,
                "eps_ttm": _f(r["eps_ttm"]),
                "yoy_net_profit_pct": _f(r["yoy_net_profit"] * 100) if r["yoy_net_profit"] is not None else None,
                "yoy_eps_pct": _f(r["yoy_eps"] * 100) if r["yoy_eps"] is not None else None,
            }
        )
    return out


def _collect_moneyflow(code: str, latest_date: str | None) -> dict:
    if not latest_date:
        return {}
    rows = db.rows(
        """
        SELECT trade_date, main_net_inflow, super_net_inflow, large_net_inflow
        FROM moneyflow WHERE code = ? AND trade_date <= ? ORDER BY trade_date DESC LIMIT 20
        """,
        [code, latest_date],
    )
    if not rows:
        return {"has_data": False}
    main_sum = sum((r["main_net_inflow"] or 0) for r in rows)
    super_sum = sum((r["super_net_inflow"] or 0) for r in rows)
    large_sum = sum((r["large_net_inflow"] or 0) for r in rows)
    inflow_days = sum(1 for r in rows if (r["main_net_inflow"] or 0) > 0)
    return {
        "has_data": True,
        "days": len(rows),
        "main_net_inflow_sum_yi": _f(main_sum / 1e8, 4),
        "super_net_inflow_sum_yi": _f(super_sum / 1e8, 4),
        "large_net_inflow_sum_yi": _f(large_sum / 1e8, 4),
        "main_inflow_days": inflow_days,
        "latest_rows": [
            {
                "trade_date": str(r["trade_date"]),
                "main_net_inflow_yi": _fmt_yi(r["main_net_inflow"]),
                "super_net_inflow_yi": _fmt_yi(r["super_net_inflow"]),
                "large_net_inflow_yi": _fmt_yi(r["large_net_inflow"]),
            }
            for r in rows[:5]
        ],
    }


def _collect_sectors(code: str) -> list[str]:
    rows = db.rows(
        """
        SELECT DISTINCT sector FROM sectors WHERE code = ? AND sector IS NOT NULL AND sector != ''
        ORDER BY sector
        """,
        [code],
    )
    return [r["sector"] for r in rows]


def _collect_market_regime(latest_date: str | None) -> dict:
    if not latest_date:
        return {}
    row = db.one(
        """
        SELECT trade_date, close,
          AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS ma20,
          AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) AS ma60,
          AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 119 PRECEDING AND CURRENT ROW) AS ma120
        FROM indices WHERE code = '000300' AND trade_date <= ?
        ORDER BY trade_date DESC LIMIT 1
        """,
        [latest_date],
    )
    if not row or row["close"] is None:
        return {}
    close, ma20, ma60, ma120 = row["close"], row["ma20"], row["ma60"], row["ma120"]
    if close and ma20 and ma60 and ma120:
        if close > ma20 > ma60 > ma120:
            regime = "牛市"
        elif close < ma20 < ma60 < ma120:
            regime = "熊市"
        else:
            regime = "震荡市"
    else:
        regime = "数据不足"
    return {
        "date": str(row["trade_date"]),
        "index_close": _f(close),
        "ma20": _f(ma20),
        "ma60": _f(ma60),
        "ma120": _f(ma120),
        "regime": regime,
    }


def _collect_rps(code: str, latest_date: str | None) -> dict:
    """近 120 日涨幅在全市场的分位（RPS120）。"""
    if not latest_date:
        return {}
    try:
        rows = db.rows(
            """
            SELECT a.code, (a.close / b.close - 1) * 100 AS pct
            FROM daily a
            JOIN (
                SELECT code, close,
                       ROW_NUMBER() OVER (PARTITION BY code ORDER BY trade_date DESC) AS rn
                FROM daily WHERE trade_date <= ?
            ) b ON a.code = b.code AND b.rn = 121
            WHERE a.trade_date = ?
            """,
            [latest_date, latest_date],
        )
        if not rows:
            return {}
        total = len(rows)
        sorted_rows = sorted(rows, key=lambda r: r["pct"], reverse=True)
        for i, r in enumerate(sorted_rows):
            if r["code"] == code:
                return {"rps120": _f((1 - i / total) * 100, 1), "rank": i + 1, "total": total}
    except Exception:  # noqa: BLE001
        return {}
    return {}


def collect_stock_data(code: str) -> dict:
    """采集一只股票的全部分析所需数据。"""
    stock = db.one(
        "SELECT code, name, industry, list_date, status FROM stocks WHERE code = ?", [code]
    )
    quote, latest_date = _collect_quote(code)
    daily_rows = _collect_daily_series(code, 250)
    fundamentals = _collect_fundamentals(code)
    moneyflow = _collect_moneyflow(code, latest_date)
    sectors = _collect_sectors(code)
    market = _collect_market_regime(latest_date)
    rps = _collect_rps(code, latest_date)
    technical = _technical(daily_rows)

    notes: list[str] = []
    if not fundamentals:
        notes.append("暂无财务数据")
    if not moneyflow.get("has_data"):
        notes.append("暂无主力资金流数据")
    if not sectors:
        notes.append("暂无板块/概念数据")

    return {
        "stock": {
            "code": stock["code"] if stock else code,
            "name": stock["name"] if stock else None,
            "industry": stock["industry"] if stock else None,
            "list_date": str(stock["list_date"]) if stock and stock["list_date"] else None,
            "status": stock["status"] if stock else None,
        },
        "quote": quote,
        "technical": technical,
        "fundamentals": fundamentals,
        "moneyflow": moneyflow,
        "sectors": sectors,
        "market": market,
        "rps": rps,
        "notes": notes,
        # 供前端绘图：近 120 根收盘（升序）
        "series": [
            {"trade_date": str(r["trade_date"]), "close": _f(r["close"]), "pct_change": _f(r["pct_change"])}
            for r in daily_rows[-120:]
        ],
    }


_SYSTEM_PROMPT = """你是一名专业的A股股票分析师，擅长基本面、技术面、资金面与估值的综合分析。
你的任务是基于给定的股票数据（全部来自本地数据湖，截止最新交易日），输出一份严谨、客观、可读性强的个股深度研究报告。

写作要求：
1. 所有数据必须来自「给定数据」，严禁编造；数据缺失处明确写「数据缺失」而非臆测。
2. 结论要给出明确的研判倾向（看多/看空/中性），并说明依据，但必须克制，不夸大。
3. 使用 Markdown 格式，结构如下（二级标题用 ##）：
   ## 一、公司概况
   ## 二、基本面分析
   ## 三、技术面与资金面
   ## 四、估值分析
   ## 五、风险提示
   ## 六、综合研判
4. 全文 800-1500 字，语言精炼，多用数据说话。
5. 结尾固定一行：> 本报告由 AI 自动生成，仅供研究参考，不构成任何投资建议。"""


def build_prompt(data: dict) -> str:
    """把结构化数据序列化进提示词。"""
    stock = data["stock"]
    quote = data["quote"]
    tech = data["technical"]
    market = data["market"]
    rps = data["rps"]
    mf = data["moneyflow"]

    lines: list[str] = []
    lines.append(f"股票：{stock.get('name') or '未知'}（{stock.get('code')}）")
    if stock.get("industry"):
        lines.append(f"所属行业：{stock['industry']}")
    if stock.get("list_date"):
        lines.append(f"上市日期：{stock['list_date']}")

    lines.append("\n【最新行情】")
    lines.append(json.dumps(quote, ensure_ascii=False))

    lines.append("\n【技术面指标】")
    lines.append(json.dumps(tech, ensure_ascii=False))
    if rps:
        lines.append(f"RPS120（近120日涨幅全市场分位）：{rps}")

    lines.append("\n【近期财务（按报告期倒序，金额单位：亿元，比率单位：%）】")
    lines.append(json.dumps(data["fundamentals"], ensure_ascii=False))

    lines.append("\n【主力资金流（近20日）】")
    lines.append(json.dumps(mf, ensure_ascii=False))

    lines.append("\n【板块/概念】")
    lines.append("、".join(data["sectors"]) if data["sectors"] else "数据缺失")

    lines.append("\n【大盘环境（沪深300）】")
    lines.append(json.dumps(market, ensure_ascii=False))

    if data.get("notes"):
        lines.append("\n【数据缺失提示】")
        lines.append("；".join(data["notes"]))

    lines.append("\n请基于以上数据，输出个股深度研究报告。")
    return "\n".join(lines)


def _clean_report(content: str | None) -> str:
    """清理模型输出：剥离内联的 <think>...</think> 推理块（推理模型可能把 CoT 混进 content）。"""
    if not content:
        return ""
    text = content.strip()
    # 去掉包裹在 <think>...</think>（或 <thinking>）里的推理内容，只保留最终回答
    for tag in ("think", "thinking"):
        start = text.find(f"<{tag}>")
        if start != -1:
            end = text.find(f"</{tag}>", start)
            if end != -1:
                text = text[end + len(f"</{tag}>"):].strip()
    return text.strip()


def analyze(code: str) -> dict:
    """执行一次完整的个股深度分析，返回 {data, report, model}。"""
    data = collect_stock_data(code)
    gateway = LLMGateway()
    # 推理模型（如 deepseek-v4-pro）会把 token 花在 reasoning 上，max_tokens 需留足余量，
    # 否则 content 为空。这里给到 8000 并做内容清理，兼容 reasoning 与非 reasoning 模型。
    resp = gateway.chat(
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": build_prompt(data)},
        ],
        max_tokens=8000,
        role="analysis",
        temperature=0.4,
    )
    report = _clean_report(resp["choices"][0]["message"].get("content"))
    if not report:
        raise RuntimeError("模型返回内容为空（推理模型可能只输出了思考过程），请在「模型配置」中为「个股深度分析」选用非推理模型或重试")
    model = resp.get("model") or "unknown"
    return {"data": data, "report": report, "model": model}
