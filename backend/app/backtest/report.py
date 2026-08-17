"""回测结果报告生成：结构化统计 + LLM 智能总结。"""
from __future__ import annotations

import json
import time
from typing import Any

from ..llm.gateway import LLMGateway


def _load_names() -> dict[str, str]:
    """股票代码 → 名称映射（用于报告展示）。"""
    try:
        from ..core import config
        from ..data import lake

        conn = lake.get_connection(config.DB_PATH, read_only=True)
        try:
            rows = conn.execute(
                "SELECT code, name FROM stocks WHERE name IS NOT NULL AND name != ''"
            ).fetchall()
            return {r[0]: r[1] for r in rows}
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return {}


def build_report(result: dict) -> dict:
    """基于回测结果生成结构化报告（纯规则统计）。"""
    metrics = result.get("metrics", {})
    params = result.get("params", {})
    trades = result.get("trades", [])
    decision_log = result.get("decision_log", [])
    equity_curve = result.get("equity_curve", [])
    names = _load_names()

    buys = [t for t in trades if t.get("action") == "buy"]
    sells = [t for t in trades if t.get("action") == "sell"]

    # 市场状态分布
    states: dict[str, int] = {}
    for d in decision_log:
        s = d.get("market_state", "unknown")
        states[s] = states.get(s, 0) + 1

    # 交易标的
    symbols = sorted({str(t.get("code")) for t in trades if t.get("code")})

    # 个股交易汇总（按 code 聚合）
    stock_map: dict[str, dict] = {}
    for t in trades:
        if t.get("action") not in ("buy", "sell"):
            continue
        code = str(t.get("code"))
        if not code:
            continue
        s = stock_map.setdefault(
            code,
            {
                "code": code,
                "buy_count": 0,
                "sell_count": 0,
                "buy_qty": 0,
                "sell_qty": 0,
                "buy_amount": 0.0,
                "sell_amount": 0.0,
                "realized_pnl": 0.0,
                "sold_cost": 0.0,
                "has_pnl": False,
            },
        )
        if t.get("action") == "buy":
            s["buy_count"] += 1
            s["buy_qty"] += int(t.get("quantity", 0) or 0)
            s["buy_amount"] += float(t.get("amount", 0) or 0)
        else:
            s["sell_count"] += 1
            s["sell_qty"] += int(t.get("quantity", 0) or 0)
            s["sell_amount"] += float(t.get("amount", 0) or 0)
            # 已实现盈亏以撮合时计算的 pnl 为准；卖出净额 - pnl = 卖出部分的成本。
            # 旧版结果文件卖出记录可能没有 pnl 字段，此时回退到「卖出额-买入额」近似。
            if "pnl" in t and t.get("pnl") is not None:
                pnl_val = float(t["pnl"])
                s["realized_pnl"] += pnl_val
                s["sold_cost"] += float(t.get("amount", 0) or 0) - pnl_val
                s["has_pnl"] = True

    # 期末持仓市值（用于「持仓中」个股的浮动盈亏与综合收益率）
    final_map = {str(p.get("code")): p for p in (result.get("final_positions") or [])}

    stock_summary = []
    for code, s in stock_map.items():
        holding = s["buy_qty"] > s["sell_qty"]
        # 已实现盈亏：优先用撮合时逐笔记录的 pnl；旧结果文件缺 pnl 时用「卖出额 - 买入额」近似
        if s["has_pnl"]:
            realized = s["realized_pnl"]
            sold_cost = s["sold_cost"]
        elif s["sell_qty"] > 0:
            realized = s["sell_amount"] - s["buy_amount"]
            sold_cost = s["buy_amount"]
        else:
            realized = 0.0
            sold_cost = 0.0

        pnl = realized
        if holding:
            # 剩余持仓成本 = 总买入额 - 已卖出部分成本；浮动盈亏 = 期末市值 - 剩余成本。
            # 无期末市值数据（旧结果）时按剩余成本计，浮动盈亏为 0，避免误判成全额亏损。
            remaining_cost = s["buy_amount"] - sold_cost
            fp = final_map.get(code)
            market_value = float(fp["market_value"]) if fp and fp.get("market_value") is not None else remaining_cost
            pnl = realized + (market_value - remaining_cost)

        stock_summary.append(
            {
                "code": code,
                "name": names.get(code, ""),
                "buy_count": s["buy_count"],
                "sell_count": s["sell_count"],
                "buy_amount": round(s["buy_amount"], 0),
                "sell_amount": round(s["sell_amount"], 0),
                "pnl": round(pnl, 0),
                "return_pct": round(pnl / s["buy_amount"] * 100, 1) if s["buy_amount"] > 0 else 0.0,
                "holding": holding,
            }
        )
    stock_summary.sort(key=lambda x: x["pnl"], reverse=True)

    # 月度收益
    month_map: dict[str, dict] = {}
    for e in equity_curve:
        month = str(e.get("date", ""))[:7]
        if not month:
            continue
        total = e.get("total")
        if month not in month_map:
            month_map[month] = {"month": month, "start": total, "end": total}
        month_map[month]["end"] = total
    monthly_returns = []
    for month, v in month_map.items():
        if v.get("start") and v["start"] > 0 and v.get("end") is not None:
            monthly_returns.append({"month": month, "ret": round((v["end"] / v["start"] - 1) * 100, 2)})

    # 决策摘要列表
    decision_summaries = [
        {"date": d.get("date"), "market_state": d.get("market_state"), "summary": d.get("summary", "")}
        for d in decision_log
        if d.get("summary")
    ]

    # 最佳/最差交易（按个股 pnl）
    best = stock_summary[0] if stock_summary else None
    worst = stock_summary[-1] if stock_summary else None

    return {
        "title": "A股策略回测结果报告",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "params": params,
        "strategy_name": result.get("strategy_name", ""),
        "strategy": result.get("strategy", ""),
        "metrics": metrics,
        "trade_stats": {
            "total_records": len(trades),
            "buys": len(buys),
            "sells": len(sells),
            "symbols_traded": len(symbols),
            "symbols": symbols,
            "stock_summary": stock_summary,
            "best_stock": best,
            "worst_stock": worst,
        },
        "decision_stats": {
            "total_decisions": len(decision_log),
            "market_states": states,
            "decision_summaries": decision_summaries,
        },
        "monthly_returns": monthly_returns,
    }


