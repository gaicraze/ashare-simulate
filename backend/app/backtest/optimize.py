"""策略优化：基于回测结果报告，用 LLM 产出针对性优化的策略。

这是「构建策略 → 实施回测 → 优化策略」闭环的最后一环：
- 输入：某次回测的完整结果（含原始策略文本 + 结构化报告）。
- 输出：诊断摘要 + 逐条改动 + 优化后的策略文本 + 建议的结构化 config。

诊断数据全部来自 build_report 的「纯规则统计」，不依赖 LLM 的预训练知识，
避免把回测期之后的信息当作优化依据（未来函数）。
"""
from __future__ import annotations

import json
import re

from ..llm.gateway import LLMGateway, extract_content
from ..knowledge import store as knowledge_store
from .report import build_report

# 优化走独立 role，默认路由到非推理模型（minimax）——推理模型会把 token 花在
# reasoning 上导致最终 content 为空，见 config_store 中 optimize 角色的缺省路由。
OPTIMIZE_ROLE = "optimize"

# 输出用「标题分节」而非「单一 JSON 对象」：策略文本自带换行/引号，塞进一个 JSON
# 字符串极易被模型写坏；分节输出对 LLM 更友好，解析也更稳健。
_OPTIMIZE_PROMPT = """你是一名 A 股量化策略优化专家。下面给出某策略回测后的完整结果。请基于真实回测数据，对策略做「针对性」优化，而不是泛泛而谈。

【原始策略】
{strategy}

【回测指标】
{metrics}

【个股交易汇总（按净盈亏从高到低，最多前 30 只）】
{stock_summary}

【月度收益】
{monthly}

【市场状态分布（决策日）】
{market_states}

【决策摘要（最近若干次，倒序）】
{decisions}

{knowledge}

请结合个股盈亏、月度收益、最大回撤、胜率、市场状态分布，找出策略真正的短板（例如：追高被套、止损过晚、选股条件过宽、某类市场环境下持续亏损、交易过于频繁、持仓过度集中/分散等），并给出可量化的改进。

严格按下面四个部分输出，每部分以一个标题行开头（标题用【】包裹，单独占一行），除此之外不要输出任何额外解释或客套话：

【诊断】
用 200 字以内说明策略的主要短板，要引用具体数据（哪类股票亏了多少、哪几个月回撤最大）。

【改动】
3~6 条，每条单独一行，用数字编号，写清楚「改什么、改成什么数值、为什么」。

【优化后策略】
输出优化后的完整策略文本，保留与原始策略相同的五部分结构（【策略目标】【一、市场择时】【二、选股逻辑】【三、风控与止损】【四、执行规则】），把上面每条改动落实到具体、可量化的规则里，删掉已被证明无效的规则。

【配置建议】
一段 JSON（可留空写「无」）。只能包含这些严格字段：{{"version":1,"timing":{{"mode":"system|declared|autonomous","position_caps":{{"bull":0.9,"range":0.5,"bear":0.2}},"liquidate_on_bear":true}},"position":{{"max_total_pct":0.9,"max_single_pct":0.3,"single_caps":{{"bull":0.25,"range":0.15}},"max_holdings":5,"min_cash_pct":0.1,"max_industry_pct":0.4}},"risk":{{"stop_loss_pct":-0.08,"stop_on_ma20_break":true,"stop_on_weekly_ma20_break":false}},"execution":{{"decide_every":3,"order_price":"next_open"}}}}。无法映射到上述字段的优化（如回撤刹车、时间止损、MACD顶背离等）一律写进【优化后策略】文本，不要塞进 JSON。
"""


