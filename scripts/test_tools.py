"""P1 验证：逐个调用内置工具，检查输出。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.tools.default import default_registry  # noqa: E402

cases = [
    ("get_latest_trade_date", {}),
    ("get_stock_list", {}),
    ("get_market_regime", {"date": "2026-07-16"}),
    ("get_stock_daily", {"code": "600519", "limit": 3}),
    ("analyze_price_volume", {"code": "600519"}),
    ("screen_by_fundamentals", {"min_roe": 0.15, "top_n": 5}),
    ("rank_by_metric", {"metric": "turnover", "top_n": 5}),
    ("rank_by_metric", {"metric": "pe_ttm", "top_n": 5}),
    ("get_market_snapshot", {"date": "2026-07-16"}),
]

print(f"工具总数: {len(default_registry)}")
print(f"工具列表: {default_registry.list_names()}\n")

for name, args in cases:
    print(f"===== {name} {args} =====")
    try:
        r = default_registry.call(name, **args)
        s = json.dumps(r, ensure_ascii=False, default=str)
        print(s[:600])
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {type(e).__name__}: {e}")

print("\n===== schema 示例 (get_stock_daily) =====")
schema = default_registry.get("get_stock_daily").to_schema()
print(json.dumps(schema, ensure_ascii=False, indent=2))
