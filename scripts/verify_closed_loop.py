"""闭环端到端验证：构建策略 → 实施回测 → 报告 → 优化策略。

用 strategies.json 中的第一条策略跑一段短回测，然后依次生成结构化报告与优化后的策略，
验证「回测 → 报告 → 优化」三环能否打通。用法（backend/ 目录，用 venv）：

    python ../scripts/verify_closed_loop.py [start] [end] [decide_every]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.backtest import optimize, report, runner  # noqa: E402
from app.strategy import store as strategy_store  # noqa: E402


def main() -> None:
    start = sys.argv[1] if len(sys.argv) > 1 else "2024-09-24"
    end = sys.argv[2] if len(sys.argv) > 2 else "2024-10-18"
    decide_every = int(sys.argv[3]) if len(sys.argv) > 3 else 3

    strategies = strategy_store.list_strategies()
    if not strategies:
        print("no strategy found")
        sys.exit(1)
    s = strategies[0]
    print(f"策略: {s['name']} ({s['id']})")

    t0 = time.time()
    payload = runner.run_backtest(
        s["text"],
        start,
        end,
        decide_every=decide_every,
        initial_cash=1_000_000,
        strategy_name=s["name"],
        config=s.get("config"),
    )
    print(f"回测完成，耗时 {time.time()-t0:.0f}s，成交 {len(payload['trades'])} 笔，决策 {len(payload['decision_log'])} 次")
    print("metrics:", json.dumps(payload["metrics"], ensure_ascii=False))

    result_file = runner.save_result(payload)
    print("结果已保存:", result_file.name)

    rep = report.build_report(payload)
    print("结构化报告生成: 个股", len(rep["trade_stats"]["stock_summary"]), "只，月度", len(rep["monthly_returns"]))

    opt = optimize.optimize_strategy(payload)
    print("优化结果 ok =", opt.get("ok"))
    print("诊断:", opt.get("diagnosis", "")[:200])
    print("改动条数:", len(opt.get("changes", [])))
    for i, c in enumerate(opt.get("changes", []), 1):
        print(f"  {i}. {c}")
    print("优化后策略长度:", len(opt.get("strategy", "")))
    print("优化后 config:", json.dumps(opt.get("config"), ensure_ascii=False)[:300])


if __name__ == "__main__":
    main()
