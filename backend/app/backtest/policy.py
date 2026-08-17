"""策略策略层：把策略结构化配置解析为引擎可执行的 Policy。

Policy 只决定「硬约束与调度」（总仓位上限、单只上限、是否强平、止损、决策间隔、
下单价格口径），不做选股/择时判断——那是策略文本 + LLM 的职责。
"""
from __future__ import annotations

from dataclasses import dataclass

from .signals import MarketSignal

# 三种择时模式
TIMING_SYSTEM = "system"            # 系统按 CSI300 均线强制仓位（= 现状，向后兼容）
TIMING_DECLARED = "declared"        # 系统算状态，只按声明的每状态上限拦「新增买入」
TIMING_AUTONOMOUS = "autonomous"    # 系统只给参考信号，LLM 全自主，引擎不做择时 clamp

TIMING_MODES = (TIMING_SYSTEM, TIMING_DECLARED, TIMING_AUTONOMOUS)

# 账户级安全网缺省值（autonomous 兜底；system 预设单独把 max_single_pct 覆盖为 0.25）
DEFAULT_MAX_TOTAL_PCT = 0.95
DEFAULT_MAX_SINGLE_PCT = 0.30
DEFAULT_MAX_HOLDINGS = 10
DEFAULT_MIN_CASH_PCT = 0.0

# LLM 决策循环的最大工具调用轮次（安全阀，与 token/时间预算并列）。
# 复杂多条件策略（择时→主线→选股→个股复核→下单）需要足够轮次走完，但也用上限防跑飞。
DEFAULT_MAX_ROUNDS = 20

# system 预设下单只上限（与改造前引擎硬编码 0.25 一致，保证向后兼容）
SYSTEM_MAX_SINGLE_PCT = 0.25