def llm_summary(result: dict, role: str = "report") -> str:
    """用 LLM 生成报告总结与改进建议。"""
    gateway = LLMGateway()
    report = build_report(result)
    metrics = report["metrics"]
    ts = report["trade_stats"]
    ds = report["decision_stats"]
    monthly = report["monthly_returns"]

    prompt = (
        "请根据以下 A 股策略回测结果，写一份中文总结（400 字以内），包含四部分：\n"
        "1. 策略整体表现评价（总收益、年化、最大回撤、夏普、胜率）；\n"
        "2. 主要盈亏来源（结合个股汇总，哪些票贡献了盈利/亏损）；\n"
        "3. 月度收益特征（哪几个月表现好/差）；\n"
        "4. 2~3 条改进建议。\n\n"
        f"回测指标：{json.dumps(metrics, ensure_ascii=False)}\n"
        f"个股交易汇总：{json.dumps(ts.get('stock_summary', [])[:20], ensure_ascii=False)}\n"
        f"月度收益：{json.dumps(monthly, ensure_ascii=False)}\n"
        f"市场状态分布：{json.dumps(ds.get('market_states', {}), ensure_ascii=False)}\n"
    )
    resp = gateway.chat(
        [{"role": "user", "content": prompt}],
        max_tokens=1000,
        role=role,
        temperature=0.3,
    )
    return resp["choices"][0]["message"]["content"]


