"""将已下载的 parquet 落库到 DuckDB 数据湖（幂等：先清空再导入）。"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from app.core import config  # noqa: E402
from app.data import lake  # noqa: E402

PARQUET_DIR = config.PARQUET_DIR


def load_daily(conn: duckdb.DuckDBPyConnection) -> None:
    p = (PARQUET_DIR / "daily.parquet").as_posix()
    conn.execute(f"""
        INSERT INTO daily
        SELECT
            stock_code AS code,
            date::DATE AS trade_date,
            open, high, low, close, volume, amount,
            NULL::DOUBLE AS adj_factor,
            ROUND((close / LAG(close) OVER (PARTITION BY stock_code ORDER BY date) - 1) * 100, 4) AS pct_change,
            NULL::DOUBLE AS turnover,
            NULL::DOUBLE AS float_mktcap
        FROM read_parquet('{p}')
    """)


def load_indices(conn: duckdb.DuckDBPyConnection) -> None:
    p = (PARQUET_DIR / "index_300.parquet").as_posix()
    conn.execute(f"""
        INSERT INTO indices
        SELECT
            '000300' AS code, '沪深300' AS name, date::DATE AS trade_date,
            open, high, low, close, volume::DOUBLE, amount
        FROM read_parquet('{p}')
    """)


def seed_stocks(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("INSERT INTO stocks (code) SELECT DISTINCT code FROM daily")


def main() -> None:
    config.ensure_dirs()
    lake.init_lake(config.DB_PATH)
    conn = lake.get_connection(config.DB_PATH)
    try:
        # 幂等：先清空
        for t in ("stocks", "daily", "indices"):
            conn.execute(f"DELETE FROM {t}")

        load_daily(conn)
        load_indices(conn)
        seed_stocks(conn)

        for t in ("stocks", "daily", "indices"):
            print(f"{t}: {lake.table_count(conn, t)} rows")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
