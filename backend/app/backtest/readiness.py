"""回测前就绪检查 + 自动补工具门控。

回答「这个策略到底能不能被系统严格执行」的问题，避免「策略要求周线/MACD/板块强度，
但系统根本没这些工具，LLM 拿通用工具糊一个近似结果」。

流程：
1. 大模型读策略文本，产出「执行计划」（这个策略该怎么一步步执行）；
2. 系统用「确定性关键词扫描」列出策略真正依赖的能力（不靠 LLM 主观，防止幻觉/漏检）；
3. 系统按能力清单逐项核对：现有工具集是否覆盖、数据表是否具备；
4. 有缺口时，先尝试「自动造工具」（生成声明式 SQL 工具并注册持久化），数据缺口给出提示；
5. 只有在缺口清零（或调用方显式 force）之后，回测才真正开跑。
"""
from __future__ import annotations

import json
import re

from ..llm.gateway import LLMGateway, extract_content
from ..tools import custom
from ..tools.registry import ToolRegistry

# 就绪检查的 LLM 调用走非推理模型角色；但部分非推理模型（如 MiniMax-M3）也会输出
# 思考块、内容可能被 max_tokens 截断，因此 LLM 只负责「执行计划」这个可读文本，
# 能力核对与缺口判定完全由确定性逻辑完成，LLM 失败不会误放行。
READINESS_ROLE = "optimize"
_ASSESS_MAX_TOKENS = 4000

# 由 Agent 层固定提供、无需在注册表里出现的工具
ALWAYS_COVERED = {"place_order", "get_latest_trade_date"}

# 能力 → 覆盖它的工具名列表（覆盖判定只认「工具名是否已在注册表里」，不靠 LLM）
CAPABILITIES: dict[str, list[str]] = {
    "日线均线与量价": ["get_stock_daily", "analyze_price_volume", "get_stock_ta"],
    "指数日线趋势/牛熊判断": ["get_index_daily", "get_market_regime"],
    "指数周线/月线趋势": ["get_index_trend"],
    "个股周线/月线均线": ["get_stock_ta"],
    "MACD": ["get_stock_ta"],
    "RSI": ["get_stock_ta", "get_index_trend"],
    "布林带": ["get_stock_ta", "get_index_trend"],
    "RPS相对强度（全市场）": ["get_rps_rank", "get_stock_profile"],
    "基本面（ROE/净利同比/PE）": ["screen_by_fundamentals", "get_stock_profile", "screen_quality_leaders", "screen_fundamental_trend"],
    "市场情绪与涨跌停": ["get_market_sentiment", "get_market_snapshot", "get_limit_up_info"],
    "资金流（主力净流入）": ["get_stock_moneyflow", "get_moneyflow_rank"],
    "流通市值/市值排名": ["rank_float_mktcap"],
    "行业/板块涨幅排名": ["get_industry_performance"],
    "换手率/成交额": ["get_stock_daily", "rank_by_metric", "get_stock_profile"],
    "涨停板统计": ["get_limit_up_info"],
    "估值（PE/PB）": ["rank_by_metric", "get_stock_profile"],
    "股票列表/代码": ["get_stock_list"],
}

