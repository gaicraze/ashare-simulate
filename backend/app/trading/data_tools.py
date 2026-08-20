"""交易分析中心专用的「补全数据 / 自主创造工具」工具集。

这些工具只注册进交易分析中心的 advisor 注册表（不污染回测共用的全局注册表），
让大模型在分析过程中：
- 发现指数/个股数据滞后时，主动补全数据（update_index_data / update_stock_data）；
- 遇到现有工具覆盖不到的需求时，自主创造只读 SQL 工具（create_sql_tool）。
"""
from __future__ import annotations

import re
from typing import Any

from ..data import updater
from ..tools import custom, generator
from ..tools.base import Tool
from ..tools.registry import ToolRegistry


def _parse_codes(text: str | None) -> list[str]:
    seen: list[str] = []
    for c in re.findall(r"\b\d{6}\b", text or ""):
        if c not in seen:
            seen.append(c)
    return seen


class UpdateIndexData(Tool):
    """补齐指数日线到最新（解决指数数据滞后于个股的问题）。"""

    name = "update_index_data"
    description = (
        "补齐指定指数的日线数据到最新交易日（本地指数数据可能滞后于个股）。"
        "默认补沪深300(000300)，也支持 000001/399001/399006。"
        "当你发现 get_index_trend / get_index_daily / get_market_regime 返回的日期明显滞后，"
        "或报告显示「指数数据滞后」时，先调用本工具补全指数，再重新查询指数指标。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "codes": {
                "type": "string",
                "description": "指数代码，多个用逗号分隔，默认 000300",
            },
        },
        "required": [],
    }

    def execute(self, codes: str | None = None, **kwargs: Any) -> dict[str, Any]:
        parsed = _parse_codes(codes) or ["000300"]
        try:
            result = updater.incremental_index_update(parsed)
        except Exception as e:  # noqa: BLE001
            return {"error": f"{type(e).__name__}: {e}"}
        return result


class UpdateStockData(Tool):
    """补齐/刷新指定个股日线到最新（历史日线，盘中安全）。"""

    name = "update_stock_data"
    description = (
        "补齐/刷新指定个股的日线数据到最新（拉取历史日 K，INSERT 缺失日期、UPSERT 已有日期）。"
        "当你发现某只个股 get_stock_daily / get_stock_ta 数据不足或缺失时，先调用本工具补全。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "codes": {
                "type": "string",
                "description": "6位股票代码，多个用逗号分隔，如 600519,300750",
            },
        },
        "required": ["codes"],
    }

    def execute(self, codes: str = "", **kwargs: Any) -> dict[str, Any]:
        parsed = _parse_codes(codes)
        if not parsed:
            return {"error": "请提供6位股票代码"}
        try:
            result = updater.sync_stock_daily(parsed[:10])
        except Exception as e:  # noqa: BLE001
            return {"error": f"{type(e).__name__}: {e}"}
        return result


class CreateSqlTool(Tool):
    """自主创造只读 SQL 工具（声明式，禁写）。"""

    name = "create_sql_tool"
    description = (
        "当现有工具无法满足某个数据查询需求时，自主创造一个新的只读 SQL 查询工具并立即注册使用。"
        "两种方式二选一：(1) 给 requirement（自然语言），系统用大模型生成 SQL；"
        "(2) 直接给 name（snake_case）+ sql（仅 SELECT/WITH 只读查询）+ description。"
        "创建成功后可立即被本会话后续轮次调用，并持久化保存。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "requirement": {
                "type": "string",
                "description": "自然语言需求，如「筛选ROE>15%且市盈率为正的股票」，系统据此生成SQL",
            },
            "name": {"type": "string", "description": "工具名（snake_case），显式模式必填"},
            "description": {"type": "string", "description": "工具描述，显式模式必填"},
            "sql": {"type": "string", "description": "只读 SELECT/WITH 查询，显式模式必填"},
            "parameters": {"type": "object", "description": "JSON Schema 参数定义，显式模式可选"},
        },
        "required": [],
    }

    def __init__(self, registry: ToolRegistry):
        self.registry = registry

    def execute(self, requirement: str | None = None, name: str | None = None,
                description: str | None = None, sql: str | None = None,
                parameters: dict | None = None, **kwargs: Any) -> dict[str, Any]:
        if requirement and str(requirement).strip():
            try:
                def_ = generator.generate_tool_def(str(requirement).strip())
            except Exception as e:  # noqa: BLE001
                return {"error": f"生成工具失败: {type(e).__name__}: {e}"}
        elif name and sql:
            def_ = generator.build_tool_def(
                str(name).strip(), str(description or "").strip(), parameters or {}, str(sql).strip()
            )
        else:
            return {"error": "请提供 requirement（自然语言需求）或 name+sql（显式定义）"}

        err = custom.validate_tool_def(def_)
        if err:
            return {"error": f"工具校验失败: {err}"}

        tool_name = def_["name"]
        if tool_name in self.registry:
            return {"error": f"工具 {tool_name} 已存在"}

        tool = custom.SQLTool(tool_name, def_["description"], def_.get("parameters", {}), def_["sql"])
        self.registry.register(tool)
        custom.save_custom_tool(def_)
        return {"ok": True, "created": tool_name, "description": def_["description"]}
