"""LLM 驱动的策略决策 Agent（回测用）：分析 + place_order 下单。"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from ..llm.gateway import LLMGateway
from ..tools.base import Tool
from ..tools.registry import ToolRegistry

# trace 中每个工具结果的最大 JSON 长度（字符）。超出则只存预览，
# 避免 get_stock_list / get_rps_rank 等大结果把 decision_log 撑到 GB 级。
_RESULT_LIMIT = 1500

# 单次决策的预算安全阀（与 max_rounds 并列）：token / 墙钟时间任一超限即提前收尾，
# 防止推理模型在困难决策上无限分析或反复重试，控制成本与耗时。
_MAX_DECIDE_TOKENS = 120_000   # 单次决策累计 completion token 上限
_MAX_DECIDE_SECONDS = 900      # 单次决策墙钟时间上限（秒）

# 列表/排名类工具回传给模型的 rows 上限：模型只需看精筛后的头部候选，
# 几百行原始结果既撑上下文又容易让模型迷失，这里统一截到前 N 行。
_MODEL_ROWS_LIMIT = 30

# 单轮「交易思路」（reasoning_content/思考链）回传记录的长度上限（字符）。
# 推理模型单轮思考可能达数千字，记录全量会撑大 decision_log；截到足够表达思路的长度即可。
_REASONING_LIMIT = 6000

# 日期合法性：YYYY-MM-DD。用于识别 LLM 传入的截断/非法日期（如 "2026-"）。
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 部分模型（如 MiniMax）会把工具调用输出成 `<tool_call><invoke name="...">...</invoke></tool_call>` 文本，
# 而不是标准 function calling 的 tool_calls 字段，这里做兼容解析。
_TOOL_CALL_RE = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL | re.IGNORECASE)
_INVOKE_RE = re.compile(r'<invoke\s+name="([^"]+)"\s*>(.*?)</invoke>', re.DOTALL | re.IGNORECASE)
_PARAM_RE = re.compile(r"<([A-Za-z_]\w*)>(.*?)</\1>", re.DOTALL)
_XML_ENTITIES = {"&lt;": "<", "&gt;": ">", "&amp;": "&", "&quot;": '"', "&apos;": "'", "&#39;": "'"}


def _is_valid_date(s: Any) -> bool:
    return isinstance(s, str) and bool(_DATE_RE.match(s))


def _unescape_xml(s: str) -> str:
    for k, v in _XML_ENTITIES.items():
        s = s.replace(k, v)
    return s


def _compact_result(result: dict) -> dict:
    """把工具结果压缩到可控体积：小结果原样返回，大结果只保留预览。"""
    try:
        s = json.dumps(result, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        return result
    if len(s) <= _RESULT_LIMIT:
        return result
    return {"__truncated__": True, "size": len(s), "preview": s[:_RESULT_LIMIT]}


class PlaceOrder(Tool):
    """交易下单工具：LLM 通过它提交买卖指令，回测引擎据此撮合。"""

    name = "place_order"
    description = (
        "提交交易订单：buy=买入（用 cash_amount 指定金额，元）；"
        "sell=卖出（用 ratio 指定卖出比例 0~1，1 表示清仓该股）；hold=持有不动。"
        "成交价格口径见系统提示/快照 execution_note（默认下一交易日开盘价成交）。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["buy", "sell", "hold"], "description": "操作类型"},
            "code": {"type": "string", "description": "6位股票代码（hold 时可为空字符串）"},
            "cash_amount": {"type": "number", "description": "买入金额（元），仅 buy 需要"},
            "ratio": {"type": "number", "description": "卖出比例 0~1（1=清仓），仅 sell 需要"},
        },
        "required": ["action"],
    }

    def execute(self, action: str, code: str = "", cash_amount: float = 0.0, ratio: float = 1.0, **kwargs: Any) -> dict:
        return {"status": "order_accepted", "action": action, "code": code, "cash_amount": cash_amount, "ratio": ratio}


_BASE_PROMPT = """你是一名A股交易员，严格执行给定的交易策略。每个决策日你会收到账户状态快照，其中包含：
- market_state / market_signal：系统计算的市场状态参考信号（bull=真bull/牛市，MA20已在MA60上方连续≥3日 / transition=温和看多，站上20日线但趋势未确认 / range=震荡 / bear=熊市）；
- position_caps：系统对你实际生效的硬约束（总仓位上限、单只上限、持仓只数上限、强制保留现金）；
- execution_note：订单成交价格口径说明。
请按以下步骤工作：
1. 调用数据工具分析市场与个股（工具可多次调用）。重要：所有涉及日期的查询（analyze_price_volume、screen_by_fundamentals、rank_by_metric、get_market_regime、get_market_snapshot、get_index_daily 等）都必须传入当前决策日 date，绝不能省略 date 或使用最新日期（那会导致使用未来数据）。
2. 根据策略思路 + 当前持仓，调用 place_order 工具下单（buy/sell/hold）。
3. 完成所有工具调用后，输出一段「决策总结」（150字以内），分三点：①市场判断（当前是牛/熊/震荡，简述依据）；②分析结论与计划（本决策日想如何调整仓位、看中了哪些股票、为什么）；③最终决策（实际买入了哪些、卖出了哪些、或选择观望）。

