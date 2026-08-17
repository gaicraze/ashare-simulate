"""工具共用的数据湖访问辅助。"""
from __future__ import annotations

from typing import Any

from ..core import config
from ..data import lake


def rows(sql: str, params: list | tuple | None = None) -> list[dict]:
    """执行只读查询，返回 list[dict]。"""
    conn = lake.get_connection(config.DB_PATH, read_only=True)
    try:
        cur = conn.execute(sql, list(params) if params else [])
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def one(sql: str, params: list | tuple | None = None) -> dict | None:
    r = rows(sql, params)
    return r[0] if r else None
