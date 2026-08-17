"""本地数据湖封装：DuckDB 连接、建表、Parquet 导入、基础统计。"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Iterable

import duckdb

from . import schema

# DuckDB 同一进程内：只读/读写模式不能混用（报 "different configuration"），
# 多个读写连接也不能同时存在。但多个**只读**连接可以并存（本进程内与跨进程均如此）。
# 因此用「读写锁」取代旧的全局互斥：读连接并发（只读不占写文件锁，跨进程只读也能并存），
# 写连接独占（等待所有读连接关闭后才打开，避免与只读连接模式冲突）。
# 这样既保留对写的互斥保护，又让读多写少的回测/看板场景不再全局串行、也不再因
# 外部只读进程而触发 "Conflicting lock"。
class _RWLock:
    """写者优先的读写锁。"""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._readers = 0
        self._writer = False
        self._writer_waiting = 0

    def acquire_read(self) -> None:
        with self._cond:
            while self._writer or self._writer_waiting > 0:
                self._cond.wait()
            self._readers += 1

    def release_read(self) -> None:
        with self._cond:
            self._readers -= 1
            if self._readers == 0:
                self._cond.notify_all()

    def acquire_write(self) -> None:
        with self._cond:
            self._writer_waiting += 1
            try:
                while self._writer or self._readers > 0:
                    self._cond.wait()
            finally:
                self._writer_waiting -= 1
            self._writer = True

    def release_write(self) -> None:
        with self._cond:
            self._writer = False
            self._cond.notify_all()


_rwlock = _RWLock()


class _LockedConnection:
    """包装 DuckDB 连接：close() 时按读写模式释放对应锁，其余属性/方法透传。"""

    def __init__(self, conn: duckdb.DuckDBPyConnection, read_only: bool = False):
        self._conn = conn
        self._read_only = read_only

    def execute(self, *args, **kwargs):
        return self._conn.execute(*args, **kwargs)

    def close(self) -> None:
        try:
            self._conn.close()
        finally:
            if self._read_only:
                _rwlock.release_read()
            else:
                _rwlock.release_write()

    def __getattr__(self, name: str):
        return getattr(self._conn, name)


def get_connection(db_path: str | Path, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """打开 DuckDB 连接（read_only=True 为只读并发，False 为写独占；用毕必须 close()）。"""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if read_only:
        _rwlock.acquire_read()
    else:
        _rwlock.acquire_write()
    try:
        conn = duckdb.connect(str(path), read_only=read_only)
    except Exception:
        if read_only:
            _rwlock.release_read()
        else:
            _rwlock.release_write()
        raise
    return _LockedConnection(conn, read_only=read_only)


def init_lake(db_path: str | Path) -> None:
    """创建全部数据表（幂等）。"""
    conn = get_connection(db_path)
    try:
        conn.execute(schema.DDL)
    finally:
        conn.close()


def import_parquet(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    parquet_paths: Iterable[str | Path],
) -> int:
    """按列名对齐导入 parquet 文件到指定表。

    使用 ``INSERT ... BY NAME`` 让 DuckDB 按列名自动匹配，
    数据包里多余/缺失的列不会导致整表失败（缺失列填 NULL）。
    返回导入后该表总行数。
    """
    paths = [str(Path(p)) for p in parquet_paths]
    for p in paths:
        conn.execute(
            f"INSERT INTO {table} BY NAME SELECT * FROM read_parquet('{p}')"
        )
    row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
    return int(row[0])


def table_count(conn: duckdb.DuckDBPyConnection, table: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
    return int(row[0])


def table_summary(db_path: str | Path) -> dict[str, int]:
    """返回各表行数，用于数据管理看板。"""
    conn = get_connection(db_path, read_only=True)
    try:
        result: dict[str, int] = {}
        for table in schema.TABLE_COLUMNS:
            try:
                result[table] = table_count(conn, table)
            except Exception:
                result[table] = 0
        return result
    finally:
        conn.close()
