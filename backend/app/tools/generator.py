"""自造工具生成助手：LLM 依据需求生成声明式 SQL 工具定义。

供两处复用：
- API ``POST /tools/generate``：用户手动输入需求生成工具；
- 交易分析中心的 ``create_sql_tool``：分析过程中让 LLM 自主创造工具。
"""
from __future__ import annotations

import json

from ..llm.gateway import LLMGateway, extract_content

TOOL_GEN_PROMPT = """你是数据分析工具生成器。用户需要一个数据查询工具，请生成一个 SQL 工具的 JSON 定义。
系统是 DuckDB 数据库，表结构：
- daily(code, trade_date, open, high, low, close, volume, amount, pct_change, turnover, pe_ttm, pb_mrq)：个股日线
- stocks(code, name, industry, list_date, status)：股票基础信息
- finances(code, report_date, pub_date, revenue, net_profit, roe, gross_margin, net_profit_margin, eps_ttm, yoy_net_profit)：季度财务
- indices(code, name, trade_date, open, high, low, close, volume, amount)：指数日线
- moneyflow(code, trade_date, main_net_inflow, super_net_inflow, large_net_inflow)：资金流
- sectors(code, sector, trade_date)：板块

要求：
1. 只生成 SELECT 查询（禁止写操作）。
2. 尽量让 SQL 自包含：不需要外部输入时直接用固定条件（如 roe > 0.1），不要用参数。
3. 确实需要外部输入时，用命名参数 :xxx，且必须在 parameters.properties 里完整定义该参数（type/description），并加入 required。
4. 只输出 JSON（不要 markdown 代码块），格式：
{"name":"工具名snake_case","description":"工具描述","parameters":{"type":"object","properties":{...},"required":[...]},"sql":"SELECT ..."}

用户需求：{requirement}
"""


def _strip_code_fence(content: str) -> str:
    text = (content or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # 去掉开头的 ```json 或 ```
        text = "\n".join(lines[1:])
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


def generate_tool_def(requirement: str, role: str = "strategy_gen") -> dict:
    """用 LLM 依据自然语言需求生成一个自造 SQL 工具定义（dict）。

    失败时抛出异常，由调用方决定如何提示。
    """
    gateway = LLMGateway()
    resp = gateway.chat(
        [{"role": "user", "content": TOOL_GEN_PROMPT.replace("{requirement}", requirement)}],
        max_tokens=1500,
        role=role,
        temperature=0.2,
    )
    content = _strip_code_fence(extract_content(resp))
    return json.loads(content)


def build_tool_def(name: str, description: str, parameters: dict, sql: str) -> dict:
    """依据显式字段（不经 LLM）构造一个自造 SQL 工具定义。"""
    return {
        "name": name,
        "description": description,
        "parameters": parameters or {"type": "object", "properties": {}, "required": []},
        "sql": sql,
    }
