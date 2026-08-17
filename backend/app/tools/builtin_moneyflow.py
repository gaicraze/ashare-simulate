"""内置工具·资金流类：个股主力资金净流入 / 全市场主力资金排名。"""
from __future__ import annotations

from typing import Any

from . import db
from .base import Tool


def _round_row(r: dict) -> dict:
    """金额按亿元展示（保留 4 位小数），便于 LLM 与看板阅读。"""
    out = dict(r)
    for k in ("main_net_inflow", "super_net_inflow", "large_net_inflow", "main_net", "super_net", "large_net"):
        v = out.get(k)
        if v is not None:
            out[k] = round(v / 1e8, 4)  # 元 -> 亿元
    return out


class GetStockMoneyflow(Tool):
    name = "get_stock_moneyflow"
    description = (
        "查询个股近 N 个交易日的主力资金净流入（亿元），含超大单/大单拆分与累计值。"
        "主力 = 超大单 + 大单。正数=主力净流入（资金抢筹），负数=主力净流出。"
        "date 必须传当前决策日，避免使用未来数据。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "6位股票代码"},
            "date": {"type": "string", "description": "截止日期 YYYY-MM-DD（回测必须传决策日）"},
            "days": {"type": "integer", "description": "统计窗口交易日数，默认20，最大120"},
        },
        "required": ["code", "date"],
    }

    def execute(self, code: str, date: str, days: int = 20, **kwargs: Any) -> dict[str, Any]:
        days = min(max(int(days), 1), 120)
        rows = db.rows(
            """
            SELECT trade_date, main_net_inflow, super_net_inflow, large_net_inflow
            FROM moneyflow
            WHERE code = ? AND trade_date <= ?
            ORDER BY trade_date DESC LIMIT ?
            """,
            [code, date, days],
        )
        if not rows:
            return {"code": code, "date": date, "error": "无资金流数据（可能尚未回填）"}

        main_sum = sum(r["main_net_inflow"] or 0 for r in rows)
        super_sum = sum(r["super_net_inflow"] or 0 for r in rows)
        large_sum = sum(r["large_net_inflow"] or 0 for r in rows)
        # 近 N 日主力净流入为正的天数
        main_in_days = sum(1 for r in rows if (r["main_net_inflow"] or 0) > 0)
        return {
            "code": code,
            "date": date,
            "days": days,
            "main_net_inflow_sum_yi": round(main_sum / 1e8, 4),
            "super_net_inflow_sum_yi": round(super_sum / 1e8, 4),
            "large_net_inflow_sum_yi": round(large_sum / 1e8, 4),
            "main_inflow_days": main_in_days,
            "rows": [_round_row(r) for r in rows],
        }


class GetMoneyflowRank(Tool):
    name = "get_moneyflow_rank"
    description = (
        "按近 N 个交易日的主力资金累计净流入对全市场股票排名，返回净流入最多的前 N 只。"
        "用于识别近期被主力资金持续加仓的强势股。date 必须传当前决策日。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "截止日期 YYYY-MM-DD（回测必须传决策日）"},
            "days": {"type": "integer", "description": "统计窗口交易日数，默认20，最大120"},
            "top_n": {"type": "integer", "description": "返回前 N 只，默认20，最大100"},
        },
        "required": ["date"],
    }

    def execute(self, date: str, days: int = 20, top_n: int = 20, **kwargs: Any) -> dict[str, Any]:
        days = min(max(int(days), 1), 120)
        top_n = min(max(int(top_n), 1), 100)
        rows = db.rows(
            """
            WITH recent AS (
                SELECT DISTINCT trade_date FROM moneyflow
                WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT ?
            )
            SELECT m.code, SUM(m.main_net_inflow) AS main_net,
                   SUM(m.super_net_inflow) AS super_net, SUM(m.large_net_inflow) AS large_net
            FROM moneyflow m
            JOIN recent r ON m.trade_date = r.trade_date
            GROUP BY m.code
            ORDER BY main_net DESC NULLS LAST LIMIT ?
            """,
            [date, days, top_n],
        )
        return {
            "date": date,
            "days": days,
            "rows": [_round_row(r) for r in rows],
            "count": len(rows),
        }
