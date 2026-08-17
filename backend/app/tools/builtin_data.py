"""内置工具·数据查询类。"""
from __future__ import annotations

from typing import Any

from . import db
from .base import Tool


class GetStockDaily(Tool):
    name = "get_stock_daily"
    description = (
        "查询个股日线行情（开高低收、成交量、成交额、涨跌幅、换手率、市盈率PE、市净率PB），"
        "按日期倒序返回。用于查看某只股票的历史走势与量价数据。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "6位股票代码，如 600519"},
            "start": {"type": "string", "description": "起始日期 YYYY-MM-DD，可选"},
            "end": {"type": "string", "description": "结束日期 YYYY-MM-DD，可选"},
            "limit": {"type": "integer", "description": "返回条数，默认60，最大500"},
        },
        "required": ["code"],
    }

    def execute(
        self, code: str, start: str | None = None, end: str | None = None, limit: int = 60
    ) -> dict[str, Any]:
        conds = ["code = ?"]
        params: list[Any] = [code]
        if start:
            conds.append("trade_date >= ?")
            params.append(start)
        if end:
            conds.append("trade_date <= ?")
            params.append(end)
        limit = min(max(int(limit), 1), 500)
        sql = (
            "SELECT code, trade_date, open, high, low, close, volume, amount, "
            "pct_change, turnover, pe_ttm, pb_mrq FROM daily "
            f"WHERE {' AND '.join(conds)} ORDER BY trade_date DESC LIMIT ?"
        )
        params.append(limit)
        return {"rows": db.rows(sql, params)}


class GetIndexDaily(Tool):
    name = "get_index_daily"
    description = (
        "查询大盘/指数的日线行情（开高低收、成交量、成交额）及5/10/20/60日均线，"
        "按日期倒序返回。用于判断大盘趋势与择时条件（如 MA20 是否在 MA60 上方、MA20 斜率、"
        "指数是否站上/跌破均线等）。数据湖当前仅收录沪深300（代码 000300）。"
        "注意：个股日线请用 get_stock_daily，本工具只查指数、查不到 ETF。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "指数代码，默认 000300（沪深300）；数据湖当前仅有 000300"},
            "start": {"type": "string", "description": "起始日期 YYYY-MM-DD，可选"},
            "end": {"type": "string", "description": "结束日期 YYYY-MM-DD，回测时必须传当前决策日（缺省为最新交易日）"},
            "limit": {"type": "integer", "description": "返回条数，默认60，最大250"},
        },
        "required": [],
    }

    def execute(
        self, code: str = "000300", start: str | None = None, end: str | None = None, limit: int = 60, **kwargs: Any
    ) -> dict[str, Any]:
        code = code or "000300"
        limit = min(max(int(limit), 1), 250)
        # 内层窗口函数在全历史（截至 end）上算均线，避免 start 截断历史导致 MA 失真；
        # 外层再按 start / limit 过滤，保证返回的均线值准确。
        inner = ["code = ?"]
        inner_params: list[Any] = [code]
        if end:
            inner.append("trade_date <= ?")
            inner_params.append(end)
        outer = []
        outer_params: list[Any] = []
        if start:
            outer.append("trade_date >= ?")
            outer_params.append(start)
        sql = (
            "SELECT trade_date, open, high, low, close, volume, amount, ma5, ma10, ma20, ma60 FROM ("
            "  SELECT trade_date, open, high, low, close, volume, amount, "
            "    ROUND(AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW), 3) AS ma5, "
            "    ROUND(AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 9 PRECEDING AND CURRENT ROW), 3) AS ma10, "
            "    ROUND(AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW), 3) AS ma20, "
            "    ROUND(AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW), 3) AS ma60 "
            f"  FROM indices WHERE {' AND '.join(inner)}"
            ") t"
            + ((" WHERE " + " AND ".join(outer)) if outer else "")
            + " ORDER BY trade_date DESC LIMIT ?"
        )
        params = inner_params + outer_params + [limit]
        rows = db.rows(sql, params)
        for r in rows:
            r["trade_date"] = str(r["trade_date"])
        return {"code": code, "rows": rows}


class GetStockList(Tool):
    name = "get_stock_list"
    description = (
        "返回数据湖中的股票代码列表（按代码排序）。count 为全市场股票总数，"
        "codes 默认只返回前 200 只，可用 limit 调整（最大 500）。"
        "如需精确筛选请使用 screen_by_fundamentals / get_rps_rank 等筛选工具，避免一次性拉取全部代码。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "返回代码数量上限，默认200，最大500"},
        },
        "required": [],
    }

    def execute(self, limit: int = 200, **kwargs: Any) -> dict[str, Any]:
        codes = [r["code"] for r in db.rows("SELECT code FROM stocks ORDER BY code")]
        total = len(codes)
        limit = min(max(int(limit), 1), 500)
        return {"count": total, "codes": codes[:limit], "truncated": total > limit}


class GetLatestTradeDate(Tool):
    name = "get_latest_trade_date"
    description = "返回数据湖中最新的交易日，用于确定当前可用的数据截止日期。"
    parameters = {"type": "object", "properties": {}, "required": []}

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        r = db.one("SELECT MAX(trade_date) AS d FROM daily")
        return {"latest_trade_date": str(r["d"]) if r and r["d"] else None}


class GetMarketSnapshot(Tool):
    name = "get_market_snapshot"
    description = (
        "返回某交易日的全市场快照：上涨/下跌家数、涨停/跌停家数、平均涨跌幅、"
        "成交额合计等，用于判断当日市场整体强弱与情绪。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "交易日 YYYY-MM-DD，缺省为最新交易日"},
        },
        "required": [],
    }

    def execute(self, date: str | None = None, **kwargs: Any) -> dict[str, Any]:
        if date is None:
            r = db.one("SELECT MAX(trade_date) AS d FROM daily")
            date = str(r["d"]) if r and r["d"] else None
        if not date:
            return {"error": "数据湖为空"}
        snap = db.one(
            """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN pct_change > 0 THEN 1 ELSE 0 END) AS up,
              SUM(CASE WHEN pct_change < 0 THEN 1 ELSE 0 END) AS down,
              SUM(CASE WHEN pct_change >= 9.9 THEN 1 ELSE 0 END) AS limit_up,
              SUM(CASE WHEN pct_change <= -9.9 THEN 1 ELSE 0 END) AS limit_down,
              ROUND(AVG(pct_change), 3) AS avg_pct,
              ROUND(SUM(amount), 0) AS total_amount
            FROM daily WHERE trade_date = ?
            """,
            [date],
        )
        if snap:
            snap["date"] = date
        return {"snapshot": snap}
