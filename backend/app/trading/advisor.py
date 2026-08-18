"""交易分析中心核心：策略 + 最新盘面 → 操作建议。

两阶段：
1. 选股（stock 模式）：用 LLM function calling 调用内置工具（市场环境/排名/基本面/资金流/量价等）
   从全市场筛选候选；若用户指定了某只股票则直接分析该股，不再全市场扫描。
2. 成文：把「策略全文 + 盘面环境（盘中含实时指数）+ 候选股数据（盘中含实时快照）+ 真实持仓（可选）」
   交给大模型，输出结构化操作建议（Markdown）。

数据口径：盘中自动拉取实时指数与候选/持仓个股快照，失败或非盘中降级为本地数据湖最新交易日。
"""
from __future__ import annotations

import json
import re
from typing import Any

from ..analysis.stock import collect_stock_data, resolve_stock
from ..llm.gateway import LLMGateway
from ..tools import db
from ..tools.base import Tool
from ..tools.default import default_registry
from ..tools.registry import ToolRegistry
from . import market as market_mod
from . import positions as positions_store


# --------------------------------------------------------------------------- #
# 工具：实时行情快照（仅交易分析中心使用，避免污染回测工具集）
# --------------------------------------------------------------------------- #
class GetLiveQuote(Tool):
    name = "get_live_quote"
    description = (
        "获取指定股票的实时/最新行情快照（现价、涨跌幅、今开、最高、最低、成交额、换手率、PE、PB、流通市值）。"
        "盘中返回腾讯实时数据，非盘中返回最新交易日数据。用于盘中选股时查看最新盘面。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "codes": {
                "type": "string",
                "description": "6位股票代码，多个用逗号分隔，如 600519,300750",
            },
        },
        "required": ["codes"],
    }

    def execute(self, codes: str = "", **kwargs: Any) -> dict[str, Any]:
        parsed = re.findall(r"\b\d{6}\b", codes or "")
        if not parsed:
            return {"error": "请提供6位股票代码"}
        quotes = market_mod.fetch_live_quotes(parsed[:30])
        if not quotes:
            return {"error": "未获取到实时行情（可能非盘中或网络不可用）", "quotes": []}
        return {"quotes": [_live_summary(q) for q in quotes.values()]}


def _advisor_registry() -> ToolRegistry:
    """复制默认工具集并追加实时行情工具（不改动回测共用的全局注册表）。"""
    reg = ToolRegistry()
    for name, tool in default_registry._tools.items():  # noqa: SLF001
        reg.register(tool)
    reg.register(GetLiveQuote())
    return reg


# --------------------------------------------------------------------------- #
# 数值辅助
# --------------------------------------------------------------------------- #
def _f(v: Any, nd: int = 2) -> float | None:
    if v is None:
        return None
    try:
        return round(float(v), nd)
    except (TypeError, ValueError):
        return None


def _fy(v: Any, nd: int = 4) -> float | None:
    """元 → 亿元。"""
    if v is None:
        return None
    try:
        return round(float(v) / 1e8, nd)
    except (TypeError, ValueError):
        return None


def _live_summary(q: dict | None) -> dict | None:
    if not q:
        return None
    return {
        "price": _f(q.get("close")),
        "pct_change": _f(q.get("pct_change")),
        "open": _f(q.get("open")),
        "high": _f(q.get("high")),
        "low": _f(q.get("low")),
        "amount_yi": _fy(q.get("amount")),
        "turnover": _f(q.get("turnover")),
        "pe_ttm": _f(q.get("pe_ttm")),
        "pb_mrq": _f(q.get("pb_mrq")),
        "float_mktcap_yi": _fy(q.get("float_mktcap")),
        "as_of": q.get("trade_date"),
    }


def _latest_close(code: str) -> float | None:
    row = db.one("SELECT close FROM daily WHERE code = ? ORDER BY trade_date DESC LIMIT 1", [code])
    return _f(row["close"]) if row else None


def _stock_name(code: str) -> str | None:
    row = db.one("SELECT name FROM stocks WHERE code = ?", [code])
    return row["name"] if row else None