def optimize_strategy(result: dict, strategy_text: str | None = None) -> dict:
    """基于回测结果生成优化后的策略。

    返回 dict：{ok, diagnosis, changes, strategy, config, error?}
    """
    strategy_text = (strategy_text or result.get("strategy") or "").strip()
    if not strategy_text:
        return {"ok": False, "error": "回测结果中没有策略内容，无法优化"}

    report = build_report(result)
    metrics = report.get("metrics", {})
    ts = report.get("trade_stats", {})
    ds = report.get("decision_stats", {})
    monthly = report.get("monthly_returns", [])

    # 个股汇总附带名称，便于 LLM 理解「谁在赚、谁在亏」（build_report 已回填 name）
    stock_summary = []
    for s in ts.get("stock_summary", [])[:30]:
        stock_summary.append(
            {
                "code": s.get("code"),
                "name": s.get("name", ""),
                "pnl": s.get("pnl"),
                "return_pct": s.get("return_pct"),
                "buy_count": s.get("buy_count"),
                "sell_count": s.get("sell_count"),
                "holding": s.get("holding"),
            }
        )

    decisions = [
        {"date": d.get("date"), "market_state": d.get("market_state"), "summary": d.get("summary", "")}
        for d in ds.get("decision_summaries", [])[-12:]
    ]

    # 从知识中心检索与当前策略相关的知识（风控/仓位/选股/择时等），作为优化参考（RAG）
    knowledge_block, _ = knowledge_store.knowledge_context(strategy_text, top_k=3)

    prompt = _OPTIMIZE_PROMPT.format(
        strategy=strategy_text,
        metrics=json.dumps(metrics, ensure_ascii=False),
        stock_summary=json.dumps(stock_summary, ensure_ascii=False),
        monthly=json.dumps(monthly, ensure_ascii=False),
        market_states=json.dumps(ds.get("market_states", {}), ensure_ascii=False),
        decisions=json.dumps(decisions, ensure_ascii=False),
        knowledge=knowledge_block,
    )

    gateway = LLMGateway()
    last: dict | None = None
    # minimax 偶发输出过短/不完整，做一次重试；两次都不过关则回退用第二次结果，保证闭环可用
    for _ in range(2):
        out = _call_and_parse(gateway, prompt, result)
        last = out
        if _strategy_valid(out["strategy"]):
            return out
    return last or {"ok": False, "error": "优化失败"}


_STRATEGY_HEADERS = ("【策略目标】", "【一、市场择时】", "【二、选股逻辑】", "【三、风控与止损】", "【四、执行规则】")


def _strategy_valid(strategy: str) -> bool:
    """判断优化后策略是否「像样」：足够长且保留了至少 3 个规定章节标题。"""
    s = (strategy or "").strip()
    if len(s) < 80:
        return False
    return sum(1 for h in _STRATEGY_HEADERS if h in s) >= 3


def _call_and_parse(gateway: LLMGateway, prompt: str, result: dict) -> dict:
    """发起一次优化 LLM 调用并解析为结构化结果。"""
    resp = gateway.chat(
        [{"role": "user", "content": prompt}],
        max_tokens=8000,
        role=OPTIMIZE_ROLE,
        temperature=0.3,
    )
    content = extract_content(resp)
    if not content:
        raise RuntimeError("优化模型未返回内容（请稍后重试）")

    sections = _parse_sections(content)

    # 优化后策略缺失时，回退用原始输出整体作为策略文本，保证闭环仍可用
    strategy = sections.get("strategy", "").strip() or content
    config = _sanitize_config(_parse_json_blob(sections.get("config", "")))
    # 配置建议为空/非法时，回退到本次回测实际生效的配置，保持可复现
    if config is None:
        config = result.get("effective_config")

    return {
        "ok": True,
        "diagnosis": sections.get("diagnosis", "").strip(),
        "changes": _parse_changes(sections.get("changes", "")),
        "strategy": strategy,
        "config": config,
    }


# 分节标题 → 内部 key 的映射（按出现顺序解析）
_SECTIONS = [
    ("【诊断】", "diagnosis"),
    ("【改动】", "changes"),
    ("【优化后策略】", "strategy"),
    ("【配置建议】", "config"),
]


def _parse_sections(text: str) -> dict[str, str]:
    """按【标题】分节解析 LLM 输出；找不到标题时返回空 dict。"""
    # 找到每个标题的起始位置（记下标题长度，用于跳过标题行）
    positions: list[tuple[int, str, int]] = []
    for title, key in _SECTIONS:
        idx = text.find(title)
        if idx >= 0:
            positions.append((idx, key, len(title)))
    if not positions:
        return {}
    positions.sort(key=lambda x: x[0])

    result: dict[str, str] = {}
    for i, (start, key, title_len) in enumerate(positions):
        title_end = start + title_len
        end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        result[key] = text[title_end:end].strip()
    return result


