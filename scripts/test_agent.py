"""P2 验证：Agent 单回合决策（LLM 调用工具分析）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.agent.agent import Agent  # noqa: E402
from app.llm.gateway import LLMGateway  # noqa: E402
from app.tools.default import default_registry  # noqa: E402

gateway = LLMGateway()
print("providers:", gateway.names)

agent = Agent(gateway, default_registry)
strategy = "牛市期间买入热门板块的龙头股"
result = agent.decide(strategy, date="2026-07-16")

print("\n===== 结论 =====")
print(result["conclusion"])
print(f"\n===== 工具调用轨迹（{len(result['trace'])} 次）=====")
for t in result["trace"]:
    print(f"  round{t['round']} {t['tool']}({json.dumps(t['arguments'], ensure_ascii=False)})")
    r = json.dumps(t["result"], ensure_ascii=False, default=str)
    print(f"    -> {r[:180]}")
