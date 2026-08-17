"""回测账户与持仓。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Position:
    code: str
    quantity: int          # 股数
    avg_cost: float        # 平均成本（含买入手续费摊薄）
    buy_date: str          # 最近一次买入日期，用于 T+1 判断


class Account:
    def __init__(self, initial_cash: float):
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.positions: dict[str, Position] = {}
        self.trades: list[dict] = []
        self.equity_history: list[dict] = []

    def position_value(self, code: str, price: float) -> float:
        p = self.positions.get(code)
        return p.quantity * price if p else 0.0

    def total_value(self, prices: dict[str, float]) -> float:
        mv = sum(self.position_value(c, prices.get(c, 0.0)) for c in self.positions)
        return self.cash + mv

    def record_equity(self, date: str, prices: dict[str, float]) -> None:
        total = self.total_value(prices)
        n_pos = len(self.positions)
        self.equity_history.append(
            {"date": date, "cash": round(self.cash, 2), "market_value": round(total - self.cash, 2), "total": round(total, 2), "positions": n_pos}
        )

    def unrealized_pnl(self, prices: dict[str, float]) -> float:
        pnl = 0.0
        for c, p in self.positions.items():
            pnl += (prices.get(c, p.avg_cost) - p.avg_cost) * p.quantity
        return pnl
