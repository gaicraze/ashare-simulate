"""统一 LLM 网关：按用途(role)路由到配置的模型 + 失败 fallback。"""
from __future__ import annotations

from typing import Any

import httpx

from . import config_store


class LLMGateway:
    """OpenAI 兼容的 LLM 调用入口。

    每次调用读取最新配置，按 role（用途）路由到管理界面配置的模型；
    指定模型失败时自动 fallback 到其他可用模型。
    """

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = 2000,
        role: str = "decide",
        temperature: float = 0.3,
    ) -> dict:
        cfg = config_store.load_config()
        preferred = cfg.get("roles", {}).get(role)
        providers = [p for p in cfg.get("providers", []) if p.get("enabled")]
        if not providers:
            raise RuntimeError("未配置任何可用的 LLM 模型（请在管理界面添加模型）")

        ordered = sorted(providers, key=lambda p: 0 if p["id"] == preferred else 1)
        errors: list[str] = []
        for p in ordered:
            try:
                return self._chat(p, messages, tools, max_tokens, temperature)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{p['name']}: {e}")
        raise RuntimeError("所有 LLM 模型均失败 → " + " | ".join(errors))

    @staticmethod
    def _chat(p: dict, messages: list[dict], tools: list[dict] | None, max_tokens: int, temperature: float) -> dict:
        payload: dict[str, Any] = {
            "model": p["model"],
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
        r = httpx.post(
            f"{p['base_url']}/chat/completions",
            headers={"Authorization": f"Bearer {p['api_key']}", "Content-Type": "application/json"},
            json=payload,
            timeout=180,
        )
        r.raise_for_status()
        return r.json()


def extract_content(resp: dict) -> str:
    """从 LLM 响应中提取最终文本。

    - 兼容推理模型（如 deepseek-reasoner 类）把答案放在 reasoning_content、
      而 content 留空的情况：优先取 content，为空时回退 reasoning_content。
    - 剥离部分模型（如 MiniMax）在 content 里夹带的 `<think>…</think>` 思考块。
    """
    import re

    msg = (resp.get("choices") or [{}])[0].get("message", {})
    text = (msg.get("content") or msg.get("reasoning_content") or "").strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    return text