交易纪律：
- 可交易范围只有 A 股个股（约 5000 只，代码前缀 00/30/60/68）；数据湖中没有 ETF、基金、债券等品种。若策略要求买入 ETF/指数等数据湖不存在的标的，禁止凭空编造代码下单，应在决策总结中明确说明「数据湖无该标的」，并改用个股替代或空仓。
- 工具口径：get_stock_daily / analyze_price_volume / get_stock_profile 等个股工具只接受个股代码（前缀 00/30/60/68）。查询大盘/指数趋势请用 get_index_daily（默认沪深300=000300，返回逐日K线与5/10/20/60日均线）或 get_market_regime（返回牛熊判定），不要用 get_stock_daily 去查 000300/399300/510300 等指数或 ETF 代码——那会返回空。
- 多周期与指标口径：判断周线/月线趋势（月线定周期、周线定方向）、MACD（金叉/死叉/红柱/顶背离）、RSI、布林带，请用 get_index_trend（指数，period=daily/weekly/monthly）或 get_stock_ta（个股，period=daily/weekly/monthly）；市值门槛/市值排名用 rank_float_mktcap；板块主线/行业涨幅排名用 get_industry_performance。策略若要求这些条件，必须实际调用对应工具算出来，不得凭感觉跳过。
- 选股纪律：screen_quality_leaders 用默认参数会返回数百只候选，切勿面对海量候选就放弃下单。务必用更严参数二次精筛到 ≤10 只短名单再复核买入，例如：screen_quality_leaders(min_rps=95、min_roe=10、min_deviation=5、max_deviation=20、max_pct_5d=15、max_pct_20d=40、max_turnover=30)，再对前几名用 get_stock_profile / analyze_price_volume 复核后果断下单。
- 买入用 cash_amount（元）；卖出用 ratio（0~1），1 表示清仓该股。
- 总仓位不得超过 position_caps 中的总仓位上限，单只不得超过 position_caps 中的单只上限；买入数量参考持仓只数上限，避免过度集中或过度分散。
- 每个决策日检查现有持仓的盈亏（pnl_pct）：若某持仓达到策略文本中定义的止损条件，主动止损卖出。止损由策略文本定义、由你自主判断，系统默认不强制。
- 严禁无脑追高；但对于已通过策略筛选、总分达标的标的，必须果断下单。
- 订单成交价格口径以快照中的 execution_note 为准。
- 轮次纪律（非常重要）：你的工具调用轮次有限。市场择时、行业主线、选股筛选用最多 2~3 轮完成，随后无论候选是否完全理想，都必须调用 place_order 给出本决策日的明确指令（buy/sell/hold），再输出决策总结。严禁反复调用同类工具把轮次耗尽却不产生任何订单。
"""

# 按择时模式追加的「强制/仓位规则」片段。
_MODE_RULES: dict[str, str] = {
    "system": """
强制行动规则（非常重要，按市场状态分档）：
- market_state=bull（真bull，趋势已确认）：积极选股买入，只要找到达标候选且可用仓位未满，就必须调用 place_order 至少买入 1 只，不得空仓观望。
- market_state=transition（温和看多，趋势刚启动/未确认）：轻仓试探（总仓位≤约30%），只精选 RPS120≥95/98 的最强龙头 1~2 只；无达标标的可观望，但不得重仓追高。
- market_state=range：震荡/无明确趋势，一律空仓（系统仓位上限已为 0），只减仓不加仓，绝不新增买入。
- market_state=bear：空仓观望，绝不买入。
""",
    "declared": """
