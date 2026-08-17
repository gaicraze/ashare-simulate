"""市场信号：三层择时（月线定周期 / 周线定方向 / 日线定仓位）+ 状态标签。

- ``state``：沿用日线 MA20/MA60 规则（bull/transition/range/bear），供 Policy 的
  system/declared 模式做仓位 clamp，保持向后兼容。
- ``layers``：新增的月线/周线/日线三层趋势明细（各层的收盘/MA5/MA20/MA60/MA20斜率/是否偏多），
  与策略「月线定周期、周线定方向、日线定仓位」对齐，作为下发给 LLM 的参考信号。
- ``three_layer_regime``：由三层共振/恶化归纳出的牛/熊/震荡标签（参考用，不改变 state）。
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime

from ..core import config
from ..data import lake


def _sma(vals: list[float], n: int) -> float | None:
    if len(vals) < n:
        return None
    return round(sum(vals[-n:]) / n, 4)


def _resample_closes(rows: list[tuple[str, float]], period: str) -> list[float]:
    """把 (trade_date, close) 升序序列聚合成该周期收盘价序列（daily 原样）。"""
    if period == "daily":
        return [c for _, c in rows]
    groups: OrderedDict = OrderedDict()
    for d, c in rows:
        dt = datetime.strptime(d, "%Y-%m-%d").date()
        key = (dt.isocalendar()[0], dt.isocalendar()[1]) if period == "weekly" else (dt.year, dt.month)
        groups.setdefault(key, []).append(c)
    return [g[-1] for g in groups.values()]


def _layer(rows: list[tuple[str, float]], period: str, kind: str) -> dict:
    """计算某一层（monthly/weekly/daily）的趋势指标与是否偏多。

    偏多口径对齐策略文本：
    - monthly（定周期）：收盘站上 MA20 且 MA5 > MA20；
    - weekly（定方向）：收盘站上 MA60 且 MA20 > MA60；
    - daily（定仓位）：收盘站上 MA20 且 MA20 斜率向上（近 5 期递增）。
    """
    closes = _resample_closes(rows, period)
    empty = {
        "period": period, "close": None, "ma5": None, "ma20": None, "ma60": None,
        "ma20_slope_up": False, "bullish": False,
    }
    if not closes:
        return empty
    ma5 = _sma(closes, 5)
    ma20 = _sma(closes, 20)
    ma60 = _sma(closes, 60)
    close = closes[-1]
    ma20_series = [_sma(closes[: i + 1], 20) for i in range(len(closes))]
    ma20_series = [x for x in ma20_series if x is not None]
    slope_up = len(ma20_series) >= 5 and all(
        ma20_series[-5:][j] < ma20_series[-5:][j + 1] for j in range(4)
    )
    if kind == "monthly":
        bullish = bool(close and ma20 and ma5 and close > ma20 and ma5 > ma20)
    elif kind == "weekly":
        bullish = bool(close and ma60 and ma20 and close > ma60 and ma20 > ma60)
    else:
        bullish = bool(close and ma20 and close > ma20 and slope_up)
    return {
        "period": period,
        "close": round(close, 4),
        "ma5": ma5,
        "ma20": ma20,
        "ma60": ma60,
        "ma20_slope_up": bool(slope_up),
        "bullish": bullish,
    }


def _three_layer_regime(layers: dict) -> str:
    """由三层偏多/偏空归纳牛/熊/震荡（参考标签，不影响 state）。"""
    flags = [layers.get(k, {}).get("bullish") for k in ("monthly", "weekly", "daily")]
    if all(f is True for f in flags):
        return "牛市"
    if all(f is False for f in flags):
        return "熊市"
    return "震荡"


@dataclass
class MarketSignal:
    """一次市场环境判断结果。

    - ``state``：语义标签 bull/transition/range/bear（供 LLM 与看板使用）。
      bull=真bull（MA20>MA60 连续≥3日）；transition=温和看多（站上20日线但趋势未确认）。
    - ``system_cap``：仅 ``system`` 择时模式使用该值作为总仓位上限。
    - ``layers`` / ``three_layer_regime``：三层择时参考信号。
    """

    state: str
    index_close: float | None
    ma20: float | None
    ma60: float | None
    system_cap: float
    layers: dict | None = None
    three_layer_regime: str = ""

    def to_dict(self) -> dict:
        d = {
            "state": self.state,
            "index_close": self.index_close,
            "ma20": self.ma20,
            "ma60": self.ma60,
            "system_cap": self.system_cap,
        }
        if self.layers:
            d["layers"] = self.layers
        if self.three_layer_regime:
            d["three_layer_regime"] = self.three_layer_regime
        return d


def _consec_ma20_above_ma60(closes: list[float]) -> int:
    """从最近一天起，MA20 连续位于 MA60 上方的天数（最多看 3 日）。"""
    consec = 0
    for i in range(len(closes) - 1, max(len(closes) - 4, -1), -1):
        m20 = _sma(closes[: i + 1], 20)
        m60 = _sma(closes[: i + 1], 60)
        if m20 is None or m60 is None or m20 <= m60:
            break
        consec += 1
    return consec


def compute_market_state(date: str) -> MarketSignal:
    """计算市场信号：日线状态标签 + 月/周/日三层择时明细。"""
    conn = lake.get_connection(config.DB_PATH, read_only=True)
    try:
        rows = conn.execute(
            """
            SELECT trade_date, close FROM indices
            WHERE code='000300' AND trade_date <= ?
            ORDER BY trade_date DESC LIMIT 500
            """,
            [date],
        ).fetchall()
    finally:
        conn.close()
    rows = [(str(r[0]), float(r[1])) for r in rows if r[1] is not None]
    rows.reverse()  # 升序
    if not rows:
        return MarketSignal("range", None, None, None, 0.0)

    daily_closes = [c for _, c in rows]
    close = daily_closes[-1]
    ma20 = _sma(daily_closes, 20)
    ma60 = _sma(daily_closes, 60)

    # 三层择时（月线定周期 / 周线定方向 / 日线定仓位）
    layers = {
        "monthly": _layer(rows, "monthly", "monthly"),
        "weekly": _layer(rows, "weekly", "weekly"),
        "daily": _layer(rows, "daily", "daily"),
    }
    regime = _three_layer_regime(layers)

    # 状态标签（保持原日线 MA20/MA60 规则，向后兼容）
    state = "range"
    system_cap = 0.0
    if close is not None and ma20 is not None and ma60 is not None:
        if close > ma20 and ma20 > ma60:
            consec = _consec_ma20_above_ma60(daily_closes)
            state = "bull" if consec >= 3 else "transition"
            system_cap = 0.9 if state == "bull" else 0.3
        elif close > ma20:
            state = "transition"
            system_cap = 0.3
        elif close < ma20 and ma20 < ma60:
            state = "bear"
            system_cap = 0.0
        else:
            state = "range"
            system_cap = 0.0

    return MarketSignal(state, close, ma20, ma60, system_cap, layers=layers, three_layer_regime=regime)