def to_markdown(report: dict) -> str:
    """把结构化报告渲染为 Markdown 文本（供下载）。"""
    m = report.get("metrics", {})
    p = report.get("params", {})
    ts = report.get("trade_stats", {})
    ds = report.get("decision_stats", {})
    monthly = report.get("monthly_returns", [])

    lines = [
        f"# {report.get('title', '回测结果报告')}",
        "",
        f"生成时间：{report.get('generated_at', '')}",
        "",
        "## 一、回测参数",
        f"- 区间：{p.get('start')} ~ {p.get('end')}",
        f"- 决策间隔：{p.get('decide_every')} 天",
        f"- 止损：{p.get('stop_loss')}",
        f"- 初始资金：{p.get('initial_cash')}",
        "",
        "## 二、策略内容",
        f"策略名称：{report.get('strategy_name', '-')}",
        "",
        report.get("strategy", ""),
        "",
        "## 三、绩效指标",
        f"- 总收益：{_pct(m.get('total_return'))}",
        f"- 年化收益：{_pct(m.get('annual_return'))}",
        f"- 最大回撤：{_pct(m.get('max_drawdown'))}（发生于 {m.get('max_dd_date', '-')}）",
        f"- 夏普比率：{m.get('sharpe')}",
        f"- 胜率：{_pct(m.get('win_rate'))}",
        f"- 最终资产：{m.get('final_value')}（初始 {m.get('initial_value')}）",
        "",
        "## 四、交易统计",
        f"- 成交记录：{ts.get('total_records')} 笔（买入 {ts.get('buys')} / 卖出 {ts.get('sells')}）",
        f"- 交易标的：{ts.get('symbols_traded')} 只",
        f"- 盈利最多：{_stock_line(ts.get('best_stock'))}",
        f"- 亏损最多：{_stock_line(ts.get('worst_stock'))}",
        "",
        "### 个股交易汇总",
    ]
    lines.append("| 股票 | 买入 | 卖出 | 总买入额 | 总卖出额 | 净盈亏 | 收益率 | 状态 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for s in ts.get("stock_summary", []):
        name = s.get("name", "")
        lines.append(
            f"| {s['code']} {name} | {s['buy_count']} | {s['sell_count']} | {_amt(s['buy_amount'])} | {_amt(s['sell_amount'])} "
            f"| {_amt(s['pnl'])} | {_pct_raw(s['return_pct'])} | {'持仓中' if s['holding'] else '已清仓'} |"
        )

    lines += [
        "",
        "## 五、决策统计",
        f"- 决策次数：{ds.get('total_decisions')}",
        f"- 市场状态分布：{ds.get('market_states')}",
        "",
    ]

    if ds.get("decision_summaries"):
        lines.append("### 决策摘要")
        lines.append("")
        for d in ds["decision_summaries"]:
            lines.append(f"- **{d['date']}（{d['market_state']}）**：{d['summary']}")
        lines.append("")

    lines.append("## 六、月度收益")
    lines.append("")
    lines.append("| 月份 | 收益率 |")
    lines.append("|---|---|")
    for r in monthly:
        lines.append(f"| {r['month']} | {r['ret']}% |")
    lines += [
        "",
        "## 七、总结",
        report.get("summary", ""),
    ]
    return "\n".join(lines)


def _stock_line(s: dict | None) -> str:
    if not s:
        return "-"
    name = s.get("name", "")
    return f"{s['code']} {name}（净盈亏 {_amt(s['pnl'])}，收益率 {_pct_raw(s['return_pct'])}）"


def _amt(v: Any) -> str:
    if v is None:
        return "-"
    return f"{float(v):,.0f}"


def _pct_raw(v: Any) -> str:
    if v is None:
        return "-"
    return f"{float(v):.1f}%"


def _pct(v: Any) -> str:
    if v is None:
        return "-"
    return f"{float(v) * 100:.2f}%"