# 确定性扫描关键词：策略文本命中任一关键词 → 判定需要该能力。
# 关键词尽量具体，避免把「覆盖了也不会误伤」的能力过度触发造成假缺口。
CAPABILITY_KEYWORDS: dict[str, list[str]] = {
    "MACD": ["MACD", "红柱", "顶背离", "底背离", "DIF", "DEA"],
    "RSI": ["RSI", "超买", "超卖"],
    "布林带": ["布林", "BOLL", "boll"],
    "指数周线/月线趋势": ["月线", "周线"],
    "个股周线/月线均线": ["周线", "生命线"],
    "流通市值/市值排名": ["市值", "流通市值"],
    "行业/板块涨幅排名": ["板块", "行业", "主线", "板块指数", "行业指数"],
    "RPS相对强度（全市场）": ["RPS", "rps", "相对强度", "强度排名"],
    "基本面（ROE/净利同比/PE）": ["ROE", "roe", "净利", "市盈率", "净利润同比", "PE"],
    "资金流（主力净流入）": ["资金流", "主力", "净流入", "净流出", "聪明钱"],
    "市场情绪与涨跌停": ["情绪", "涨跌停家数", "连板", "赚钱效应"],
    "涨停板统计": ["涨停"],
    "换手率/成交额": ["换手", "成交额", "日均成交"],
    "指数日线趋势/牛熊判断": ["牛熊", "牛市", "熊市", "震荡市", "择时", "定仓位", "定方向"],
    "日线均线与量价": ["均线", "量价", "量比", "缩量", "放量", "回踩", "站上", "金叉", "死叉", "MA5", "MA10", "MA20", "MA60"],
    "估值（PE/PB）": ["PB", "pb", "市净率", "估值"],
}

_SCHEMA_HINT = """
数据湖 DuckDB 表：
- daily(code, trade_date, open, high, low, close, volume, amount, pct_change, turnover, float_mktcap, pe_ttm, pb_mrq)：个股日线（float_mktcap=流通市值元）
- stocks(code, name, industry, list_date, status)：股票基础信息
- finances(code, report_date, pub_date, roe, net_profit_margin, yoy_net_profit, eps_ttm, ...)：季度财务
- indices(code, name, trade_date, open, high, low, close, volume, amount)：指数日线（当前仅沪深300=000300）
- moneyflow(code, trade_date, main_net_inflow, ...)：资金流
- sectors(code, sector, trade_date)：板块（稀疏行业快照，无板块指数行情）
"""

_PLAN_PROMPT = """你是 A 股量化回测系统的「回测前执行规划员」。下面是一份交易策略。

【策略】
{strategy}

【数据湖结构】
{schema_hint}

请用 150 字以内，说明这个策略在回测里应当如何一步步执行：择时 → 选股 → 买入 → 持仓 → 止损/止盈。只描述交易执行逻辑，不要提工具名、函数名。直接输出这段执行计划文本，不要输出 JSON、不要输出任何其它解释。
"""

# 自动造工具（SQL 声明式工具）的生成 prompt：只允许 SELECT 只读查询
_REMEDY_PROMPT = """你是数据分析工具生成器。系统需要补一个缺失的工具，请生成声明式 SQL 工具 JSON。
数据湖 DuckDB 表结构：
- daily(code, trade_date, open, high, low, close, volume, amount, pct_change, turnover, float_mktcap, pe_ttm, pb_mrq)
- stocks(code, name, industry, list_date, status)
- finances(code, report_date, pub_date, revenue, net_profit, roe, gross_margin, net_profit_margin, eps_ttm, yoy_net_profit)
- indices(code, name, trade_date, open, high, low, close, volume, amount)
- moneyflow(code, trade_date, main_net_inflow, super_net_inflow, large_net_inflow)
- sectors(code, sector, trade_date)

缺失的能力：{capability}
策略上下文：{why}

要求：
1. 只生成 SELECT/WITH 查询，禁止写操作。
2. 尽量自包含；确实需要外部输入时用命名参数 :xxx，并在 parameters.properties 完整定义（type/description），加入 required。
3. 只输出 JSON（不要 markdown 代码块），格式：
{{"name":"工具名snake_case","description":"工具描述","parameters":{{"type":"object","properties":{{...}},"required":[...]}},"sql":"SELECT ..."}}
"""


def _parse_json_blob(text: str) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if m:
        text = m.group(1)
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:  # noqa: BLE001
        pass
    try:
        start = text.find("{")
        if start >= 0:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        obj = json.loads(text[start : i + 1])
                        if isinstance(obj, dict):
                            return obj
                        break
    except Exception:  # noqa: BLE001
        pass
    return None


