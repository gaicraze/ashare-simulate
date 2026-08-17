"""策略执行 Agent：单回合 function calling 决策循环。"""
from __future__ import annotations

import json
from typing import Any

from ..llm.gateway import LLMGateway
from ..tools.registry import ToolRegistry

SYSTEM_PROMPT = """你是一名A股量化交易策略分析助手。用户会给你一条交易策略思路（自然语言描述），
你需要根据这条思路，主动调用可用的工具获取真实数据并分析，最后给出结论。

要求：
1. 优先调用工具获取真实数据，不要凭空编造。
2. 工具返回的是结构化 JSON，请基于数据做判断。
3. 工具可多次调用（例如先判断市场环境，再筛选股票，再查个股量价）。
4. 最终用简洁的中文给出结论：市场判断、候选标的、理由、以及操作建议（买入/观望/卖出）。
"""


class Agent:
    """把「策略思路 + 工具集」交给 LLM，执行一轮完整的分析决策。"""

    def __init__(self, gateway: LLMGateway, registry: ToolRegistry):
        self.gateway = gateway
        self.registry = registry

    def decide(
        self,
        strategy: str,
        date: str | None = None,
        max_rounds: int = 6,
        role: str = "decide",
    ) -> dict[str, Any]:
        user_content = f"策略思路：{strategy}"
        if date:
            user_content += f"\n当前决策日期：{date}"
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        tools = self.registry.list_schemas()
        trace: list[dict] = []

        for round_no in range(max_rounds):
            resp = self.gateway.chat(messages, tools=tools, role=role)
            msg = resp["choices"][0]["message"]
            tool_calls = msg.get("tool_calls")

            if not tool_calls:
                return {
                    "conclusion": msg.get("content", ""),
                    "rounds": round_no + 1,
                    "trace": trace,
                }

            # 追加干净的 assistant 消息（含 tool_calls）
            messages.append(
                {
                    "role": "assistant",
                    "content": msg.get("content"),
                    "tool_calls": tool_calls,
                }
            )
            for tc in tool_calls:
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                try:
                    result = self.registry.call(name, **args)
                except Exception as e:  # noqa: BLE001
                    result = {"error": f"{type(e).__name__}: {e}"}
                trace.append(
                    {"round": round_no + 1, "tool": name, "arguments": args, "result": result}
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    }
                )

        return {
            "conclusion": "(达到最大工具调用轮次，未输出最终结论)",
            "rounds": max_rounds,
            "trace": trace,
        }
