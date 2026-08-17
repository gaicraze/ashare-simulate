"""检查已下载 parquet 的 schema 与样本，用于字段映射。"""
from __future__ import annotations

from pathlib import Path

import duckdb

PARQUET_DIR = Path(__file__).resolve().parents[1] / "backend" / "data" / "parquet"
conn = duckdb.connect()

for p in sorted(PARQUET_DIR.glob("*.parquet")):
    print(f"\n===== {p.name} =====")
    desc = conn.execute(f"DESCRIBE SELECT * FROM read_parquet('{p}')").fetchall()
    for col, typ, *_ in desc:
        print(f"  {col}: {typ}")
    n = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{p}')").fetchone()[0]
    print(f"  rows = {n}")
    # 样本前 3 行
    try:
        df = conn.execute(f"SELECT * FROM read_parquet('{p}') LIMIT 3").df()
        print("  sample:")
        for _, row in df.iterrows():
            print("   ", row.to_dict())
    except Exception as e:  # noqa: BLE001
        print(f"  sample error: {e}")

conn.close()