def scan_requirements(strategy_text: str) -> list[dict]:
    """确定性关键词扫描：命中即认为策略需要该能力。"""
    text = strategy_text or ""
    reqs: list[dict] = []
    for capability, keywords in CAPABILITY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            reqs.append({"capability": capability, "why": "", "covered": False})
    return reqs


def _covered(capability: str, registry: ToolRegistry) -> bool:
    if capability in ALWAYS_COVERED:
        return True
    tools = CAPABILITIES.get(capability)
    if tools is not None:
        return any(t in registry for t in tools)
    # 未知能力：模糊匹配工具名/描述
    tokens = [t for t in re.split(r"[^0-9A-Za-z_\u4e00-\u9fff]+", capability) if len(t) >= 2]
    if not tokens:
        return False
    for t in registry.list_schemas():
        name = t["function"]["name"]
        desc = t["function"].get("description", "")
        if any(tok in name or tok in desc for tok in tokens):
            return True
    return False


def check_coverage(requirements: list[dict], registry: ToolRegistry) -> list[dict]:
    """给需求清单逐项打上 covered 标记，返回含 covered 字段的完整清单。"""
    out = []
    for r in requirements:
        r = dict(r)
        r["covered"] = _covered(r.get("capability", ""), registry)
        out.append(r)
    return out


def plan_execution(strategy_text: str) -> str:
    """让 LLM 产出执行计划（best-effort，失败返回空串不阻断）。

    MiniMax-M3 等思考型模型偶发把 token 全花在 <think> 上、最终 content 为空，
    这里重试一次，仍为空则交给调用方用确定性兜底。
    """
    gateway = LLMGateway()
    prompt = _PLAN_PROMPT.format(strategy=strategy_text, schema_hint=_SCHEMA_HINT)
    last_err: Exception | None = None
    for _ in range(2):
        try:
            resp = gateway.chat(
                [{"role": "user", "content": prompt}],
                max_tokens=_ASSESS_MAX_TOKENS,
                role=READINESS_ROLE,
                temperature=0.2,
            )
            content = extract_content(resp).strip()
            if content:
                return content
        except Exception as e:  # noqa: BLE001
            last_err = e
    return f"（执行计划生成未返回有效内容{('：' + str(last_err)) if last_err else ''}，以下为能力核对结果）"


def fallback_plan(requirements: list[dict]) -> str:
    """确定性兜底：用已匹配的能力类别拼一句「如何执行」的概览，避免报告留空。"""
    caps = [r["capability"] for r in (requirements or [])]
    groups = {
        "择时": ["指数周线/月线趋势", "指数日线趋势/牛熊判断", "RSI", "布林带"],
        "选股": ["行业/板块涨幅排名", "RPS相对强度（全市场）", "基本面（ROE/净利同比/PE）", "流通市值/市值排名", "换手率/成交额"],
        "个股信号": ["个股周线/月线均线", "MACD", "日线均线与量价", "涨停板统计"],
    }
    parts = []
    for label, keys in groups.items():
        hit = [c for c in caps if c in keys]
        if hit:
            parts.append(f"{label}（{'、'.join(hit)}）")
    return "该策略按：\n" + "；\n".join(parts) + "。\n逐日：先判市场环境定仓位上限 → 按选股条件筛标的 → 用个股信号确认买点/止损 → 下单并跟踪持仓。" if parts else ""


def assess(strategy_text: str, registry: ToolRegistry) -> dict:
    """能力扫描（确定性）+ 覆盖核对（确定性）。不调 LLM，瞬时完成。"""
    requirements = check_coverage(scan_requirements(strategy_text), registry)
    gaps = [r for r in requirements if not r["covered"]]
    return {
        "execution_plan": "",
        "requirements": requirements,
        "gaps": gaps,
        "ready": len(gaps) == 0,
    }


