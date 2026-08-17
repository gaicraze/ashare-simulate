"""首次运行：把 parquet 数据重建为本地 DuckDB 数据湖（幂等）。

从 `backend/data/parquet/*.parquet`（含分片重组后的完整文件）重建
`backend/data/duckdb/market.duckdb`，步骤：

1. 把 `*.parquet.part*` 分片重组回完整 parquet（已存在且非空则跳过）；
2. 建表并导入 daily / indices / stocks；
3. 用 valuation.parquet 回填 turnover / pe_ttm / pb_mrq；
4. 推导 float_mktcap（流通市值 ≈ 成交量 × 收盘价 × 100 / 换手率%）；
5. 用 fundamentals.parquet 重建 finances。

注意：stocks 的 name / industry 与 daily 的 adj_factor 依赖在线数据源，
首次联网后可通过「数据管理 → 元数据回填 / 增量更新」补齐（见 README）。

用法：
    python scripts/rebuild_lake.py
    python scripts/rebuild_lake.py --db-path /tmp/x.duckdb --parquet-dir backend/data/parquet
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from app.core import config  # noqa: E402
from app.data import lake, schema  # noqa: E402

SPLIT_FILES = ["daily.parquet", "valuation.parquet"]


def join_chunks(parquet_dir: Path) -> None:
    """把 *.parquet.part* 分片合并回完整 parquet（已存在且非空则跳过）。"""
    parquet_dir = Path(parquet_dir)
    for name in SPLIT_FILES:
        target = parquet_dir / name
        parts = sorted(parquet_dir.glob(f"{name}.part*"))
        if not parts:
            continue
        if target.exists() and target.stat().st_size > 0:
            continue
        expected = sum(p.stat().st_size for p in parts)
        with target.open("wb") as f:
            for p in parts:
                f.write(p.read_bytes())
        print(f"[join] {name}: {len(parts)} 分片 -> {target.stat().st_size / 1e6:.1f} MB (期望 {expected / 1e6:.1f} MB)")


def load_daily(conn: duckdb.DuckDBPyConnection, parquet_dir: Path) -> None:
    p = (parquet_dir / "daily.parquet").as_posix()
    conn.execute(f"""
        INSERT INTO daily
            (code, trade_date, open, high, low, close, volume, amount, adj_factor, pct_change)
        SELECT
            stock_code AS code,
            date::DATE AS trade_date,
            open, high, low, close, volume, amount,
            NULL::DOUBLE AS adj_factor,
            ROUND((close / LAG(close) OVER (PARTITION BY stock_code ORDER BY date) - 1) * 100, 4) AS pct_change
        FROM read_parquet('{p}')
    """)


def load_indices(conn: duckdb.DuckDBPyConnection, parquet_dir: Path) -> None:
    p = (parquet_dir / "index_300.parquet").as_posix()
    conn.execute(f"""
        INSERT INTO indices
        SELECT
            '000300' AS code, '沪深300' AS name, date::DATE AS trade_date,
            open, high, low, close, volume::DOUBLE, amount
        FROM read_parquet('{p}')
    """)


def seed_stocks(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("INSERT INTO stocks (code) SELECT DISTINCT code FROM daily")


def backfill_valuation(conn: duckdb.DuckDBPyConnection, parquet_dir: Path) -> None:
    v = (parquet_dir / "valuation.parquet").as_posix()
    conn.execute(f"""
        UPDATE daily d
        SET turnover = v.turn,
            pe_ttm   = v.pe_ttm,
            pb_mrq   = v.pb_mrq
        FROM read_parquet('{v}') v
        WHERE d.code = v.stock_code AND d.trade_date = v.date::DATE
    """)


def derive_float_mktcap(conn: duckdb.DuckDBPyConnection) -> None:
    # float_mktcap(元) ≈ volume(股) × close(元) × 100 / turnover(%)
    conn.execute("""
        UPDATE daily
        SET float_mktcap = volume * close * 100.0 / turnover
        WHERE turnover IS NOT NULL AND turnover > 0
          AND volume IS NOT NULL AND close IS NOT NULL
    """)


def rebuild_finances(conn: duckdb.DuckDBPyConnection, parquet_dir: Path) -> None:
    f = (parquet_dir / "fundamentals.parquet").as_posix()
    conn.execute("DROP TABLE IF EXISTS finances")
    conn.execute(schema.FINANCES_DDL)
    conn.execute(f"""
        INSERT INTO finances
            (code, report_date, pub_date, revenue, net_profit, roe, gross_margin,
             net_profit_margin, eps_ttm, yoy_net_profit, yoy_eps, yoy_equity, yoy_asset)
        SELECT
            stock_code AS code,
            stat_date::DATE AS report_date,
            pub_date::DATE AS pub_date,
            revenue, net_profit, roe, gross_margin, net_profit_margin, eps_ttm,
            yoy_net_profit, yoy_eps, yoy_equity, yoy_asset
        FROM read_parquet('{f}')
    """)


def main() -> None:
    ap = argparse.ArgumentParser(description="从 parquet 重建 DuckDB 数据湖")
    ap.add_argument("--db-path", default=str(config.DB_PATH))
    ap.add_argument("--parquet-dir", default=str(config.PARQUET_DIR))
    args = ap.parse_args()

    db_path = Path(args.db_path)
    parquet_dir = Path(args.parquet_dir)
    parquet_dir.mkdir(parents=True, exist_ok=True)

    join_chunks(parquet_dir)
    lake.init_lake(db_path)

    conn = lake.get_connection(db_path)
    try:
        for t in ("stocks", "daily", "indices"):
            conn.execute(f"DELETE FROM {t}")

        load_daily(conn, parquet_dir)
        load_indices(conn, parquet_dir)
        seed_stocks(conn)
        backfill_valuation(conn, parquet_dir)
        derive_float_mktcap(conn)
        rebuild_finances(conn, parquet_dir)

        print("== 数据湖重建完成 ==")
        for t in ("stocks", "daily", "indices", "finances"):
            print(f"  {t}: {lake.table_count(conn, t)} rows")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
