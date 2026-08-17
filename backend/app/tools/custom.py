"""自造工具：LLM 生成声明式 SQL 工具，统一校验、执行、注册、持久化。

设计：LLM 不生成任意 Python 代码（避免沙箱风险），而是生成「声明式 SQL 工具」
（name/description/parameters/sql），系统用统一的 SQLTool 安全执行只读查询。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..core import config
from ..data import lake
from .base import Tool

CUSTOM_FILE = config.DATA_DIR / "custom_tools.json"

# 只允许只读查询
FORBIDDEN = re.compile(
    r"\b(DROP|DELETE|UPDATE|INSERT|CREATE|ALTER|TRUNCATE|ATTACH|DETACH|COPY|PRAGMA)\b",
    re.IGNORECASE,
)


def _normalize_sql(sql: str) -> str:
    """把 :name 风格的命名参数统一为 DuckDB 的 $name。"""
    return re.sub(r":([a-zA-Z_][a-zA-Z0-9_]*)", r"$\1", sql)


class SQLTool(Tool):
    """声明式自造工具：执行命名参数化的只读 SQL。"""

    def __init__(self, name: str, description: str, parameters: dict, sql: str):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.sql = sql

    def execute(self, **kwargs):
        conn = lake.get_connection(config.DB_PATH, read_only=True)
        try:
            cur = conn.execute(_normalize_sql(self.sql), kwargs)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            return {"rows": rows, "count": len(rows)}
        finally:
            conn.close()


def _load() -> list[dict]:
    if not CUSTOM_FILE.exists():
        return []
    try:
        data = json.loads(CUSTOM_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001
        return []


def _save(tools: list[dict]) -> None:
    CUSTOM_FILE.parent.mkdir(parents=True, exist_ok=True)
    CUSTOM_FILE.write_text(json.dumps(tools, ensure_ascii=False, indent=2), encoding="utf-8")


def list_custom_defs() -> list[dict]:
    return _load()


def load_custom_tools() -> list[SQLTool]:
    return [SQLTool(t["name"], t["description"], t.get("parameters", {}), t["sql"]) for t in _load()]


def register_custom_tools(registry) -> None:
    for t in load_custom_tools():
        try:
            registry.register(t)
        except ValueError:
            pass


def validate_tool_def(def_: dict) -> str | None:
    """校验工具定义，返回错误信息；None 表示通过。"""
    name = def_.get("name", "")
    if not re.match(r"^[a-z][a-z0-9_]{2,40}$", name):
        return f"工具名不合法: {name}"
    sql = def_.get("sql", "")
    if not sql.strip():
        return "SQL 不能为空"
    if FORBIDDEN.search(sql):
        return "SQL 包含禁止的写操作（仅允许 SELECT 查询）"
    upper = sql.strip().upper()
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        return "SQL 必须以 SELECT/WITH 开头"
    # 试运行（空参数，可能因缺参数报错，只要不是语法/权限错误即可）
    try:
        conn = lake.get_connection(config.DB_PATH, read_only=True)
        try:
            conn.execute(_normalize_sql(sql))
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "syntax" in msg.lower() or "Catalog" in msg or "permission" in msg.lower():
                return f"SQL 校验失败: {msg[:120]}"
            # Binder/参数绑定错误可接受（说明是参数化查询，调用时需传参）
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        return f"SQL 执行失败: {str(e)[:120]}"
    return None


def save_custom_tool(def_: dict) -> dict:
    tools = _load()
    tools = [t for t in tools if t["name"] != def_["name"]]
    tools.append(def_)
    _save(tools)
    return def_


def delete_custom_tool(name: str) -> bool:
    tools = _load()
    new = [t for t in tools if t["name"] != name]
    if len(new) == len(tools):
        return False
    _save(new)
    return True