def _parse_changes(text: str) -> list[str]:
    """把「改动」分节解析成列表：按行切分，去掉编号与空行。"""
    changes: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # 去掉 "1." / "1、" / "-" 等常见编号前缀
        line = re.sub(r"^\s*(?:\d+[.、)]|\-|\*)\s*", "", line).strip()
        if line:
            changes.append(line)
    return changes


def _parse_json_blob(text: str) -> object:
    """从一段文本里提取一个 JSON 对象（容忍 ```json 代码块与前后杂散文字）。"""
    text = (text or "").strip()
    if not text or text in ("无", "无。", "不需要", "null"):
        return None
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:  # noqa: BLE001
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                return obj
        except Exception:  # noqa: BLE001
            pass
    # 取首个平衡花括号块
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


def _sanitize_config(cfg: object) -> dict | None:
    """把 LLM 输出的 config 收敛到 Policy schema（version=1 的严格字段）。

    丢弃所有无法映射到 schema 的键（如 drawdown_brake、strong_bull、行业分散等），
    只保留 timing/position/risk/execution 下的合法字段；若清洗后为空则返回 None。
    """
    from .policy import TIMING_MODES

    if not isinstance(cfg, dict):
        return None

    def _num(v: object) -> float | None:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        return float(v)

    out: dict = {"version": 1}

    timing = cfg.get("timing")
    if isinstance(timing, dict):
        t: dict = {}
        mode = timing.get("mode")
        if mode in TIMING_MODES:
            t["mode"] = mode
        caps = timing.get("position_caps")
        if isinstance(caps, dict):
            clean_caps = {}
            for k in ("bull", "transition", "range", "bear"):
                v = _num(caps.get(k))
                if v is not None and 0 <= v <= 1:
                    clean_caps[k] = v
            if clean_caps:
                t["position_caps"] = clean_caps
        if "liquidate_on_bear" in timing:
            t["liquidate_on_bear"] = bool(timing["liquidate_on_bear"])
        if t:
            out["timing"] = t

    pos = cfg.get("position")
    if isinstance(pos, dict):
        p: dict = {}
        for k in ("max_total_pct", "max_single_pct", "min_cash_pct", "max_industry_pct"):
            v = _num(pos.get(k))
            if v is not None and 0 <= v <= 1:
                p[k] = v
        mh = pos.get("max_holdings")
        if isinstance(mh, int) and 1 <= mh <= 100:
            p["max_holdings"] = mh
        sc = pos.get("single_caps")
        if isinstance(sc, dict):
            clean_sc = {}
            for k in ("bull", "transition", "range", "bear"):
                v = _num(sc.get(k))
                if v is not None and 0 <= v <= 1:
                    clean_sc[k] = v
            if clean_sc:
                p["single_caps"] = clean_sc
        if p:
            out["position"] = p

    risk = cfg.get("risk")
    if isinstance(risk, dict):
        r: dict = {}
        if "stop_loss_pct" in risk:
            v = _num(risk["stop_loss_pct"])
            if v is not None and -1 <= v <= 0:
                r["stop_loss_pct"] = v
        for k in ("stop_on_ma20_break", "stop_on_weekly_ma20_break"):
            if k in risk and isinstance(risk[k], bool):
                r[k] = risk[k]
        if r:
            out["risk"] = r

    exe = cfg.get("execution")
    if isinstance(exe, dict):
        e: dict = {}
        de = exe.get("decide_every")
        if isinstance(de, int) and 1 <= de <= 250:
            e["decide_every"] = de
        if exe.get("order_price") in ("next_open", "close"):
            e["order_price"] = exe["order_price"]
        if e:
            out["execution"] = e

    # 只有 version 字段，说明 LLM 没给出任何有效配置
    if list(out.keys()) == ["version"]:
        return None
    return out
