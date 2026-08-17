"""P2 验证：Minimax provider 连通性。"""
from __future__ import annotations

import os

import httpx

BASE = os.getenv("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1").rstrip("/")
KEY = os.getenv("MINIMAX_API_KEY", "")
MODEL = os.getenv("MINIMAX_MODEL", "MiniMax-M2")

r = httpx.post(
    f"{BASE}/chat/completions",
    headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
    json={"model": MODEL, "messages": [{"role": "user", "content": "用一句话回复：你好"}], "max_tokens": 30},
    timeout=60,
)
print("status:", r.status_code)
print(r.text[:500])
