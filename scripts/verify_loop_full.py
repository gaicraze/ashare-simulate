"""闭环完整验证：构建策略 → 回测 → 报告 → 优化 → 保存新策略 → 再回测 → 对比。

用 strategies.json 第一条策略跑一段回测，优化后保存为新策略，再用优化后策略重跑同一区间，
最后并排对比两次回测的绩效，验证「构建—回测—优化—再回测」闭环真的打通。

用法（backend/ 目录，用 venv）：
    python ../scripts/verify_loop_full.py [start] [end] [decide_every]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.backtest import optimize, runner  # noqa: E402
from app.strategy import store as strategy_store  # noqa: E402


def _fmt(m: dict) -> str:
    return (
        f"总收益 {m.get('total_return', 0)*100:+.2f}% | "
        f"年化 {m.get('annual_return', 0)*100:+.2f}% | "
        f"最大回撤 {m.get('max_drawdown', 0)*100:.2f}% | "
        f"夏普 {m.get('sharpe', 0):.2f} | "
        f"胜率 {m.get('win_rate', 0)*100:.1f}% | "
        f"成交 {m.get('trade_count', 0)} 笔"
    )


def main() -> None:
    start = sys.argv[1] if len(sys.argv) > 1 else "2024-09-24"
    end = sys.argv[2] if len(sys.argv) > 2 else "2024-10-18"
    decide_every = int(sys.argv[3]) if len(sys.argv) > 3 else 3

    strategies = strategy_store.list_strategies()
    if not strategies:
        print("no strategy found")
        sys.exit(1)
    s = strategies[0]
    print(f"原始策略: {s['name']} ({s['id']})")

    # 1. 首次回测
    t0 = time.time()
    p1 = runner.run_backtest(
        s["text"], start, end, decide_every=decide_every,
        initial_cash=1_000_000, strategy_name=s["name"], config=s.get("config"),
    )
    print(f"[1/4] 首次回测完成 {time.time()-t0:.0f}s → {_fmt(p1['metrics'])}")
    runner.save_result(p1)

    # 2. 优化
    t0 = time.time()
    opt = optimize.optimize_strategy(p1)
    print(f"[2/4] 优化完成 {time.time()-t0:.0f}s → 诊断 {len(opt.get('diagnosis',''))} 字 / 改动 {len(opt.get('changes',[]))} 条 / 新策略 {len(opt.get('strategy',''))} 字")
    for i, c in enumerate(opt.get("changes", []), 1):
        print(f"      改动{i}: {c}")

    # 3. 保存优化后策略
    new_name = f"{s['name']}·优化版"
    new_s = strategy_store.create_strategy(new_name, opt["strategy"], opt.get("config"))
    print(f"[3/4] 已保存优化后策略: {new_name} ({new_s['id']})")

    # 4. 用优化后策略再回测（同一区间）
    t0 = time.time()
    p2 = runner.run_backtest(
        new_s["text"], start, end, decide_every=decide_every,
        initial_cash=1_000_000, strategy_name=new_s["name"], config=new_s.get("config"),
    )
    print(f"[4/4] 再回测完成 {time.time()-t0:.0f}s → {_fmt(p2['metrics'])}")
    runner.save_result(p2)

    print("\n===== 闭环对比 =====")
    print(f"原始策略   : {_fmt(p1['metrics'])}")
    print(f"优化后策略 : {_fmt(p2['metrics'])}")


if __name__ == "__main__":
    main()
