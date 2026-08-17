"""工具基类：所有可被 LLM 调用的工具都继承此基类。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Tool(ABC):
    """工具的抽象基类。

    每个工具必须提供 name / description / parameters(JSON Schema)，
    并实现 execute(**kwargs) 返回结构化结果（dict）。
    """

    name: str = ""
    description: str = ""
    parameters: dict = {"type": "object", "properties": {}, "required": []}

    @abstractmethod
    def execute(self, **kwargs: Any) -> Any:
        """执行工具，返回结构化结果（建议为 dict，便于 LLM 与看板消费）。"""

    def to_schema(self) -> dict:
        """返回 OpenAI Function Calling 兼容的工具描述。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def __repr__(self) -> str:
        return f"<Tool {self.name}>"
