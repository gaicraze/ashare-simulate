"""引擎回归：用确定性 stub decide_fn 冻结/对比引擎输出。

不依赖 LLM，只验证「撮合 + 账户 + 逐日循环」这一层的确定性行为，
用于 Phase 1 纯重构（signals/policy 抽取）的零变化回归验证。

用法（在 backend/ 目录下执行，用 backend/.venv）：
    python ../scripts/regress_engine.py freeze     # 冻结基线
    python ../scripts/regress_engine.py compare    # 重跑并与基线逐字段对比
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.backtest.engine import BacktestEngine  # noqa: E402

# 基线放在 data/regress/ 下，避免与用户回测结果（data/backtest/*.json）混在一起被扫进任务列表
BASELINE = BACKEND_DIR / "data" / "regress" / "baseline_engine.json"

START, END = "2024-09-01", "2024-12-31"
DECIDE_EVERY = 5
INITIAL_CASH = 1_000_000

# 覆盖多条路径：反复买入（触发单只 25% 上限、总仓位上限、资金不足等），
# 以及对未持仓标的卖出（触发「无持仓」拒绝）。
BUY_CODE = "600519"
BUY_AMOUNT = 300_000
SELL_CODE = "000001"  # 永不持仓，始终被拒


def stub_decide(date: str, snapshot: dict) -> dict:
    return {
        "orders": [
            {"action": "buy", "code": BUY_CODE, "cash_amount": BUY_AMOUNT},
            {"action": "sell", "code": SELL_CODE, "ratio": 1.0},
        ],
        "trace": [{"tool": "stub", "args": {}, "summary": "baseline stub"}],
        "summary": "baseline stub",
    }


def run() -> dict:
    engine = BacktestEngine(initial_cash=INITIAL_CASH)
    result = engine.run(stub_decide, START, END, decide_every=DECIDE_EVERY)
    return {
        "params": {
            "start": START,
            "end": END,
            "decide_every": DECIDE_EVERY,
            "initial_cash": INITIAL_CASH,
        },
        "equity_curve": result["equity_curve"],
        "trades": result["trades"],
        "metrics": result["metrics"],
        "decision_log": result["decision_log"],
    }


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str, indent=2)


def freeze() -> None:
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(_dump(run()), encoding="utf-8")
    print("baseline frozen ->", BASELINE)


def compare() -> None:
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    cur = run()
    keys = ["equity_curve", "trades", "metrics", "decision_log"]
    diffs = [k for k in keys if base.get(k) != cur.get(k)]
    if diffs:
        print("DIFF FOUND in:", diffs)
        for k in diffs:
            print(f"\n===== {k} (baseline) =====")
            print(_dump(base.get(k))[:3000])
            print(f"\n===== {k} (current) =====")
            print(_dump(cur.get(k))[:3000])
        sys.exit(1)
    print("OK: equity_curve / trades / metrics / decision_log 与基线一致")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "compare"
    {"freeze": freeze, "compare": compare}[cmd]()