@dataclass
class Policy:
    timing_mode: str = TIMING_SYSTEM
    position_caps: dict[str, float] | None = None   # declared 模式：每状态仓位上限
    liquidate_on_bear: bool = True                  # system 模式：bear 是否强清
    max_total_pct: float = DEFAULT_MAX_TOTAL_PCT    # 账户级总仓上限（安全网）
    max_single_pct: float = DEFAULT_MAX_SINGLE_PCT  # 账户级单只上限
    single_caps: dict[str, float] | None = None     # 每状态单只上限（覆盖 max_single_pct，如 bull=0.25/range=0.15）
    max_holdings: int = DEFAULT_MAX_HOLDINGS
    min_cash_pct: float = DEFAULT_MIN_CASH_PCT
    max_industry_pct: float | None = None           # 单一行业持仓占总资产上限（如 0.4=40%），None=不限
    stop_loss_pct: float | None = None              # None=策略自主；负数=引擎强制止损阈值
    stop_on_ma20_break: bool = False                # True=收盘跌破日线MA20 系统强制清仓（策略「硬止损」）
    stop_on_weekly_ma20_break: bool = False         # True=周线收盘跌破MA20 系统强制清仓（策略「机构生命线」）
    decide_every: int = 5
    order_price: str = "next_open"                  # next_open | close
    max_rounds: int = DEFAULT_MAX_ROUNDS            # LLM 决策循环最大轮次（经 config.execution.max_rounds 可配）

    # ---- 构造 ----
    @classmethod
    def from_config(
        cls,
        cfg: dict | None,
        legacy_stop_loss: float | None = None,
        legacy_decide_every: int = 5,
    ) -> "Policy":
        """从策略 config 解析 Policy。

        优先级：策略 config 字段 > legacy 参数（旧 API 的 stop_loss / decide_every 兜底）。
        cfg 为空时产出 system 预设，等价于改造前的引擎行为。
        """
        if not isinstance(cfg, dict):
            cfg = {}
        timing = cfg.get("timing") if isinstance(cfg.get("timing"), dict) else {}
        pos = cfg.get("position") if isinstance(cfg.get("position"), dict) else {}
        risk = cfg.get("risk") if isinstance(cfg.get("risk"), dict) else {}
        exe = cfg.get("execution") if isinstance(cfg.get("execution"), dict) else {}

        mode = timing.get("mode") or TIMING_SYSTEM
        if mode not in TIMING_MODES:
            mode = TIMING_SYSTEM

        # 单只上限：system 预设缺省 0.25（向后兼容），其余预设缺省 0.30
        if "max_single_pct" in pos:
            max_single_pct = float(pos["max_single_pct"])
        else:
            max_single_pct = SYSTEM_MAX_SINGLE_PCT if mode == TIMING_SYSTEM else DEFAULT_MAX_SINGLE_PCT

        # 止损：config 优先，legacy_stop_loss 作为无 config 时的兜底
        stop_loss = risk.get("stop_loss_pct") if "stop_loss_pct" in risk else legacy_stop_loss
        # 均线止损（收盘跌破日线MA20 / 周线破MA20）——系统级强制执行开关
        stop_on_ma20_break = bool(risk.get("stop_on_ma20_break", False))
        stop_on_weekly_ma20_break = bool(risk.get("stop_on_weekly_ma20_break", False))
        # 决策间隔：config 优先，legacy_decide_every 作为无 config 时的兜底
        decide_every = exe.get("decide_every") if "decide_every" in exe else legacy_decide_every
        # LLM 决策轮次上限：config 优先，缺省用 DEFAULT_MAX_ROUNDS
        max_rounds = int(exe.get("max_rounds", DEFAULT_MAX_ROUNDS)) if "max_rounds" in exe else DEFAULT_MAX_ROUNDS
        if max_rounds < 1:
            max_rounds = DEFAULT_MAX_ROUNDS

        single_caps = pos.get("single_caps") if isinstance(pos.get("single_caps"), dict) else None
        max_industry_pct = pos.get("max_industry_pct")
        max_industry_pct = float(max_industry_pct) if max_industry_pct is not None else None

        return cls(
            timing_mode=mode,
            position_caps=timing.get("position_caps") if isinstance(timing.get("position_caps"), dict) else None,
            liquidate_on_bear=bool(timing.get("liquidate_on_bear", True)),
            max_total_pct=float(pos.get("max_total_pct", DEFAULT_MAX_TOTAL_PCT)),
            max_single_pct=max_single_pct,
            single_caps=single_caps,
            max_holdings=int(pos.get("max_holdings", DEFAULT_MAX_HOLDINGS)),
            min_cash_pct=float(pos.get("min_cash_pct", DEFAULT_MIN_CASH_PCT)),
            max_industry_pct=max_industry_pct,
            stop_loss_pct=float(stop_loss) if stop_loss is not None else None,
            stop_on_ma20_break=stop_on_ma20_break,
            stop_on_weekly_ma20_break=stop_on_weekly_ma20_break,
            decide_every=int(decide_every),
            order_price=str(exe.get("order_price", "next_open")),
            max_rounds=max_rounds,
        )

    # ---- 引擎消费的决策点 ----
    def total_position_cap(self, sig: MarketSignal) -> float:
        """返回当前市场状态下的总仓位上限（0~1，相对总资产）。

        - system     → 信号自带的 system_cap（0.9/0/0）
        - declared   → 策略声明的该状态上限，未声明则回退 max_total_pct
        - autonomous → 账户级安全网 max_total_pct
        """
        if self.timing_mode == TIMING_SYSTEM:
            return sig.system_cap
        if self.timing_mode == TIMING_DECLARED:
            caps = self.position_caps or {}
            return float(caps.get(sig.state, self.max_total_pct))
        return self.max_total_pct

    def single_cap_for(self, state: str) -> float:
        """当前状态下的单只上限：优先用 single_caps[state]，否则回退 max_single_pct。

        用于「趋势期单只≤25%、震荡期单只≤15%」这类按市场状态分档的严格限仓。
        """
        if self.single_caps and state in self.single_caps:
            return float(self.single_caps[state])
        return self.max_single_pct

    def should_force_liquidate(self, sig: MarketSignal) -> bool:
        """是否由引擎强制清仓（收盘触发，次日开盘卖出）。

        仅 system 模式 + liquidate_on_bear + bear 时成立；declared/autonomous 交给策略自主。
        """
        return self.timing_mode == TIMING_SYSTEM and self.liquidate_on_bear and sig.state == "bear"

    def allows_decision(self, sig: MarketSignal) -> bool:
        """该市场状态下是否允许 LLM 决策。

        system 模式在 bear 时不调 LLM（由强制清仓接管，与改造前一致）；
        declared/autonomous 始终允许，由 LLM 决定减仓/买入。
        """
        if self.timing_mode == TIMING_SYSTEM and sig.state == "bear":
            return False
        return True

    def is_decision_day(self, i: int) -> bool:
        return i % self.decide_every == 0

    def to_dict(self) -> dict:
        """序列化实际生效的配置（写入回测结果，保证可复现）。"""
        return {
            "timing": {
                "mode": self.timing_mode,
                "position_caps": self.position_caps,
                "liquidate_on_bear": self.liquidate_on_bear,
            },
            "position": {
                "max_total_pct": self.max_total_pct,
                "max_single_pct": self.max_single_pct,
                "single_caps": self.single_caps,
                "max_holdings": self.max_holdings,
                "min_cash_pct": self.min_cash_pct,
                "max_industry_pct": self.max_industry_pct,
            },
            "risk": {
                "stop_loss_pct": self.stop_loss_pct,
                "stop_on_ma20_break": self.stop_on_ma20_break,
                "stop_on_weekly_ma20_break": self.stop_on_weekly_ma20_break,
            },
            "execution": {"decide_every": self.decide_every, "order_price": self.order_price, "max_rounds": self.max_rounds},
        }


