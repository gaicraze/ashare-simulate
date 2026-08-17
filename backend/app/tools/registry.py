"""工具注册表：统一管理所有工具（注册、列表、调用）。"""
from __future__ import annotations

from typing import Any

from .base import Tool


class ToolRegistry:
    """工具的单一入口，供 LLM 网关（P2）与 API 层使用。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具已注册: {tool.name}")
        self._tools[tool.name] = tool

    def register_many(self, tools: list[Tool]) -> None:
        for t in tools:
            self.register(t)

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"未知工具: {name}")
        return self._tools[name]

    def list_schemas(self) -> list[dict]:
        """返回全部工具的 schema 列表（供 LLM function calling 使用）。"""
        return [t.to_schema() for t in self._tools.values()]

    def list_names(self) -> list[str]:
        return list(self._tools.keys())

    def call(self, name: str, **kwargs: Any) -> Any:
        return self.get(name).execute(**kwargs)

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
