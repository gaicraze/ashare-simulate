"""LLM provider 配置与加载（OpenAI 兼容接口）。"""
from __future__ import annotations

import os
from dataclasses import dataclass

from ..core import config  # noqa: F401  确保 load_dotenv 已执行


@dataclass
class Provider:
    name: str
    base_url: str
    api_key: str
    model: str


def load_providers() -> list[Provider]:
    """从环境变量加载已配置的 providers，顺序即默认优先级。"""
    providers: list[Provider] = []
    if os.getenv("DEEPSEEK_API_KEY"):
        providers.append(
            Provider(
                name="deepseek",
                base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
                api_key=os.getenv("DEEPSEEK_API_KEY", ""),
                model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
            )
        )
    if os.getenv("MINIMAX_API_KEY"):
        providers.append(
            Provider(
                name="minimax",
                base_url=os.getenv("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1").rstrip("/"),
                api_key=os.getenv("MINIMAX_API_KEY", ""),
                model=os.getenv("MINIMAX_MODEL", "MiniMax-M2"),
            )
        )
    return providers