def remedy_gaps(gaps: list[dict], registry: ToolRegistry) -> list[dict]:
    """对每个缺口尝试自动造一个 SQL 工具；返回补救结果列表。"""
    results = []
    gateway = LLMGateway()
    for gap in gaps:
        capability = gap.get("capability", "")
        why = gap.get("why", "") or capability
        prompt = _REMEDY_PROMPT.format(capability=capability, why=why)
        item = {"capability": capability, "why": why, "remedied": False, "detail": ""}
        try:
            resp = gateway.chat(
                [{"role": "user", "content": prompt}],
                max_tokens=2000,
                role=READINESS_ROLE,
                temperature=0.2,
            )
            def_ = _parse_json_blob(extract_content(resp))
            if not def_ or not def_.get("name") or not def_.get("sql"):
                item["detail"] = "未能生成有效 SQL 工具定义"
            else:
                err = custom.validate_tool_def(def_)
                if err:
                    item["detail"] = f"生成的 SQL 校验失败：{err}"
                elif def_["name"] in registry:
                    item["detail"] = f"工具 {def_['name']} 已存在"
                else:
                    tool = custom.SQLTool(def_["name"], def_["description"], def_.get("parameters", {}), def_["sql"])
                    registry.register(tool)
                    custom.save_custom_tool(def_)
                    item["remedied"] = True
                    item["detail"] = f"已自动生成并注册工具 {def_['name']}"
        except Exception as e:  # noqa: BLE001
            item["detail"] = f"自动造工具失败：{type(e).__name__}: {e}"
        results.append(item)
    return results


def gate(strategy_text: str, registry: ToolRegistry, force: bool = False, max_rounds: int = 2) -> dict:
    """回测前门控：判断 → 补工具 → 再判断，直到就绪或达到上限。"""
    report: dict = {"ready": False, "force": force, "rounds": [], "gaps": [], "remedies": []}
    for _ in range(max_rounds):
        a = assess(strategy_text, registry)
        report["execution_plan"] = a["execution_plan"]
        report["requirements"] = a["requirements"]
        report["rounds"].append({"ready": a["ready"], "gaps": [g["capability"] for g in a["gaps"]]})
        if a["ready"] or force:
            report["ready"] = a["ready"] or force
            report["gaps"] = a["gaps"]
            return report
        remedies = remedy_gaps(a["gaps"], registry)
        report["remedies"].extend(remedies)
        report["gaps"] = a["gaps"]
        if not any(r["remedied"] for r in remedies):
            return report
    a = assess(strategy_text, registry)
    report["execution_plan"] = a["execution_plan"]
    report["requirements"] = a["requirements"]
    report["ready"] = a["ready"]
    report["gaps"] = a["gaps"]
    return report


def gaps_markdown(report: dict) -> str:
    """把门控结果渲染成可读 Markdown。"""
    lines = ["## 回测前就绪检查", ""]
    plan = report.get("execution_plan") or fallback_plan(report.get("requirements") or [])
    if plan:
        lines += ["**执行计划**：", plan, ""]
    reqs = report.get("requirements") or []
    if reqs:
        lines.append("**需求能力核对**：")
        for r in reqs:
            mark = "✅ 已具备" if r.get("covered") else "❌ 缺失"
            lines.append(f"- {r['capability']}：{mark}" + (f"（{r['why']}）" if r.get("why") else ""))
        lines.append("")
    if report.get("remedies"):
        lines.append("**自动补救**：")
        for r in report["remedies"]:
            mark = "✅" if r.get("remedied") else "⚠️"
            lines.append(f"- {mark} {r['capability']}：{r['detail']}")
        lines.append("")
    if report.get("gaps"):
        lines.append("**仍缺（需造工具或补数据后才可严格执行）**：")
        for g in report["gaps"]:
            lines.append(f"- {g['capability']}" + (f"：{g['why']}" if g.get("why") else ""))
        lines.append("")
    lines.append("**结论**：" + ("✅ 可以开跑回测" if report.get("ready") else "❌ 暂不建议回测，先补齐上述缺口"))
    return "\n".join(lines)