# --------------------------------------------------------------------------- #
# 阶段一：选股
# --------------------------------------------------------------------------- #
PICK_SYSTEM = """你是A股选股助手。根据给定的交易策略，主动调用可用工具（市场环境、指标排名、基本面筛选、RPS强度、主力资金排名、涨跌停、量价分析、实时行情等）从全市场筛选出最符合策略的 3~6 只候选股票。

要求：
1. 必须先调用工具获取真实数据再下结论，严禁凭空编造股票代码。
2. 可多轮调用工具：先看市场环境/排名/筛选，再针对个股查量价/资金流/实时行情。
3. 查询排名、筛选、RPS 类工具时，date 参数请使用最新交易日 {date}。
4. 最终只输出一个 JSON 对象（不要输出其它任何文字），格式：
{"codes": ["600519", "300750"], "reason": "一句话说明选股逻辑"}
   若按策略当前应空仓、或没有符合条件的股票，请输出 {"codes": [], "reason": "说明原因"}。
5. codes 中只能是可交易的 A 股个股代码（来自筛选/排名工具的 rows.code），严禁填写指数代码（如 000300、000001、399001、399006）。"""

# 仅这些工具的返回里包含「可交易个股」的 code 列表，兜底采集时只认它们，避免误采指数代码。
_CANDIDATE_TOOLS = {
    "rank_by_metric",
    "screen_by_fundamentals",
    "screen_fundamental_trend",
    "screen_quality_leaders",
    "get_rps_rank",
    "get_moneyflow_rank",
    "get_stock_list",
}


def _valid_stock_codes(codes: list[str]) -> list[str]:
    """只保留 stocks 表中真实存在的个股代码，剔除指数 / 未知 / 编造代码。"""
    seen: list[str] = []
    for c in codes:
        c = str(c).strip()
        if c.isdigit() and len(c) == 6 and c not in seen:
            seen.append(c)
    if not seen:
        return []
    ph = ",".join(["?"] * len(seen))
    rows = db.rows(f"SELECT code FROM stocks WHERE code IN ({ph})", seen)
    valid = {r["code"] for r in rows}
    return [c for c in seen if c in valid]


def _parse_codes(content: str, trace: list[dict]) -> list[str]:
    text = (content or "").strip()
    codes: list[str] = []
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            codes = [str(c) for c in obj.get("codes", []) if str(c)]
        except Exception:  # noqa: BLE001
            codes = []
    if not codes:
        codes = re.findall(r"\b(\d{6})\b", text)
    if codes:
        return _valid_stock_codes(codes)[:6]
    # 兜底：只从筛选/排名类工具的结果里采集 code（避免误采指数代码）
    for t in trace:
        if t.get("tool") not in _CANDIDATE_TOOLS:
            continue
        r = t.get("result") or {}
        if isinstance(r, dict) and r.get("code"):
            codes.append(str(r["code"]))
        for row in (r.get("rows") or []) if isinstance(r, dict) else []:
            if isinstance(row, dict) and row.get("code"):
                codes.append(str(row["code"]))
    return _valid_stock_codes(codes)[:6]


def _pick_candidates(strategy_text: str, latest_date: str | None, scope: str | None) -> tuple[list[str], list[dict]]:
    """返回 (候选代码列表, 工具调用轨迹)。scope 指定个股时直接返回该股。"""
    if scope and scope.strip():
        s = resolve_stock(scope.strip())
        return ([s["code"]] if s else []), []

    gateway = LLMGateway()
    reg = _advisor_registry()
    date = latest_date or ""
    messages: list[dict] = [
        {"role": "system", "content": PICK_SYSTEM.replace("{date}", date)},
        {"role": "user", "content": f"最新交易日：{date}\n\n交易策略：\n{strategy_text}"},
    ]
    tools = reg.list_schemas()
    trace: list[dict] = []

    for _ in range(5):
        resp = gateway.chat(messages, tools=tools, role="trading", max_tokens=1600, temperature=0.2)
        msg = resp["choices"][0]["message"]
        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            codes = _parse_codes(msg.get("content") or "", trace)
            return codes, trace
        messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": tool_calls})
        for tc in tool_calls:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
            except Exception:  # noqa: BLE001
                args = {}
            try:
                result = reg.call(name, **args)
            except Exception as e:  # noqa: BLE001
                result = {"error": f"{type(e).__name__}: {e}"}
            trace.append({"tool": name, "arguments": args, "result": result})
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                }
            )
    return [], trace


# --------------------------------------------------------------------------- #
# 候选数据与持仓
# --------------------------------------------------------------------------- #
def _candidate_payload(data: dict, live: dict | None) -> dict:
    stock = data.get("stock") or {}
    return {
        "code": stock.get("code"),
        "name": stock.get("name"),
        "industry": stock.get("industry"),
        "quote": data.get("quote"),
        "live": live,
        "technical": data.get("technical"),
        "fundamentals": (data.get("fundamentals") or [])[:2],
        "moneyflow": data.get("moneyflow"),
        "sectors": data.get("sectors"),
        "rps": data.get("rps"),
        "notes": data.get("notes"),
    }


