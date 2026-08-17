"""回测运行器：封装「策略 + LLM Agent + 引擎」的完整回测流程，供脚本与 API 复用。"""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..agent.strategy_agent import LLMStrategyAgent
from ..core import config
from ..llm.gateway import LLMGateway
from ..tools.default import default_registry
from .engine import BacktestEngine
from .policy import Policy

RESULT_DIR = config.DATA_DIR / "backtest"


def run_backtest(
    strategy: str,
    start: str,
    end: str,
    decide_every: int = 5,
    stop_loss: float | None = None,
    initial_cash: float = 1_000_000,
    strategy_name: str = "",
    progress_cb=None,
    stop_check=None,
    config: dict | None = None,
) -> dict:
    # 策略结构化配置 → Policy；config 为空时产出 system 预设（等价于改造前行为）。
    # legacy 的 stop_loss / decide_every 作为无 config 字段时的兜底。
    policy = Policy.from_config(config, legacy_stop_loss=stop_loss, legacy_decide_every=decide_every)

    gateway = LLMGateway()
    agent = LLMStrategyAgent(
        gateway, default_registry, strategy,
        timing_mode=policy.timing_mode, max_rounds=policy.max_rounds,
    )
    engine = BacktestEngine(initial_cash=initial_cash)

    t0 = time.time()
    result = engine.run(
        agent.decide, start, end, policy=policy,
        progress_cb=progress_cb, stop_check=stop_check,
    )
    elapsed = time.time() - t0

    payload = {
        "strategy": strategy,
        "strategy_name": strategy_name,
        "params": {
            "start": start,
            "end": end,
            "decide_every": decide_every,
            "stop_loss": stop_loss,
            "initial_cash": initial_cash,
            "strategy_name": strategy_name,
        },
        "effective_config": policy.to_dict(),
        "metrics": result["metrics"],
        "equity_curve": result["equity_curve"],
        "benchmark_curve": result.get("benchmark_curve"),
        "trades": result["trades"],
        "decision_log": result["decision_log"],
        "final_positions": result.get("final_positions"),
        "elapsed_sec": round(elapsed, 1),
    }
    return payload


def save_result(payload: dict) -> Path:
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    start = payload["params"]["start"]
    end = payload["params"]["end"]
    out = RESULT_DIR / f"backtest_{start}_{end}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    return out


def list_results() -> list[dict]:
    if not RESULT_DIR.exists():
        return []
    results = []
    for f in sorted(RESULT_DIR.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            results.append(
                {
                    "file": f.name,
                    "params": d.get("params"),
                    "metrics": d.get("metrics"),
                    "elapsed_sec": d.get("elapsed_sec"),
                }
            )
        except Exception:  # noqa: BLE001
            continue
    return results


def _reconstruct_holdings(trades: list[dict]) -> dict[str, tuple[int, float]]:
    """从成交记录重放持仓（沿用撮合的加权平均成本法），返回 code -> (数量, 平均成本)。"""
    pos: dict[str, list[float]] = {}  # code -> [qty, avg_cost]
    for t in trades:
        action = t.get("action")
        if action not in ("buy", "sell"):
            continue
        code = str(t.get("code") or "")
        if not code:
            continue
        qty = int(t.get("quantity", 0) or 0)
        if qty <= 0:
            continue
        if action == "buy":
            amount = float(t.get("amount", 0) or 0)
            cur = pos.get(code)
            if cur is None:
                pos[code] = [float(qty), amount / qty]
            else:
                total_cost = cur[1] * cur[0] + amount
                new_qty = cur[0] + qty
                cur[0] = new_qty
                cur[1] = total_cost / new_qty if new_qty else 0.0
        else:  # 卖出：平均成本不变，仅减数量
            cur = pos.get(code)
            if cur is not None:
                cur[0] -= qty
                if cur[0] <= 0:
                    del pos[code]
    return {c: (int(v[0]), v[1]) for c, v in pos.items() if v[0] > 0}


def _backfill_final_positions(d: dict) -> None:
    """旧结果文件没有 final_positions 时，用结束日收盘价重建期末持仓快照。

    持仓中个股的浮动盈亏 = (期末价 - 平均成本) × 剩余数量，即「按结束时间价格与
    买入价格计算盈亏」。数据湖不可用等异常时降级为空列表，不影响结果读取。
    """
    if d.get("final_positions") is not None:
        return
    trades = d.get("trades") or []
    holdings = _reconstruct_holdings(trades)
    if not holdings:
        d["final_positions"] = []
        return
    end = (d.get("params") or {}).get("end")
    if not end:
        d["final_positions"] = []
        return
    try:
        from ..data import lake

        conn = lake.get_connection(config.DB_PATH, read_only=True)
        try:
            fp = []
            for code, (qty, avg_cost) in holdings.items():
                row = conn.execute(
                    "SELECT close FROM daily WHERE code = ? AND trade_date <= ? "
                    "ORDER BY trade_date DESC LIMIT 1",
                    [code, end],
                ).fetchone()
                px = float(row[0]) if row and row[0] is not None else avg_cost
                fp.append(
                    {
                        "code": code,
                        "quantity": qty,
                        "avg_cost": round(avg_cost, 2),
                        "price": round(px, 2),
                        "pnl_pct": round((px / avg_cost - 1) * 100, 2) if avg_cost else 0.0,
                        "market_value": round(qty * px, 2),
                    }
                )
            d["final_positions"] = fp
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        d["final_positions"] = []


def load_result(filename: str) -> dict | None:
    p = RESULT_DIR / filename
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    _backfill_final_positions(d)
    return d
