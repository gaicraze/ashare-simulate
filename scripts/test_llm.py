"""P2 验证：DeepSeek API 连通性 + Function Calling。"""
from __future__ import annotations

import os

import httpx

BASE = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
KEY = os.getenv("DEEPSEEK_API_KEY", "")
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

TOOL = {
    "type": "function",
    "function": {
        "name": "get_market_regime",
        "description": "判断指定日期A股市场环境（牛市/熊市/震荡市）",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "日期 YYYY-MM-DD"},
            },
            "required": ["date"],
        },
    },
}


def call(messages: list, tools: list | None = None) -> dict:
    payload: dict = {"model": MODEL, "messages": messages, "max_tokens": 300}
    if tools:
        payload["tools"] = tools
    r = httpx.post(
        f"{BASE}/chat/completions",
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
        json=payload,
        timeout=120,
    )
    return {"status": r.status_code, "body": r.json() if r.status_code == 200 else r.text}


print("===== 测试1: 基本对话 =====")
r = call([{"role": "user", "content": "用一句话回答：A股市场是什么？"}])
print("status:", r["status"])
if r["status"] == 200:
    print("reply:", r["body"]["choices"][0]["message"]["content"])
else:
    print(r["body"])

print("\n===== 测试2: Function Calling =====")
r = call(
    [
        {"role": "system", "content": "你是一个股票分析助手，需要时调用工具。"},
        {"role": "user", "content": "帮我判断一下 2026-07-16 的A股市场环境。"},
    ],
    tools=[TOOL],
)
print("status:", r["status"])
if r["status"] == 200:
    msg = r["body"]["choices"][0]["message"]
    print("content:", msg.get("content"))
    print("tool_calls:", msg.get("tool_calls"))
else:
    print(r["body"])
