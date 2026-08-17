# -*- coding: utf-8 -*-
"""独立进程运行 Mi姐策略回测（不依赖 HTTP 后端，后端重启不影响）。

用法:
    cd backend && .venv/bin/python ../scripts/run_mijie_backtest.py

读取 /tmp/mijie_strategy.json（策略生成接口的产物），
区间 2026-01-01 ~ 2026-06-01，decide_every=5，autonomous 模式。
完成后把报告 markdown 写到 /tmp/mijie_report.md 并打印关键指标。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.backtest import readiness, report, runner  # noqa: E402
from app.tools.default import default_registry  # noqa: E402

START, END = "2026-01-01", "2026-06-01"
DECIDE_EVERY = 5
INITIAL_CASH = 1_000_000
STRATEGY_NAME = "Mi姐波段趋势策略(2026版)"


def main() -> None:
    d = json.load(open("/tmp/mijie_strategy.json", encoding="utf-8"))
    strategy = d["strategy"]
    config = d.get("config") or {"version": 1, "timing": {"mode": "autonomous"}}

    # 回测前就绪门控：先判断策略能否被现有工具严格执行，缺口先补，再开跑
    gate = readiness.gate(strategy, default_registry)
    print("=== 回测前就绪检查 ===", flush=True)
    print(readiness.gaps_markdown(gate), flush=True)
    if not gate["ready"]:
        print("就绪检查未通过：请先补齐缺口后再回测（或用 force 强制）。", flush=True)
        sys.exit(2)

    t0 = time.time()

    def progress_cb(p: dict) -> None:
        ts = time.time() - t0
        print(
            f"[{ts:6.0f}s] {p.get('date', '')} state={p.get('market_state', '?')} "
            f"equity={p.get('equity', '?'):>12} trades={p.get('trade_count', '?')}",
            flush=True,
        )

    payload = runner.run_backtest(
        strategy,
        START,
        END,
        decide_every=DECIDE_EVERY,
        stop_loss=None,
        initial_cash=INITIAL_CASH,
        strategy_name=STRATEGY_NAME,
        progress_cb=progress_cb,
        config=config,
    )
    elapsed = time.time() - t0
    print(f"\n回测完成，耗时 {elapsed:.0f}s", flush=True)

    # 保存结果
    out = runner.save_result(payload)
    print(f"结果已保存: {out}", flush=True)

    # 生成报告
    rep = report.build_report(payload)
    rep["markdown"] = report.to_markdown(rep)
    md = rep["markdown"]
    Path("/tmp/mijie_report.md").write_text(md, encoding="utf-8")
    print(f"报告已保存: /tmp/mijie_report.md ({len(md)} 字符)", flush=True)

    m = payload["metrics"]
    print("\n===== 回测结果 =====")
    for k, v in m.items():
        print(f"  {k}: {v}", flush=True)
    print("===MARKDOWN_START===", flush=True)
    print(md, flush=True)
    print("===MARKDOWN_END===", flush=True)


if __name__ == "__main__":
    main()
