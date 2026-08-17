"""快速查看单个 parquet 的 schema 与样本。用法: python scripts/peek.py fundamentals.parquet"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb

PARQUET_DIR = Path(__file__).resolve().parents[1] / "backend" / "data" / "parquet"

for name in sys.argv[1:]:
    p = PARQUET_DIR / name
    if not p.exists():
        print(f"[missing] {name}")
        continue
    conn = duckdb.connect()
    print(f"===== {name} =====")
    desc = conn.execute(f"DESCRIBE SELECT * FROM read_parquet('{p.as_posix()}')").fetchall()
    for col, typ, *_ in desc:
        print(f"  {col}: {typ}")
    n = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{p.as_posix()}')").fetchone()[0]
    print(f"  rows = {n}")
    try:
        df = conn.execute(f"SELECT * FROM read_parquet('{p.as_posix()}') LIMIT 2").df()
        print("  sample:")
        for _, row in df.iterrows():
            print("   ", row.to_dict())
    except Exception as e:  # noqa: BLE001
        print(f"  sample error: {e}")
    conn.close()
