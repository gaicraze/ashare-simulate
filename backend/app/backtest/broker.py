"""A 股撮合：T+1、涨跌停、手续费、印花税、滑点、100 股整数倍。"""
from __future__ import annotations

from .account import Account, Position


class AShareBroker:
    def __init__(
        self,
        commission_rate: float = 0.0003,   # 佣金 万3
        min_commission: float = 5.0,       # 最低佣金 5 元
        stamp_tax: float = 0.001,          # 印花税（卖出）千1
        slippage: float = 0.001,           # 滑点 千1
        max_participation: float = 0.10,   # 单笔买入最多占当日成交量比例（流动性约束）
    ):
        self.commission_rate = commission_rate
        self.min_commission = min_commission
        self.stamp_tax = stamp_tax
        self.slippage = slippage
        self.max_participation = max_participation

    def buy(
        self,
        account: Account,
        code: str,
        date: str,
        price: float,
        cash_amount: float,
        limit_up: bool = False,
        day_volume: float | None = None,
    ) -> dict:
        """用指定现金买入。返回成交信息。

        day_volume：当日成交量（股）。非空时对买入数量施加「单笔最多占当日成交量
        max_participation」的流动性约束，避免策略在流动性差的标的上买到现实中买不到的量。
        """
        if limit_up:
            return {"status": "rejected", "reason": "涨停无法买入"}
        if price <= 0:
            return {"status": "rejected", "reason": "无价格"}
        cash_amount = min(cash_amount, account.cash)
        exec_price = price * (1 + self.slippage)
        commission = max(cash_amount * self.commission_rate, self.min_commission)
        net = cash_amount - commission
        qty = int(net / exec_price / 100) * 100
        if qty <= 0:
            return {"status": "rejected", "reason": "资金不足"}
        # 流动性约束：单笔买入股数不超过当日成交量的 max_participation（向下取整到 100 股）
        liquidity_capped = False
        if day_volume is not None and day_volume > 0:
            max_qty = int(day_volume * self.max_participation / 100) * 100
            if max_qty <= 0:
                return {"status": "rejected", "reason": "成交量不足（流动性不足，无法成交）"}
            if qty > max_qty:
                qty = max_qty
                liquidity_capped = True
        cost = qty * exec_price + commission
        account.cash -= cost

        pos = account.positions.get(code)
        if pos:
            total_qty = pos.quantity + qty
            pos.avg_cost = (pos.avg_cost * pos.quantity + cost) / total_qty
            pos.quantity = total_qty
            pos.buy_date = date
        else:
            account.positions[code] = Position(code=code, quantity=qty, avg_cost=cost / qty, buy_date=date)

        trade = {"date": date, "action": "buy", "code": code, "price": round(exec_price, 4), "quantity": qty, "amount": round(cost, 2), "commission": round(commission, 2), "summary": f"买入 {code} {qty}股 @{round(exec_price, 2)}，金额 {round(cost, 0)} 元"}
        account.trades.append(trade)
        return {"status": "filled", "liquidity_capped": liquidity_capped, **trade}

    def sell(
        self,
        account: Account,
        code: str,
        date: str,
        price: float,
        ratio: float = 1.0,
        limit_down: bool = False,
        reason: str | None = None,
    ) -> dict:
        """卖出持仓的指定比例（ratio=1 清仓）。返回成交信息。

        reason：卖出原因（如「系统强制止损」「熊市强清」），用于区分
        系统强制卖出与策略主动卖出，便于审计「严格落实」是否到位。
        """
        pos = account.positions.get(code)
        if not pos or pos.quantity <= 0:
            return {"status": "rejected", "reason": "无持仓"}
        if limit_down:
            return {"status": "rejected", "reason": "跌停无法卖出"}
        if pos.buy_date == date:
            return {"status": "rejected", "reason": "T+1 当日买入不可卖出"}

        exec_price = price * (1 - self.slippage)
        if ratio >= 1:
            qty = pos.quantity
        else:
            qty = int(pos.quantity * ratio / 100) * 100
            if qty <= 0:
                return {"status": "rejected", "reason": "卖出股数不足100"}
            qty = min(qty, pos.quantity)

        gross = qty * exec_price
        commission = max(gross * self.commission_rate, self.min_commission)
        stamp = gross * self.stamp_tax
        net = gross - commission - stamp
        account.cash += net

        # 已实现盈亏 = 卖出净额 - 成本（avg_cost 已含买入佣金摊薄）
        pnl = net - pos.avg_cost * qty

        pos.quantity -= qty
        if pos.quantity <= 0:
            del account.positions[code]

        trade = {"date": date, "action": "sell", "code": code, "price": round(exec_price, 4), "quantity": qty, "amount": round(net, 2), "pnl": round(pnl, 2), "commission": round(commission, 2), "stamp_tax": round(stamp, 2), "summary": f"卖出 {code} {qty}股 @{round(exec_price, 2)}，金额 {round(net, 0)} 元"}
        if reason:
            trade["reason"] = reason
        account.trades.append(trade)
        return {"status": "filled", **trade}
