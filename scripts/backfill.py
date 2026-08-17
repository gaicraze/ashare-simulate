"""P1 数据回填：换手率/估值进 daily，财务进 finances（幂等）。"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from app.core import config  # noqa: E402
from app.data import lake, schema  # noqa: E402

PARQUET_DIR = config.PARQUET_DIR


def add_daily_columns(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("ALTER TABLE daily ADD COLUMN IF NOT EXISTS pe_ttm DOUBLE")
    conn.execute("ALTER TABLE daily ADD COLUMN IF NOT EXISTS pb_mrq DOUBLE")


def backfill_daily_valuation(conn: duckdb.DuckDBPyConnection) -> None:
    v = (PARQUET_DIR / "valuation.parquet").as_posix()
    conn.execute(f"""
        UPDATE daily d
        SET turnover = v.turn,
            pe_ttm   = v.pe_ttm,
            pb_mrq   = v.pb_mrq
        FROM read_parquet('{v}') v
        WHERE d.code = v.stock_code AND d.trade_date = v.date::DATE
    """)


def rebuild_finances(conn: duckdb.DuckDBPyConnection) -> None:
    f = (PARQUET_DIR / "fundamentals.parquet").as_posix()
    conn.execute("DROP TABLE IF EXISTS finances")
    conn.execute(schema.FINANCES_DDL)
    conn.execute(f"""
        INSERT INTO finances
        SELECT
            stock_code AS code,
            stat_date::DATE AS report_date,
            pub_date::DATE AS pub_date,
            revenue, net_profit, roe, gross_margin, net_profit_margin, eps_ttm,
            yoy_net_profit, yoy_eps, yoy_equity, yoy_asset
        FROM read_parquet('{f}')
    """)


def main() -> None:
    config.ensure_dirs()
    conn = lake.get_connection(config.DB_PATH)
    try:
        add_daily_columns(conn)
        backfill_daily_valuation(conn)
        rebuild_finances(conn)

        # 验证
        r = conn.execute(
            "SELECT COUNT(*) AS n, COUNT(turnover) AS n_turn, COUNT(pe_ttm) AS n_pe FROM daily"
        ).fetchone()
        print(f"daily rows={r[0]}, turnover 回填={r[1]}, pe_ttm 回填={r[2]}")
        print(f"finances rows={lake.table_count(conn, 'finances')}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
