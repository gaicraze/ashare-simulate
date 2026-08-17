"""回测引擎：逐日循环 + 撮合 + 决策回调。

择时/仓位/风控等「策略级」决策由 Policy（policy.py）驱动；
引擎只负责撮合、账户、逐日循环与进度回调。市场状态判断见 signals.py。
"""
from __future__ import annotations

from typing import Callable

from ..core import config
from ..data import lake
from .account import Account
from .broker import AShareBroker
from .metrics import compute_metrics
from .policy import Policy
from .signals import compute_market_state


def limit_pct(code: str, is_st: bool = False) -> float:
    """近似涨跌停阈值：ST/*ST 用 5%，创业板(300/301)/科创板(688) 用 20%，其余用 10%。

    阈值取 9.8/19.8/4.9 而非 10/20/5，用于判断「开盘价是否已封板」的近似比较，
    避免用当日全天涨跌幅的未来数据。
    """
    if is_st:
        return 4.9
    if code.startswith(("300", "301", "688", "689")):
        return 19.8
    return 9.8


class BacktestEngine:
    def __init__(self, broker: AShareBroker | None = None, initial_cash: float = 1_000_000):
        self.broker = broker or AShareBroker()
        self.initial_cash = initial_cash
        self.current_max_pos = 0.9  # 当前市场状态允许的总仓位上限（由 Policy 在 run 中刷新）
        self.current_state = "range"  # 当前市场状态标签（run 中每日刷新；执行买单前必已刷新）
        self.policy: Policy | None = None

    # ---- 数据访问 ----
    def trading_days(self, start: str, end: str) -> list[str]:
        conn = lake.get_connection(config.DB_PATH, read_only=True)
        try:
            rows = conn.execute(
                "SELECT DISTINCT trade_date FROM daily WHERE trade_date >= ? AND trade_date <= ? ORDER BY trade_date",
                [start, end],
            ).fetchall()
            return [str(r[0]) for r in rows]
        finally:
            conn.close()

    def day_bars(self, date: str) -> dict[str, dict]:
        conn = lake.get_connection(config.DB_PATH, read_only=True)
        try:
            rows = conn.execute(
                """
                SELECT t.code, t.open, t.close, t.pct_change, t.prev_close, t.volume, s.name, s.industry FROM (
                    SELECT code, trade_date, open, close, pct_change, volume,
                           LAG(close) OVER (PARTITION BY code ORDER BY trade_date) AS prev_close
                    FROM daily
                    WHERE trade_date <= ? AND trade_date >= (?::DATE - INTERVAL 20 DAY)
                ) t
                LEFT JOIN stocks s ON s.code = t.code
                WHERE t.trade_date = ?
                """,
                [date, date, date],
            ).fetchall()
            return {
                r[0]: {
                    "open": r[1],
                    "close": r[2],
                    "pct_change": r[3],
                    "prev_close": r[4],
                    "volume": r[5],
                    "is_st": bool(r[6]) and (r[6].upper().startswith("ST") or r[6].upper().startswith("*ST")),
                    "industry": r[7],
                }
                for r in rows
            }
        finally:
            conn.close()

    def index_close(self, date: str) -> float | None:
        conn = lake.get_connection(config.DB_PATH, read_only=True)
        try:
            r = conn.execute(
                "SELECT close FROM indices WHERE code='000300' AND trade_date <= ? ORDER BY trade_date DESC LIMIT 1",
                [date],
            ).fetchone()
            return float(r[0]) if r and r[0] is not None else None
        finally:
            conn.close()

    def ma_breaks(self, code: str, date: str) -> dict:
        """返回个股当日是否收盘跌破日线MA20 / 周线MA20（供系统级均线止损）。

        只用 ``trade_date <= date`` 的数据，点内时、无未来函数；周线按 ISO 周聚合，
        与 builtin_ta 的 get_stock_ta 口径一致。
        """
        conn = lake.get_connection(config.DB_PATH, read_only=True)
        try:
            rows = conn.execute(
                """
                SELECT trade_date, close FROM daily
                WHERE code = ? AND trade_date <= ?
                ORDER BY trade_date DESC LIMIT 150
                """,
                [code, date],
            ).fetchall()
        finally:
            conn.close()
        rows = [(str(r[0]), float(r[1])) for r in rows if r[1] is not None]
        rows.reverse()  # 升序
        out = {"below_ma20": False, "below_weekly_ma20": False}
        if len(rows) < 20:
            return out
        closes = [c for _, c in rows]
        out["below_ma20"] = closes[-1] < (sum(closes[-20:]) / 20)
        # 周线聚合：ISO 周，取每周最后一根日线收盘
        from collections import OrderedDict
        from datetime import datetime
        groups: OrderedDict = OrderedDict()
        for d, c in rows:
            dt = datetime.strptime(d, "%Y-%m-%d").date()
            key = (dt.isocalendar()[0], dt.isocalendar()[1])
            groups.setdefault(key, []).append(c)
        weekly_closes = [g[-1] for g in groups.values()]
        if len(weekly_closes) >= 20:
            out["below_weekly_ma20"] = weekly_closes[-1] < (sum(weekly_closes[-20:]) / 20)
        return out

    # ---- 执行 ----
    def run(
        self,
        decide_fn: Callable,
        start: str,
        end: str,
        decide_every: int = 5,
        stop_loss: float | None = None,
        progress_cb: Callable | None = None,
        stop_check: Callable | None = None,
        policy: Policy | None = None,
    ) -> dict:
        # 未显式传入 Policy 时，用 legacy 参数构造 system 预设（等价于改造前行为）
        self.policy = policy or Policy.from_config(
            None, legacy_stop_loss=stop_loss, legacy_decide_every=decide_every
        )
        account = Account(self.initial_cash)
        days = self.trading_days(start, end)
        pending_orders: list[dict] = []
        decision_log: list[dict] = []
        closes: dict[str, float] = {}

        for i, date in enumerate(days):
            if stop_check and stop_check():
                break
            bars = self.day_bars(date)
            opens = {c: b["open"] for c, b in bars.items() if b["open"] is not None}
            closes = {c: b["close"] for c, b in bars.items() if b["close"] is not None}
            prev_closes = {c: b["prev_close"] for c, b in bars.items() if b["prev_close"] is not None}
            volumes = {c: b["volume"] for c, b in bars.items() if b["volume"] is not None}
            st_flags = {c: b["is_st"] for c, b in bars.items()}
            industries = {c: b.get("industry") for c, b in bars.items() if b.get("industry")}

            # 1. 执行上个决策日挂出的订单（当日开盘价成交）
            for order in pending_orders:
                self._execute(order, account, date, opens, prev_closes, volumes, st_flags, industries)
            pending_orders = []

            # 2. 记录收盘权益
            account.record_equity(date, closes)

            # 3. 市场状态信号（每日，由 signals.compute_market_state 判定；是否据此限仓由 Policy 决定）
            sig = compute_market_state(date)
            state = sig.state
            self.current_state = state
            self.current_max_pos = self.policy.total_position_cap(sig)

            # 4. 熊市强制清仓（收盘触发，次日开盘卖出；仅 system 模式生效）
            if self.policy.should_force_liquidate(sig):
                for code in list(account.positions.keys()):
                    pending_orders.append({"action": "sell", "code": code, "ratio": 1.0, "reason": "熊市强制清仓"})

            # 5. 个股止损（系统级强制执行策略的「止损铁律」，默认关闭、由 config 开启）：
            #    a) 止损幅度硬约束（浮亏 ≤ stop_loss_pct）
            #    b) 收盘跌破日线MA20 → 清仓（硬止损）
            #    c) 周线收盘跌破MA20 → 清仓（机构生命线）
            need_pct_stop = self.policy.stop_loss_pct is not None
            need_ma_stop = self.policy.stop_on_ma20_break or self.policy.stop_on_weekly_ma20_break
            if need_pct_stop or need_ma_stop:
                for code in list(account.positions.keys()):
                    pos = account.positions.get(code)
                    if not pos or code not in closes:
                        continue
                    reason = None
                    if need_pct_stop:
                        loss_pct = closes[code] / pos.avg_cost - 1
                        if loss_pct <= self.policy.stop_loss_pct:
                            reason = f"系统强制止损（浮亏 {loss_pct:.1%} ≤ {self.policy.stop_loss_pct:.1%}）"
                    if reason is None and need_ma_stop:
                        flags = self.ma_breaks(code, date)
                        if self.policy.stop_on_ma20_break and flags["below_ma20"]:
                            reason = "系统强制止损（收盘跌破日线MA20）"
                        elif self.policy.stop_on_weekly_ma20_break and flags["below_weekly_ma20"]:
                            reason = "系统强制止损（周线收盘跌破MA20·机构生命线）"
                    if reason:
                        pending_orders.append({"action": "sell", "code": code, "ratio": 1.0, "reason": reason})

            # 6. 定期决策（system 模式熊市不调 LLM，与改造前一致）
            if self.policy.is_decision_day(i) and self.policy.allows_decision(sig):
                snapshot = self._snapshot(account, date, closes)
                snapshot["market_state"] = state
                snapshot["max_position_pct"] = self.current_max_pos
                # 参考信号 + 实际生效硬约束 + 价格口径，供 LLM 决策与复盘
                snapshot["market_signal"] = sig.to_dict()
                snapshot["position_caps"] = {
                    "max_total_pct": self.policy.max_total_pct,
                    "max_single_pct": self.policy.max_single_pct,
                    "max_holdings": self.policy.max_holdings,
                    "min_cash_pct": self.policy.min_cash_pct,
                    "effective_total_cap": self.current_max_pos,
                }
                snapshot["execution_note"] = self._execution_note()
                try:
                    result = decide_fn(date, snapshot) or {}
                    orders = result.get("orders", []) if isinstance(result, dict) else (result or [])
                    trace = result.get("trace", []) if isinstance(result, dict) else []
                    reasoning = result.get("reasoning", []) if isinstance(result, dict) else []
                    pending_orders.extend(orders)
                    # 决策摘要：优先用 LLM 生成的总结，否则简单拼接
                    summary = result.get("summary") if isinstance(result, dict) else ""
                    if not summary:
                        if orders:
                            parts = []
                            for o in orders:
                                if o.get("action") == "buy":
                                    parts.append(f"买入{o.get('code')}")
                                elif o.get("action") == "sell":
                                    parts.append(f"卖出{o.get('code')}")
                            summary = "、".join(parts) if parts else "持有观望"
                        else:
                            summary = "持有观望"
                    decision_log.append(
                        {
                            "date": date,
                            "market_state": state,
                            "positions_before": snapshot["positions"],
                            "orders": orders,
                            "analysis": trace,
                            "summary": summary,
                            "reasoning": reasoning,
                        }
                    )
                except Exception as e:  # noqa: BLE001
                    account.trades.append({"date": date, "action": "decide_error", "code": "", "reason": str(e)})

            # 7. 进度回调
            if progress_cb:
                positions_detail = []
                for code, pos in account.positions.items():
                    px = closes.get(code, pos.avg_cost)
                    positions_detail.append(
                        {
                            "code": code,
                            "quantity": pos.quantity,
                            "avg_cost": round(pos.avg_cost, 2),
                            "price": round(px, 2),
                            "pnl_pct": round((px / pos.avg_cost - 1) * 100, 2) if pos.avg_cost else 0.0,
                            "market_value": round(pos.quantity * px, 2),
                        }
                    )
                progress_cb(
                    {
                        "day_index": i + 1,
                        "total_days": len(days),
                        "date": date,
                        "market_state": state,
                        "positions": len(account.positions),
                        "decisions": len(decision_log),
                        "last_decision": decision_log[-1] if decision_log else None,
                        "decision_log": decision_log,
                        "equity_curve": account.equity_history,
                        "trades": account.trades,
                        "positions_detail": positions_detail,
                        "cash": round(account.cash, 2),
                        "total_value": round(account.total_value(closes), 2),
                    }
                )

        # 期末持仓快照：用于报告里「持仓中」个股的浮动盈亏与综合收益率计算
        final_positions = []
        for code, pos in account.positions.items():
            px = closes.get(code, pos.avg_cost)
            final_positions.append(
                {
                    "code": code,
                    "quantity": pos.quantity,
                    "avg_cost": round(pos.avg_cost, 2),
                    "price": round(px, 2),
                    "pnl_pct": round((px / pos.avg_cost - 1) * 100, 2) if pos.avg_cost else 0.0,
                    "market_value": round(pos.quantity * px, 2),
                }
            )

        # 沪深300 基准曲线（与权益曲线按日期对齐，用于超额收益）
        benchmark_curve = []
        for e in account.equity_history:
            close = self.index_close(e["date"])
            if close is not None:
                benchmark_curve.append({"date": e["date"], "total": close})

        metrics = compute_metrics(account.equity_history, benchmark_curve, account.trades)
        return {
            "account": account,
            "metrics": metrics,
            "equity_curve": account.equity_history,
            "benchmark_curve": benchmark_curve,
            "trades": account.trades,
            "decision_log": decision_log,
            "final_positions": final_positions,
            "trading_days": len(days),
        }

    def _execute(
        self,
        order: dict,
        account: Account,
        date: str,
        opens: dict,
        prev_closes: dict,
        volumes: dict,
        st_flags: dict,
        industries: dict | None = None,
    ) -> None:
        action = order.get("action")
        code = order.get("code", "")
        if code not in opens:
            # 标的不存在或当日无行情（停牌/无数据），明确回填拒绝原因，避免静默丢弃
            order["rejected"] = "无行情数据（标的不存在或当日停牌）"
            return
        price = opens[code]
        prev_close = prev_closes.get(code)
        is_st = bool(st_flags.get(code))
        industries = industries or {}
        result: dict = {}
        if action == "buy":
            limit_up = self._at_limit(price, prev_close, limit_pct(code, is_st), "up")
            cash_amount = order.get("cash_amount", 0)
            total = account.total_value(opens)
            # 持仓只数上限（仅限制新开仓；对已持仓的加仓不限制）
            if code not in account.positions and len(account.positions) >= self.policy.max_holdings:
                order["rejected"] = f"持仓只数已达上限 {self.policy.max_holdings}"
                return
            # 单只上限（按市场状态分档：single_caps 优先，否则回退 max_single_pct）
            cur_value = account.position_value(code, price)
            max_single = max(0.0, total * self.policy.single_cap_for(self.current_state) - cur_value)
            # 总仓位上限：市场状态上限 与 强制保留现金 取更严者
            total_cap_pct = min(self.current_max_pos, 1.0 - self.policy.min_cash_pct)
            invested = total - account.cash
            max_total = max(0.0, total * total_cap_pct - invested)
            # 行业集中度上限（如「同一行业持仓≤40%」）
            max_industry: float | None = None
            if self.policy.max_industry_pct is not None:
                ind = industries.get(code)
                if ind:
                    ind_value = cur_value
                    for c2, p2 in account.positions.items():
                        if c2 != code and industries.get(c2) == ind:
                            ind_value += p2.quantity * opens.get(c2, p2.avg_cost)
                    max_industry = max(0.0, total * self.policy.max_industry_pct - ind_value)
            original_amount = cash_amount
            bounds = [cash_amount, max_single, max_total] + ([max_industry] if max_industry is not None else [])
            cash_amount = min(bounds)
            if cash_amount < original_amount:
                order["adjusted"] = {
                    "reason": "单只/总仓位/行业/保留现金上限",
                    "from": round(original_amount, 2),
                    "to": round(cash_amount, 2),
                }
            result = self.broker.buy(
                account, code, date, price, cash_amount, limit_up, day_volume=volumes.get(code)
            )
        elif action == "sell":
            limit_down = self._at_limit(price, prev_close, limit_pct(code, is_st), "down")
            result = self.broker.sell(
                account, code, date, price, order.get("ratio", 1.0), limit_down, reason=order.get("reason")
            )
        # 成交后把实际成交价/金额回填到订单，使决策过程能看到买卖价格
        if result.get("status") == "filled":
            order["exec_price"] = result.get("price")
            order["quantity"] = result.get("quantity")
            order["amount"] = result.get("amount")
            # 流动性约束导致的减量：显式记录，避免「想买多少、实际只买成多少」不可追溯
            if result.get("liquidity_capped"):
                order["adjusted"] = {
                    "reason": "流动性约束（单笔买入不超过当日成交量10%）",
                    "from": round(order.get("cash_amount", 0), 2),
                    "to": round(result.get("amount", 0), 2),
                }
        elif result.get("status") == "rejected":
            order["rejected"] = result.get("reason")

    @staticmethod
    def _at_limit(price: float, prev_close: float | None, limit_pct_val: float, side: str) -> bool:
        """用开盘价相对昨收判断是否触及涨/跌停（开盘成交视角，避免用当日全天涨跌幅的
        未来数据）。prev_close 缺失（如长期停牌后首日）时按不封板处理。"""
        if prev_close is None or prev_close <= 0 or price is None:
            return False
        threshold = 1 + (limit_pct_val / 100) if side == "up" else 1 - (limit_pct_val / 100)
        if side == "up":
            return price >= prev_close * threshold
        return price <= prev_close * threshold

    def _execution_note(self) -> str:
        """把下单价格口径显式告诉 LLM，避免「收盘看盘、次日开盘成交」造成误解。"""
        if self.policy.order_price == "close":
            return "本决策日按当日收盘价成交（含滑点）。"
        return "本决策日收盘后决策，订单于下一交易日开盘价成交（含滑点），成交价可能因隔夜跳空偏离你看到的收盘价。"

    def _snapshot(self, account: Account, date: str, closes: dict) -> dict:
        positions = []
        for code, pos in account.positions.items():
            price = closes.get(code, pos.avg_cost)
            positions.append(
                {
                    "code": code,
                    "quantity": pos.quantity,
                    "avg_cost": round(pos.avg_cost, 2),
                    "price": round(price, 2),
                    "pnl_pct": round((price / pos.avg_cost - 1) * 100, 2) if pos.avg_cost else 0.0,
                    "market_value": round(pos.quantity * price, 2),
                }
            )
        return {
            "date": date,
            "cash": round(account.cash, 2),
            "positions": positions,
            "total_value": round(account.total_value(closes), 2),
            "index_close": self.index_close(date),
        }
