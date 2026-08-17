"""绩效指标：总收益、年化、最大回撤、夏普、胜率。"""
from __future__ import annotations

import math
import statistics


def compute_metrics(
    equity_curve: list[dict],
    benchmark_curve: list[dict] | None = None,
    trades: list[dict] | None = None,
    risk_free: float = 0.02,
    periods_per_year: int = 252,
) -> dict:
    """equity_curve / benchmark_curve: [{date, total}]（按日期升序）。
    trades: 成交记录列表（含 sell 的 pnl 字段），用于计算交易胜率。"""
    if not equity_curve or len(equity_curve) < 2:
        return {"error": "权益曲线不足"}

    totals = [float(e["total"]) for e in equity_curve]
    initial = totals[0]
    final = totals[-1]
    total_return = final / initial - 1

    n = len(totals)
    years = n / periods_per_year
    annual_return = (1 + total_return) ** (1 / years) - 1 if years > 0 and total_return > -1 else -1.0

    daily_returns = [totals[i] / totals[i - 1] - 1 for i in range(1, n)]

    # 最大回撤
    peak = totals[0]
    max_dd = 0.0
    max_dd_date = equity_curve[0]["date"]
    peak_date = equity_curve[0]["date"]
    for e in equity_curve:
        t = float(e["total"])
        if t > peak:
            peak = t
            peak_date = e["date"]
        dd = t / peak - 1
        if dd < max_dd:
            max_dd = dd
            max_dd_date = e["date"]

    # 夏普
    mean_daily = statistics.fmean(daily_returns) if daily_returns else 0.0
    std_daily = statistics.stdev(daily_returns) if len(daily_returns) > 1 else 0.0
    sharpe = (mean_daily * periods_per_year - risk_free) / (std_daily * math.sqrt(periods_per_year)) if std_daily > 0 else 0.0

    # 按日胜率（日收益>0 的天数占比）、盈亏比
    win_days = sum(1 for r in daily_returns if r > 0)
    loss_days = sum(1 for r in daily_returns if r < 0)
    daily_win_rate = win_days / len(daily_returns) if daily_returns else 0.0
    avg_win = statistics.fmean([r for r in daily_returns if r > 0]) if win_days else 0.0
    avg_loss = statistics.fmean([r for r in daily_returns if r < 0]) if loss_days else 0.0

    result: dict = {
        "total_return": round(total_return, 4),
        "annual_return": round(annual_return, 4),
        "max_drawdown": round(max_dd, 4),
        "max_dd_date": max_dd_date,
        "peak_date": peak_date,
        "sharpe": round(sharpe, 3),
        "daily_win_rate": round(daily_win_rate, 4),
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "days": n,
        "years": round(years, 2),
        "final_value": round(final, 2),
        "initial_value": round(initial, 2),
    }

    # 交易胜率：按卖出平仓的已实现盈亏（pnl>0 视为盈利单）。
    # 看板"胜率"字段展示此值；无成交记录时回退到按日胜率。
    if trades:
        sells = [t for t in trades if t.get("action") == "sell"]
        wins = sum(1 for t in sells if (t.get("pnl") or 0.0) > 0)
        losses = sum(1 for t in sells if (t.get("pnl") or 0.0) < 0)
        closed = wins + losses
        result["trade_count"] = closed
        result["trade_wins"] = wins
        result["trade_losses"] = losses
        result["win_rate"] = round(wins / closed, 4) if closed else 0.0
    else:
        result["win_rate"] = result["daily_win_rate"]

    # 基准对比（超额收益）
    if benchmark_curve:
        b = {e["date"]: float(e["total"]) for e in benchmark_curve}
        # 用权益日期对齐基准
        aligned = []
        for e in equity_curve:
            if e["date"] in b:
                aligned.append(b[e["date"]])
        if aligned:
            b_ret = aligned[-1] / aligned[0] - 1
            result["benchmark_return"] = round(b_ret, 4)
            result["excess_return"] = round(total_return - b_ret, 4)

    return result