def enrich_positions(positions: list[dict], clock: dict) -> list[dict]:
    codes = [p["code"] for p in positions]
    live_map: dict[str, dict] = {}
    if clock.get("is_trading"):
        live_map = market_mod.fetch_live_quotes(codes)
    out: list[dict] = []
    for p in positions:
        q = live_map.get(p["code"])
        cur = _f(q.get("close")) if q else None
        live_pct = _f(q.get("pct_change")) if q else None
        if cur is None:
            cur = _latest_close(p["code"])
        cost = p.get("cost_price")
        qty = p.get("quantity")
        pnl_pct = ((cur - cost) / cost * 100) if cur and cost else None
        out.append(
            {
                "id": p.get("id"),
                "code": p["code"],
                "name": p.get("name") or _stock_name(p["code"]),
                "quantity": qty,
                "cost_price": cost,
                "current_price": cur,
                "live_pct_change": live_pct,
                "market_value": _f(cur * qty) if cur is not None and qty is not None else None,
                "pnl_pct": _f(round(pnl_pct, 3)) if pnl_pct is not None else None,
            }
        )
    return out


def portfolio_overview(positions: list[dict], account: dict) -> dict:
    """汇总账户资金 + 持仓的整体情况：总资产 / 仓位 / 现金 / 相对本金的盈亏。"""
    positions_value = _f(sum(p["market_value"] for p in positions if p.get("market_value") is not None))
    principal = account.get("principal")
    available_cash = account.get("available_cash")

    total_assets: float | None = None
    if positions_value is not None and available_cash is not None:
        total_assets = _f(positions_value + available_cash)
    elif positions_value is not None:
        total_assets = positions_value
    elif available_cash is not None:
        total_assets = available_cash

    position_ratio_pct: float | None = None
    cash_ratio_pct: float | None = None
    if total_assets and positions_value is not None:
        position_ratio_pct = _f(positions_value / total_assets * 100, 1)
    if total_assets and available_cash is not None:
        cash_ratio_pct = _f(available_cash / total_assets * 100, 1)

    total_pnl: float | None = None
    total_pnl_pct: float | None = None
    if principal and total_assets is not None:
        total_pnl = _f(total_assets - principal)
        total_pnl_pct = _f((total_assets / principal - 1) * 100, 2)

    return {
        "principal": principal,
        "available_cash": available_cash,
        "positions_value": positions_value,
        "total_assets": total_assets,
        "position_ratio_pct": position_ratio_pct,
        "cash_ratio_pct": cash_ratio_pct,
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl_pct,
    }


def portfolio_snapshot() -> dict:
    """账户 + 持仓的实时资产概览（供前端展示，无需生成建议）。

    持仓市值取最新价（盘中实时快照，失败/非盘中降级为最新交易日收盘价），
    与可用现金相加得到总资产，并计算仓位/现金比例与相对本金的盈亏。
    """
    clock = market_mod.trading_session()
    positions = enrich_positions(positions_store.list_positions(), clock)
    account = positions_store.get_account()
    overview = portfolio_overview(positions, account)
    return {
        "clock": clock,
        "positions": positions,
        "account": account,
        "overview": overview,
    }


# --------------------------------------------------------------------------- #
# 阶段二：生成建议
# --------------------------------------------------------------------------- #
ADVICE_SYSTEM = """你是一名经验丰富的A股交易顾问（投研助手）。你会拿到：一条交易策略、当前市场环境（可能含盘中实时数据）、一批候选股票的详细数据（可选）、用户的账户资金情况（可选，含本金/可用现金/持仓市值/仓位比例/相对本金盈亏）、真实持仓（可选）、以及用户的补充说明（可选）。请据此给出严谨、可执行的操作意见。

写作要求：
1. 所有数据必须来自「给定数据」，严禁编造；数据缺失处写「数据缺失」而非臆测。
2. 结论要明确（买入/增持/持有/减仓/卖出/观望），并说明依据；语气克制，不夸大、不承诺收益、不给出确定的买卖价格点位，只给参考区间或「关注/等待」类表述。
3. 给出「个股买入」建议时，必须结合最新盘面与账户资金情况（本金/可用现金），给出候选标的、买入理由与参考仓位/金额区间；若用户有持仓，还需结合持仓与账户情况，对每只持仓给出加减仓/持有/止损建议，并给出整体的现金与仓位管理意见。
4. 用户「补充说明」中的个性化要求（风险偏好、行业偏好、资金安排、规避某类股票等）优先级最高，需在结论中明确体现；与策略冲突时以补充说明为准，并说明取舍理由。
5. 用 Markdown 输出，结构如下（二级标题 ##）：
   ## 一、市场研判
   ## 二、候选标的与操作建议（无候选时省略此节）
   ## 三、持仓诊断与操作建议（无持仓时省略此节）
   ## 四、风险提示
6. 每个标的/持仓给出一句话操作结论（可附参考区间），并说明这是基于当前盘面的参考。
7. 结尾固定一行：> 本建议由 AI 自动生成，仅供研究参考，不构成任何投资建议，据此操作风险自负。"""