仓位规则（系统已按你声明的各状态上限给出 position_caps 与 max_position_pct）：
- 你可以在当前上限内自由买卖；新增买入不得超过该上限。
- 当市场状态切换导致当前仓位高于新上限时，是否减仓由你按策略文本决定；系统只拦截超限的新增买入，不强制减仓。
- 择时判断（是否牛市、各状态下仓位该多高）以策略文本为准，market_state / market_signal 仅为参考信号。
""",
    "autonomous": """
仓位与择时规则（策略全自主）：
- market_state / market_signal 仅为系统提供的参考信号；是否采用、仓位多高、何时加减仓、是否止损，均由你按策略文本独立判断。
- 系统只施加账户级安全网（position_caps 中的总仓位/单只/只数上限），不会替你做择时或强制清仓。
""",
}


def _build_system_prompt(timing_mode: str) -> str:
    """按择时模式组装 system prompt；未知模式回退到 system 规则。"""
    rules = _MODE_RULES.get(timing_mode, _MODE_RULES["system"])
    return _BASE_PROMPT + rules


class LLMStrategyAgent:
    """把策略思路 + 账户快照交给 LLM，返回结构化订单列表。"""

    def __init__(
        self,
        gateway: LLMGateway,
        registry: ToolRegistry,
        strategy_text: str,
        max_rounds: int = 20,
        role: str = "backtest",
        temperature: float = 0.2,
        timing_mode: str = "system",
        max_tokens: int = 16000,
    ):
        self.gateway = gateway
        self.registry = registry
        self.strategy_text = strategy_text
        self.max_rounds = max_rounds
        self.role = role
        self.temperature = temperature
        self.timing_mode = timing_mode
        # 推理模型（deepseek-v4-pro 等）单轮 reasoning 就常达 4000~6000 token，
        # 默认 2000 会导致 finish_reason=length 截断、永远走不到 place_order/总结。
        self.max_tokens = max_tokens
        self.system_prompt = _build_system_prompt(timing_mode)
        self.current_date: str | None = None

    def decide(self, date: str, snapshot: dict) -> dict:
        # 记录当前决策日，用于给所有数据工具注入 point-in-time 截止日期，
        # 防止 LLM 漏传 date 时工具默认取「最新交易日」导致未来数据泄漏。
        self.current_date = date

        user = (
            f"策略思路：{self.strategy_text}\n\n"
            f"当前决策日：{date}\n"
            f"当前账户状态：\n{json.dumps(snapshot, ensure_ascii=False, default=str)}"
        )
        messages: list[dict] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user},
        ]
        tools = self.registry.list_schemas() + [PlaceOrder().to_schema()]

        orders: list[dict] = []
        trace: list[dict] = []
        summary = ""
        reasoning: list[str] = []  # 逐轮记录模型的「交易思路」（思考链），供前端展示
        t_start = time.time()
        used_tokens = 0
        for _ in range(self.max_rounds):
            # 预算安全阀：token / 时间任一超限即收尾（正常决策远不会触达）
            if used_tokens >= _MAX_DECIDE_TOKENS or (time.time() - t_start) >= _MAX_DECIDE_SECONDS:
                if not summary:
                    summary = f"（已达单次决策预算上限：completion_tokens={used_tokens}，提前收尾）"
                break
            resp = self.gateway.chat(messages, tools=tools, role=self.role, temperature=self.temperature, max_tokens=self.max_tokens)
            msg = resp["choices"][0]["message"]
            used_tokens += int(resp.get("usage", {}).get("completion_tokens", 0) or 0)
            # 捕获本轮「交易思路」：优先推理模型的 reasoning_content（思考链），否则回退 content 文本
            rc = self._strip_think(msg.get("reasoning_content") or "").strip()
            content = self._strip_think(msg.get("content") or "").strip()
            tool_calls = msg.get("tool_calls")
            if not tool_calls:
                # 兼容部分模型把工具调用输出成 `<tool_call>…</tool_call>` 文本的情况
                tool_calls = self._parse_text_tool_calls(content)
            if not tool_calls:
                summary = content
                # 最终轮若还有独立的思考链（reasoning_content），一并记入思路
                if rc and rc != content:
                    reasoning.append(rc[:_REASONING_LIMIT] + ("…" if len(rc) > _REASONING_LIMIT else ""))
                break
            thought = rc or content
            if thought:
                reasoning.append(thought[:_REASONING_LIMIT] + ("…" if len(thought) > _REASONING_LIMIT else ""))
            messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": tool_calls})
            for tc in tool_calls:
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                if name == "place_order":
                    parsed = self._parse_order(args)
                    if "hold" in parsed:
                        result = {"status": "hold"}
                        order_summary = "持有观望"
                    elif "rejected" in parsed:
                        result = {"status": "rejected", "reason": parsed["rejected"]}
                        order_summary = f"订单无效（{parsed['rejected']}）"
                    else:
                        orders.append(parsed)
                        result = {"status": "order_accepted"}
                        order_summary = self._order_text(args)
                    trace.append({"tool": name, "args": args, "summary": order_summary, "result": result})
                elif name == "get_latest_trade_date":
                    # 回测中「最新交易日」就是当前决策日，避免暴露未来数据
                    result = {"latest_trade_date": self.current_date}
                    trace.append({"tool": name, "args": args, "summary": self._tool_summary(name, args, result), "result": result})
                else:
                    args = self._inject_as_of(name, args)
                    try:
                        result = self.registry.call(name, **args)
                    except Exception as e:  # noqa: BLE001
                        result = {"error": f"{type(e).__name__}: {e}"}
                    trace.append({"tool": name, "args": args, "summary": self._tool_summary(name, args, result), "result": _compact_result(result)})
                # 面向模型的工具结果做精简（剔 bars、截 rows、兜底预览），
                # 避免把全量原始 JSON 塞回模型撑大上下文；完整结果仍留在 trace 供复盘。
                messages.append(
                    {"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(self._result_for_model(name, result), ensure_ascii=False, default=str)}
                )
        return {"orders": orders, "trace": trace, "summary": summary, "reasoning": reasoning}

    @staticmethod
    def _result_for_model(name: str, result: Any) -> Any:
        """把工具结果裁剪成「面向模型」的精简视图，只保留决策所需信息。

        规则（可叠加）：
        - 剔除 get_index_trend / get_stock_ta 的 bars 历史序列（模型只需标量+结论标志）；
        - 列表/排名类工具只保留前 _MODEL_ROWS_LIMIT 行，screen_by_fundamentals 再裁字段；
        - 兜底：仍超过 _RESULT_LIMIT 字符的走预览截断。
        """
        if not isinstance(result, dict):
            return result
        r = dict(result)
        # 1) 冗长历史序列：模型判断金叉/斜率用的是上方已算好的布尔+标量，bars 纯冗余
        for key in ("bars",):
            r.pop(key, None)
        # 2) 列表/排名类：只留头部候选（这就是「命中结果」，模型只需看精筛后的短名单）
        rows = r.get("rows")
        if isinstance(rows, list):
            original_len = len(rows)
            if name == "screen_by_fundamentals":
                keep = ("code", "roe", "net_profit_margin", "eps_ttm", "yoy_net_profit")
                rows = [{k: row.get(k) for k in keep if k in row} for row in rows[:_MODEL_ROWS_LIMIT]]
            else:
                rows = rows[:_MODEL_ROWS_LIMIT]
            if original_len > _MODEL_ROWS_LIMIT:
                r["__truncated__"] = True
            r["rows"] = rows
        # get_stock_list 的 codes 也做同样截断
        codes = r.get("codes")
        if isinstance(codes, list) and len(codes) > _MODEL_ROWS_LIMIT:
            r["codes"] = codes[:_MODEL_ROWS_LIMIT]
            r["__truncated__"] = True
        # 注：面向模型的结果不做字符串预览截断——结构化 rows/标量比截断后的字符串更有用；
        # 完整结果仍通过 trace 的 _compact_result 留存于 decision_log，避免日志膨胀。
        return r

    def _inject_as_of(self, name: str, args: dict) -> dict:
        """若工具支持 date/end 参数且 LLM 未传入，则注入当前决策日，杜绝未来数据。

        只对数据查询类工具生效（其 schema 里声明了 date 或 end 属性）；place_order
        等非查询工具不受影响。get_latest_trade_date 已在 decide 中单独处理。
        """
        try:
            tool = self.registry.get(name)
        except KeyError:
            return args
        props = (tool.parameters or {}).get("properties", {}) or {}
        args = dict(args)
        # date/end：缺失或非法（如 "2026-" 截断）都强制注入当前决策日，杜绝未来数据
        if "date" in props and not _is_valid_date(args.get("date")):
            args["date"] = self.current_date
        if "end" in props and not _is_valid_date(args.get("end")):
            args["end"] = self.current_date
        return args

    def _parse_text_tool_calls(self, content: str) -> list[dict] | None:
        """兼容解析部分模型（如 MiniMax）把工具调用输出成 `<tool_call>…</tool_call>` 文本的情况。

        把 `<invoke name="X"><k>v</k>…</invoke>` 转成与标准 function calling 相同结构的
        列表（id/type/function.name/function.arguments），避免这些调用被当成最终总结而中断分析。
        """
        if not content:
            return None
        parsed: list[dict] = []
        for block in _TOOL_CALL_RE.findall(content):
            for m in _INVOKE_RE.finditer(block):
                name = m.group(1).strip()
                args: dict[str, Any] = {}
                for pm in _PARAM_RE.finditer(m.group(2)):
                    args[pm.group(1)] = _unescape_xml(pm.group(2)).strip()
                parsed.append(
                    {
                        "id": f"txtcall_{len(parsed)}",
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
                    }
                )
        return parsed or None

    @staticmethod
    def _strip_think(text: str) -> str:
        """去掉推理模型输出的 <think>…</think> 思考块，只保留最终结论。"""
        if not text:
            return ""
        return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()

    @staticmethod
    def _tool_summary(name: str, args: dict, result: dict) -> str:
        """把工具调用结果提炼成人类可读的中文摘要。"""
        if name == "get_market_regime":
            close = result.get("index_close")
            return f"市场环境：{result.get('regime', '?')}" + (f"（沪深300 {close} 点）" if close else "")
        if name == "get_market_snapshot":
            s = result.get("snapshot") or {}
            return f"市场快照：上涨 {s.get('up')} / 下跌 {s.get('down')} 家，涨停 {s.get('limit_up')} / 跌停 {s.get('limit_down')}，平均 {s.get('avg_pct')}%"
        if name == "get_latest_trade_date":
            return f"最新交易日：{result.get('latest_trade_date')}"
        if name == "rank_by_metric":
            metric = args.get("metric", "?")
            rows = result.get("rows") or []
            top = "、".join(str(r.get("code")) for r in rows[:6])
            return f"{metric} 排名（前{len(rows)}）：{top}"
        if name == "screen_by_fundamentals":
            return f"基本面筛选：符合条件 {result.get('count', 0)} 只"
        if name == "screen_quality_leaders":
            rows = result.get("rows") or []
            head = "、".join(str(r.get("code")) for r in rows[:6])
            return f"优质龙头候选 {result.get('count', 0)} 只（前{len(rows)}）：{head}"
        if name == "screen_fundamental_trend":
            rows = result.get("rows") or []
            head = "、".join(str(r.get("code")) for r in rows[:6])
            return f"基本面趋势改善候选 {result.get('count', 0)} 只（前{len(rows)}）：{head}"
        if name == "get_stock_moneyflow":
            s = result.get("main_net_inflow_sum_yi")
            days = result.get("days", "?")
            return f"近{days}日主力净流入 {s} 亿（正=抢筹/负=流出）"
        if name == "get_moneyflow_rank":
            rows = result.get("rows") or []
            head = "、".join(str(r.get("code")) for r in rows[:6])
            return f"主力资金净流入排名（前{len(rows)}）：{head}"
        if name == "get_market_sentiment":
            return (
                f"情绪：上涨 {result.get('up')} / 下跌 {result.get('down')}，"
                f"涨停 {result.get('limit_up')} / 跌停 {result.get('limit_down')}，"
                f"连板高度 {result.get('max_limit_up_streak')} 板"
            )
        if name == "analyze_price_volume":
            code = args.get("code", "")
            close = result.get("close")
            ma20 = result.get("ma20")
            pct20 = result.get("pct_20d")
            vs = result.get("close_vs_ma20_pct")
            parts = [f"收盘 {close}"]
            if ma20:
                parts.append(f"20日线 {ma20}")
            if vs is not None:
                parts.append(f"偏离20日线 {vs}%")
            if pct20 is not None:
                parts.append(f"近20日 {pct20}%")
            return f"{code}：" + "，".join(parts)
        if name == "get_stock_daily":
            return f"日线行情：{len(result.get('rows') or [])} 条"
        if name == "get_index_daily":
            rows = result.get("rows") or []
            if not rows:
                return "指数日线：0 条"
            last = rows[0]
            parts = [f"{result.get('code', '?')} 收盘 {last.get('close')}"]
            for key, label in (("ma5", "MA5"), ("ma10", "MA10"), ("ma20", "MA20"), ("ma60", "MA60")):
                if last.get(key) is not None:
                    parts.append(f"{label} {last[key]}")
            return "，".join(parts) + f"（近{len(rows)}条）"
        if name == "get_index_trend":
            period = args.get("period", "daily")
            parts = [f"指数{period}线 收盘 {result.get('close')}"]
            for key, label in (("ma5", "MA5"), ("ma20", "MA20"), ("ma60", "MA60")):
                if result.get(key) is not None:
                    parts.append(f"{label} {result[key]}")
            if result.get("ma20_slope_up") is not None:
                parts.append("MA20斜率向上" if result.get("ma20_slope_up") else "MA20斜率未向上")
            if result.get("rsi24") is not None:
                parts.append(f"RSI24 {result['rsi24']}")
            return "，".join(parts)
        if name == "get_stock_ta":
            code = args.get("code", "")
            period = args.get("period", "daily")
            parts = [f"{code}{period}线 收盘 {result.get('close')}"]
            if result.get("ma20") is not None:
                parts.append(f"MA20 {result['ma20']}")
            if result.get("macd_dif") is not None:
                parts.append(f"MACD DIF {result['macd_dif']}/DEA {result.get('macd_dea')}/柱 {result.get('macd_hist')}")
                if result.get("macd_golden_cross"):
                    parts.append("MACD零轴上金叉")
            if result.get("rsi12") is not None:
                parts.append(f"RSI12 {result['rsi12']}")
            return "，".join(parts)
        if name == "rank_float_mktcap":
            rows = result.get("rows") or []
            head = "、".join(f"{r.get('code')}({r.get('float_mktcap_yi')}亿)" for r in rows[:6])
            return f"流通市值排名（前{len(rows)}）：{head}"
        if name == "get_industry_performance":
            rows = result.get("rows") or []
            head = "、".join(f"{r.get('industry')}{r.get('avg_return_pct')}%" for r in rows[:8])
            return f"行业涨幅排名（前{len(rows)}）：{head}"
        if name == "get_stock_list":
            return f"股票列表：{result.get('count', 0)} 只"
        if name == "get_stock_profile":
            f = result.get("fundamental") or {}
            v = result.get("valuation") or {}
            pv = result.get("price_volume") or {}
            rps = result.get("rps120")
            parts = []
            if f.get("roe_pct") is not None:
                parts.append(f"ROE {f['roe_pct']}%")
            if f.get("yoy_profit_pct") is not None:
                parts.append(f"净利同比 {f['yoy_profit_pct']}%")
            if v.get("pe_ttm") is not None:
                parts.append(f"PE {v['pe_ttm']}")
            if pv.get("close") is not None:
                parts.append(f"收盘 {pv['close']}")
            if pv.get("close_vs_ma20_pct") is not None:
                parts.append(f"偏离20日线 {pv['close_vs_ma20_pct']}%")
            if rps is not None:
                parts.append(f"RPS120 {rps}")
            return f"{result.get('code')}：" + "，".join(parts) if parts else f"{result.get('code')}：无数据"
        s = json.dumps(result, ensure_ascii=False, default=str)
        return s[:120]

    @staticmethod
    def _order_text(args: dict) -> str:
        action = args.get("action")
        code = str(args.get("code", ""))
        if action == "buy":
            return f"买入 {code}（金额 {args.get('cash_amount', 0)} 元）"
        if action == "sell":
            return f"卖出 {code}（比例 {args.get('ratio', 1)}）"
        return "持有观望"

    @staticmethod
    def _parse_order(args: dict) -> dict:
        """把 place_order 参数解析为订单。

        返回三种形态之一：
        - 有效订单 dict（action/code/...）
        - {"hold": True}（持有观望，无订单）
        - {"rejected": 原因}（非法订单，不静默丢弃）
        """
        action = args.get("action")
        code = str(args.get("code", "")).strip()
        if action == "buy":
            if not code:
                return {"rejected": "买入缺少股票代码"}
            try:
                cash_amount = float(args.get("cash_amount", 0))
            except (TypeError, ValueError):
                return {"rejected": "买入金额非法"}
            if cash_amount <= 0:
                return {"rejected": "买入金额需大于 0"}
            return {"action": "buy", "code": code, "cash_amount": cash_amount}
        if action == "sell":
            if not code:
                return {"rejected": "卖出缺少股票代码"}
            try:
                ratio = float(args.get("ratio", 1.0))
            except (TypeError, ValueError):
                ratio = 1.0
            ratio = max(0.0, min(1.0, ratio))
            if ratio <= 0:
                return {"rejected": "卖出比例需大于 0"}
            return {"action": "sell", "code": code, "ratio": ratio}
        if action == "hold":
            return {"hold": True}
        return {"rejected": f"未知操作类型: {action}"}
