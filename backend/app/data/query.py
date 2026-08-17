"""数据查询接口：供 API 层与后续工具层复用。"""
from __future__ import annotations

from pathlib import Path

from . import lake


def get_daily(
    db_path: str | Path,
    code: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int = 1000,
) -> list[dict]:
    """查询日线行情，可按代码与日期区间过滤。"""
    conn = lake.get_connection(db_path, read_only=True)
    try:
        sql = "SELECT * FROM daily"
        conds: list[str] = []
        params: list = []
        if code:
            conds.append("code = ?")
            params.append(code)
        if start:
            conds.append("trade_date >= ?")
            params.append(start)
        if end:
            conds.append("trade_date <= ?")
            params.append(end)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY trade_date DESC, code LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        cols = [d[0] for d in conn.description]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def get_stock_list(db_path: str | Path) -> list[dict]:
    conn = lake.get_connection(db_path, read_only=True)
    try:
        rows = conn.execute("SELECT * FROM stocks ORDER BY code").fetchall()
        cols = [d[0] for d in conn.description]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def search_stocks(db_path: str | Path, q: str, limit: int = 30) -> list[dict]:
    """按代码（前缀）或名称（子串）搜索股票，供行情中心按名称/代码查询。"""
    q = (q or "").strip()
    if not q:
        return []
    conn = lake.get_connection(db_path, read_only=True)
    try:
        rows = conn.execute(
            """
            SELECT code, name, industry
            FROM stocks
            WHERE code LIKE ? OR name LIKE ?
            ORDER BY CASE WHEN code = ? THEN 0 WHEN code LIKE ? THEN 1 ELSE 2 END, code
            LIMIT ?
            """,
            [q + "%", "%" + q + "%", q, q + "%", limit],
        ).fetchall()
        cols = [d[0] for d in conn.description]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def get_latest_trade_date(db_path: str | Path) -> str | None:
    """数据湖里最新的交易日，用于判断数据新鲜度。"""
    conn = lake.get_connection(db_path, read_only=True)
    try:
        row = conn.execute("SELECT MAX(trade_date) AS d FROM daily").fetchone()
        return str(row[0]) if row and row[0] else None
    finally:
        conn.close()


def get_daily_date_range(db_path: str | Path) -> dict:
    conn = lake.get_connection(db_path, read_only=True)
    try:
        row = conn.execute(
            "SELECT MIN(trade_date) AS mn, MAX(trade_date) AS mx, COUNT(DISTINCT code) AS n_code FROM daily"
        ).fetchone()
        return {
            "min_date": str(row[0]) if row[0] else None,
            "max_date": str(row[1]) if row[1] else None,
            "distinct_codes": int(row[2]) if row[2] else 0,
        }
    finally:
        conn.close()