def validate_config(cfg: dict | None) -> str | None:
    """轻量校验策略 config；返回错误信息字符串，None 表示通过。"""
    if cfg is None:
        return None
    if not isinstance(cfg, dict):
        return "config 必须是 JSON 对象"
    if "version" in cfg and cfg.get("version") != 1:
        return f"不支持的 config version: {cfg.get('version')}（当前仅支持 1）"

    timing = cfg.get("timing")
    if timing is not None:
        if not isinstance(timing, dict):
            return "timing 必须是对象"
        mode = timing.get("mode")
        if mode is not None and mode not in TIMING_MODES:
            return f"非法 timing.mode: {mode}（可选 {', '.join(TIMING_MODES)}）"
        caps = timing.get("position_caps")
        if caps is not None:
            if not isinstance(caps, dict):
                return "timing.position_caps 必须是对象"
            for k, v in caps.items():
                if k not in ("bull", "transition", "range", "bear"):
                    return f"非法状态 {k}（可选 bull/transition/range/bear）"
                if not isinstance(v, (int, float)) or not (0 <= v <= 1):
                    return f"timing.position_caps.{k} 必须在 0~1 之间"

    pos = cfg.get("position")
    if pos is not None:
        if not isinstance(pos, dict):
            return "position 必须是对象"
        for k in ("max_total_pct", "max_single_pct", "min_cash_pct", "max_industry_pct"):
            if k in pos and not isinstance(pos[k], (int, float)):
                return f"position.{k} 必须是数字"
        if "max_industry_pct" in pos and not (0 <= pos["max_industry_pct"] <= 1):
            return "position.max_industry_pct 必须在 0~1 之间"
        sc = pos.get("single_caps")
        if sc is not None:
            if not isinstance(sc, dict):
                return "position.single_caps 必须是对象"
            for k, v in sc.items():
                if k not in ("bull", "transition", "range", "bear"):
                    return f"非法状态 {k}（可选 bull/transition/range/bear）"
                if not isinstance(v, (int, float)) or not (0 <= v <= 1):
                    return f"position.single_caps.{k} 必须在 0~1 之间"

    risk = cfg.get("risk")
    if risk is not None:
        if not isinstance(risk, dict):
            return "risk 必须是对象"
        for k in ("stop_on_ma20_break", "stop_on_weekly_ma20_break"):
            if k in risk and not isinstance(risk[k], bool):
                return f"risk.{k} 必须是布尔值"

    exe = cfg.get("execution")
    if exe is not None:
        if not isinstance(exe, dict):
            return "execution 必须是对象"
        if "order_price" in exe and exe["order_price"] not in ("next_open", "close"):
            return f"非法 execution.order_price: {exe['order_price']}（可选 next_open/close）"
        if "max_rounds" in exe and not (isinstance(exe["max_rounds"], int) and exe["max_rounds"] >= 1):
            return "execution.max_rounds 必须是 ≥1 的整数"
    return None