def build_advice_prompt(
    strategy_text: str,
    mode: str,
    ctx: dict,
    candidates: list[dict],
    positions: list[dict],
    account: dict | None = None,
    overview: dict | None = None,
    notes: str | None = None,
) -> str:
    lines: list[str] = []
    lines.append("【交易策略】")
    lines.append(strategy_text.strip() or "（未填写策略）")

    if notes and notes.strip():
        lines.append("\n【用户补充说明】")
        lines.append(notes.strip())

    lines.append("\n【市场环境】")
    lines.append(json.dumps(ctx, ensure_ascii=False, default=str))

    if candidates:
        lines.append("\n【候选股票数据】")
        for c in candidates:
            lines.append(json.dumps(c, ensure_ascii=False, default=str))

    # 账户资金 + 持仓：只要设置了本金/现金或存在持仓，就作为上下文提供（两种模式都提供）
    has_account = (account or {}).get("principal") is not None or (account or {}).get("available_cash") is not None
    lines.append("\n【用户账户与持仓】")
    if positions or has_account:
        lines.append(
            json.dumps(
                {"account": account or {}, "overview": overview or {}, "positions": positions},
                ensure_ascii=False,
                default=str,
            )
        )
    else:
        lines.append("（未填写本金/可用现金，且无持仓）")

    lines.append("\n请基于以上信息，输出操作建议（Markdown）。")
    return "\n".join(lines)


def _clean(content: str | None) -> str:
    if not content:
        return ""
    text = content.strip()
    for tag in ("think", "thinking"):
        start = text.find(f"<{tag}>")
        if start != -1:
            end = text.find(f"</{tag}>", start)
            if end != -1:
                text = text[end + len(f"</{tag}>"):].strip()
    return text.strip()


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def run_advice(
    strategy: dict,
    mode: str = "stock",
    scope: str | None = None,
    pull_intraday: bool = True,
    notes: str | None = None,
) -> dict:
    strategy_text = strategy.get("text") or ""
    ctx = market_mod.market_context(pull_intraday=pull_intraday)
    latest_date = ctx.get("latest_trade_date")

    # 1) 候选
    codes: list[str] = []
    pick_trace: list[dict] = []
    if mode == "stock":
        codes, pick_trace = _pick_candidates(strategy_text, latest_date, scope)
        codes = [c for c in codes if c and c.isdigit() and len(c) == 6]

    # 2) 候选数据 + 实时行情
    candidates: list[dict] = []
    live_map: dict[str, dict] = {}
    if codes and ctx["clock"]["is_trading"]:
        live_map = market_mod.fetch_live_quotes(codes)
    for code in codes[:6]:
        try:
            data = collect_stock_data(code)
        except Exception:  # noqa: BLE001
            continue
        candidates.append(_candidate_payload(data, _live_summary(live_map.get(code))))

    # 3) 账户资金（两种模式都提供）+ 持仓（portfolio 模式）
    account = positions_store.get_account()
    positions: list[dict] = []
    if mode == "portfolio":
        positions = enrich_positions(positions_store.list_positions(), ctx["clock"])
    overview = portfolio_overview(positions, account)

    # 4) 生成建议
    gateway = LLMGateway()
    resp = gateway.chat(
        [
            {"role": "system", "content": ADVICE_SYSTEM},
            {"role": "user", "content": build_advice_prompt(strategy_text, mode, ctx, candidates, positions, account, overview, notes)},
        ],
        max_tokens=8000,
        role="trading",
        temperature=0.4,
    )
    report = _clean(resp["choices"][0]["message"].get("content"))
    if not report:
        raise RuntimeError("模型返回内容为空（推理模型可能只输出了思考过程），请在「模型配置」中为「交易分析中心」选用非推理模型或重试")

    return {
        "strategy_id": strategy.get("id"),
        "strategy_name": strategy.get("name"),
        "mode": mode,
        "market": market_mod.market_context_jsonable(ctx),
        "candidates": candidates,
        "positions": positions,
        "account": account,
        "portfolio_overview": overview,
        "notes": (notes or "").strip(),
        "pick_trace": pick_trace,
        "report": report,
        "model": resp.get("model") or "unknown",
    }
