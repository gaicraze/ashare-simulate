"""内置工具·分析类：市场环境 / 量价 / 基本面 / 排名。"""
from __future__ import annotations

from typing import Any

from . import db
from .base import Tool

# (date, days) → 全市场 RPS 排名表（按 RPS 降序），带 rank/total。
# 缓存避免 get_stock_profile / get_rps_rank 每次调用都做全市场扫描。
_RPS_CACHE: dict[tuple[str, int], list[dict]] = {}
_RPS_CACHE_MAX = 8


def _rps_rank_table(date: str, days: int) -> list[dict]:
    """返回 (date, days) 下的全市场 RPS 排名（按 RPS 降序，含 rank/total）。"""
    key = (date, days)
    cached = _RPS_CACHE.get(key)
    if cached is not None:
        return cached
    rows = db.rows(
        """
        SELECT a.code, (a.close / b.close - 1) * 100 AS pct_change
        FROM daily a
        JOIN (
            SELECT code, close,
                   ROW_NUMBER() OVER (PARTITION BY code ORDER BY trade_date DESC) AS rn
            FROM daily WHERE trade_date <= ?
        ) b ON a.code = b.code AND b.rn = ?
        WHERE a.trade_date = ?
        """,
        [date, days + 1, date],
    )
    n = len(rows)
    rows_sorted = sorted(rows, key=lambda r: r["pct_change"], reverse=True)
    table = []
    for i, r in enumerate(rows_sorted):
        table.append(
            {
                "code": r["code"],
                "pct_change": round(r["pct_change"], 2),
                "rps": round((1 - i / n) * 100, 1) if n else 0.0,
                "rank": i + 1,
                "total": n,
            }
        )
    if len(_RPS_CACHE) >= _RPS_CACHE_MAX:
        _RPS_CACHE.pop(next(iter(_RPS_CACHE)))
    _RPS_CACHE[key] = table
    return table


class GetMarketRegime(Tool):
    name = "get_market_regime"
    description = (
        "判断指定日期的A股市场整体环境（真bull/温和看多/熊市/震荡市），"
        "依据沪深300指数的收盘价与20/60/120日均线：真bull=收盘站上20日线且MA20已在MA60上方连续≥3日；"
        "温和看多=站上20日线但趋势未确认；熊市=收盘跌破20日线且MA20在MA60下方。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "判断日期 YYYY-MM-DD，缺省为最新交易日"},
        },
        "required": [],
    }

    def execute(self, date: str | None = None, **kwargs: Any) -> dict[str, Any]:
        if date is None:
            r = db.one("SELECT MAX(trade_date) AS d FROM indices")
            date = str(r["d"]) if r and r["d"] else None
        rows = db.rows(
            """
            SELECT trade_date, close,
              AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS ma20,
              AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) AS ma60,
              AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 119 PRECEDING AND CURRENT ROW) AS ma120
            FROM indices
            WHERE code = '000300' AND trade_date <= ?
            ORDER BY trade_date DESC LIMIT 3
            """,
            [date],
        )
        if not rows or rows[0]["close"] is None:
            return {"regime": "未知", "date": date, "reason": "无指数数据"}

        top = rows[0]
        close, ma20, ma60, ma120 = top["close"], top["ma20"], top["ma60"], top["ma120"]
        # MA20 在 MA60 上方连续天数（含当日，最多看3日）
        consec = 0
        for r in rows:
            if r["ma20"] is None or r["ma60"] is None or r["ma20"] <= r["ma60"]:
                break
            consec += 1

        if ma20 and ma60 and close is not None:
            if close > ma20 and ma20 > ma60 and consec >= 3:
                regime = "真bull"
            elif close > ma20:
                regime = "温和看多"
            elif close < ma20 and ma20 < ma60:
                regime = "熊市"
            else:
                regime = "震荡市"
        else:
            regime = "数据不足(上市初期)"
        return {
            "regime": regime,
            "date": str(top["trade_date"]),
            "index_close": close,
            "ma20": round(ma20, 2) if ma20 else None,
            "ma60": round(ma60, 2) if ma60 else None,
            "ma120": round(ma120, 2) if ma120 else None,
        }


class AnalyzePriceVolume(Tool):
    name = "analyze_price_volume"
    description = (
        "对个股做量价技术分析：计算5/10/20/60日均线、当前价相对均线位置、"
        "近5/20日累计涨跌幅、量比（当日成交量/近20日均量）、最新换手率。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "6位股票代码"},
            "days": {"type": "integer", "description": "分析窗口天数，默认60，最大250"},
            "date": {"type": "string", "description": "截止日期 YYYY-MM-DD，只分析该日期及之前的数据（回测时必须传决策日）"},
        },
        "required": ["code"],
    }

    def execute(self, code: str, days: int = 60, date: str | None = None, **kwargs: Any) -> dict[str, Any]:
        days = min(max(int(days), 20), 250)
        if date:
            rows = db.rows(
                """
                SELECT * FROM (
                  SELECT trade_date, close, volume, pct_change, turnover
                  FROM daily WHERE code = ? AND trade_date <= ?
                  ORDER BY trade_date DESC LIMIT ?
                ) ORDER BY trade_date ASC
                """,
                [code, date, days],
            )
        else:
            rows = db.rows(
                """
                SELECT * FROM (
                  SELECT trade_date, close, volume, pct_change, turnover
                  FROM daily WHERE code = ? ORDER BY trade_date DESC LIMIT ?
                ) ORDER BY trade_date ASC
                """,
                [code, days],
            )
        if not rows:
            return {"code": code, "error": "无数据"}

        closes = [r["close"] for r in rows]
        volumes = [r["volume"] or 0 for r in rows]
        last = rows[-1]

        def ma(n: int) -> float | None:
            if len(closes) < n:
                return None
            return round(sum(closes[-n:]) / n, 2)

        ma5, ma10, ma20, ma60 = ma(5), ma(10), ma(20), ma(60)
        close = closes[-1]

        # 近5/20日涨跌幅
        def pct_over(n: int) -> float | None:
            if len(closes) < n + 1:
                return None
            return round((closes[-1] / closes[-n - 1] - 1) * 100, 2)

        # 量比：最近一日成交量 / 前20日均量
        vol_ratio = None
        if len(volumes) >= 21 and sum(volumes[-21:-1]) > 0:
            vol_ratio = round(volumes[-1] / (sum(volumes[-21:-1]) / 20), 2)

        return {
            "code": code,
            "date": str(last["trade_date"]),
            "close": close,
            "ma5": ma5,
            "ma10": ma10,
            "ma20": ma20,
            "ma60": ma60,
            "close_vs_ma20_pct": round((close / ma20 - 1) * 100, 2) if ma20 else None,
            "pct_5d": pct_over(5),
            "pct_20d": pct_over(20),
            "volume_ratio": vol_ratio,
            "turnover": last["turnover"],
        }


class ScreenByFundamentals(Tool):
    name = "screen_by_fundamentals"
    description = (
        "按基本面指标筛选股票：对每只股票取其最新一期财务报告，"
        "按 ROE、净利率、归母净利同比等条件过滤，返回按 ROE 降序的前 N 只。"
        "注意：三个阈值均为「百分比」单位，例如 min_roe=5 表示 ROE≥5%（与 get_stock_profile 返回的百分比口径一致）。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "min_roe": {"type": "number", "description": "最小ROE（百分比，5=5%），可选"},
            "min_net_margin": {"type": "number", "description": "最小净利率（百分比，5=5%），可选"},
            "min_yoy_profit": {"type": "number", "description": "最小归母净利同比（百分比，5=5%），可选"},
            "top_n": {"type": "integer", "description": "返回前 N 只，默认50，最大200"},
            "date": {"type": "string", "description": "截止日期 YYYY-MM-DD，只用该日期之前已披露的财报（回测时必须传决策日）"},
        },
        "required": [],
    }

    def execute(
        self,
        min_roe: float | None = None,
        min_net_margin: float | None = None,
        min_yoy_profit: float | None = None,
        top_n: int = 50,
        date: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        conds = ["rn = 1"]
        params: list[Any] = []
        if min_roe is not None:
            conds.append("roe >= ?")
            params.append(float(min_roe) / 100.0)
        if min_net_margin is not None:
            conds.append("net_profit_margin >= ?")
            params.append(float(min_net_margin) / 100.0)
        if min_yoy_profit is not None:
            conds.append("yoy_net_profit >= ?")
            params.append(float(min_yoy_profit) / 100.0)
        top_n = min(max(int(top_n), 1), 200)

        if date:
            inner_sql = "SELECT *, ROW_NUMBER() OVER (PARTITION BY code ORDER BY report_date DESC) AS rn FROM finances WHERE pub_date <= ?"
            base_params: list[Any] = [date]
        else:
            inner_sql = "SELECT *, ROW_NUMBER() OVER (PARTITION BY code ORDER BY report_date DESC) AS rn FROM finances"
            base_params = []

        sql = f"""
            SELECT code, report_date, roe, net_profit_margin, gross_margin,
                   eps_ttm, yoy_net_profit, net_profit, revenue
            FROM (
              {inner_sql}
            ) t
            WHERE {' AND '.join(conds)}
            ORDER BY roe DESC NULLS LAST LIMIT ?
        """
        all_params = base_params + params + [top_n]
        result = db.rows(sql, all_params)
        return {"rows": result, "count": len(result)}


class RankByMetric(Tool):
    name = "rank_by_metric"
    description = (
        "按指定指标对全市场股票排名：pe_ttm(市盈率,升序=低估)、pb_mrq(市净率,升序=低估)、"
        "turnover(换手率,降序=活跃)。返回某交易日的前 N 只。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "metric": {"type": "string", "enum": ["pe_ttm", "pb_mrq", "turnover"], "description": "排名指标"},
            "date": {"type": "string", "description": "交易日 YYYY-MM-DD，缺省为最新交易日"},
            "top_n": {"type": "integer", "description": "返回前 N 只，默认20，最大100"},
        },
        "required": ["metric"],
    }

    def execute(self, metric: str, date: str | None = None, top_n: int = 20, **kwargs: Any) -> dict[str, Any]:
        if metric not in ("pe_ttm", "pb_mrq", "turnover"):
            return {"error": f"metric 必须是 pe_ttm/pb_mrq/turnover，收到 {metric}"}
        if date is None:
            r = db.one("SELECT MAX(trade_date) AS d FROM daily")
            date = str(r["d"]) if r and r["d"] else None
        top_n = min(max(int(top_n), 1), 100)
        order = "ASC" if metric in ("pe_ttm", "pb_mrq") else "DESC"
        # 估值指标只排正值（负PE/负PB表示亏损/净资产为负，无“低估”意义）
        positive = f"AND {metric} > 0" if metric in ("pe_ttm", "pb_mrq") else ""
        sql = f"""
            SELECT code, close, pct_change, turnover, pe_ttm, pb_mrq
            FROM daily
            WHERE trade_date = ? AND {metric} IS NOT NULL {positive}
            ORDER BY {metric} {order} NULLS LAST LIMIT ?
        """
        rows = db.rows(sql, [date, top_n])
        return {"metric": metric, "date": date, "order": order, "rows": rows}


class GetRpsRank(Tool):
    name = "get_rps_rank"
    description = (
        "计算个股 RPS（相对强度）：近 N 日涨幅在全市场的排名分位（0~100，越大越强）。"
        "RPS>85 表示近 N 日表现强于约 85% 的股票。返回 RPS 最高的前 N 只，或查询单只股票的 RPS。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "截止日期 YYYY-MM-DD（回测必须传决策日）"},
            "days": {"type": "integer", "description": "统计窗口交易日数，默认120（即RPS120）"},
            "top_n": {"type": "integer", "description": "返回 RPS 最高的前 N 只，默认20，最大100"},
            "code": {"type": "string", "description": "可选：查询指定股票的 RPS 值"},
        },
        "required": ["date"],
    }

    def execute(self, date: str, days: int = 120, top_n: int = 20, code: str | None = None, **kwargs: Any) -> dict[str, Any]:
        days = min(max(int(days), 5), 500)
        top_n = min(max(int(top_n), 1), 100)
        table = _rps_rank_table(date, days)
        if not table:
            return {"error": "无数据", "date": date}
        n = table[0]["total"]
        if code:
            for r in table:
                if r["code"] == code:
                    return {
                        "code": code,
                        "pct_change": r["pct_change"],
                        "rps": r["rps"],
                        "rank": r["rank"],
                        "total": n,
                        "date": date,
                        "days": days,
                    }
            return {"code": code, "error": "该股票无数据"}
        result = [
            {"code": r["code"], "pct_change": r["pct_change"], "rps": r["rps"]}
            for r in table[:top_n]
        ]
        return {"rows": result, "total": n, "date": date, "days": days}


class GetLimitUpInfo(Tool):
    name = "get_limit_up_info"
    description = (
        "统计个股近 N 个交易日出现涨停的次数与最近涨停日期。"
        "主板按涨幅≥9.8%计涨停，创业板/科创板按≥19.8%计。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "6位股票代码"},
            "date": {"type": "string", "description": "截止日期 YYYY-MM-DD（回测必须传决策日）"},
            "days": {"type": "integer", "description": "统计窗口交易日数，默认20"},
        },
        "required": ["code", "date"],
    }

    def execute(self, code: str, date: str, days: int = 20, **kwargs: Any) -> dict[str, Any]:
        days = min(max(int(days), 5), 120)
        limit = 19.8 if code.startswith(("300", "301", "688")) else 9.8
        sql = """
            SELECT COUNT(*) AS limit_up_count, MAX(trade_date) AS last_limit_up_date
            FROM (
                SELECT trade_date, pct_change,
                       ROW_NUMBER() OVER (ORDER BY trade_date DESC) AS rn
                FROM daily WHERE code = ? AND trade_date <= ?
            ) t
            WHERE rn <= ? AND pct_change >= ?
        """
        row = db.one(sql, [code, date, days, limit])
        return {
            "code": code,
            "date": date,
            "days": days,
            "limit_up_count": row["limit_up_count"] if row else 0,
            "last_limit_up_date": str(row["last_limit_up_date"]) if row and row["last_limit_up_date"] else None,
        }


class GetStockProfile(Tool):
    name = "get_stock_profile"
    description = (
        "综合查询个股的「龙头画像」：基本面（ROE/净利率/净利同比/每股收益）、估值（PE/PB）、"
        "量价（收盘价/20日均线/偏离/近20日涨幅/换手/量比）、RPS120（相对强度）。"
        "用于综合判断该股是否为值得买入的龙头——基本面不差、估值合理、趋势向上。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "6位股票代码"},
            "date": {"type": "string", "description": "截止日期 YYYY-MM-DD（回测必须传决策日）"},
        },
        "required": ["code", "date"],
    }

    def execute(self, code: str, date: str, **kwargs: Any) -> dict[str, Any]:
        fin = db.one(
            """
            SELECT roe, net_profit_margin, yoy_net_profit, eps_ttm
            FROM finances WHERE code = ? AND pub_date <= ?
            ORDER BY report_date DESC LIMIT 1
            """,
            [code, date],
        )
        val = db.one(
            """
            SELECT pe_ttm, pb_mrq
            FROM daily WHERE code = ? AND trade_date <= ?
            ORDER BY trade_date DESC LIMIT 1
            """,
            [code, date],
        )
        rows = db.rows(
            """
            SELECT * FROM (
              SELECT trade_date, close, volume, turnover
              FROM daily WHERE code = ? AND trade_date <= ?
              ORDER BY trade_date DESC LIMIT 60
            ) ORDER BY trade_date ASC
            """,
            [code, date],
        )
        pv: dict = {}
        if rows:
            closes = [r["close"] for r in rows]
            volumes = [r["volume"] or 0 for r in rows]
            last = rows[-1]
            ma20 = round(sum(closes[-20:]) / 20, 2) if len(closes) >= 20 else None
            close = closes[-1]
            pct20 = round((closes[-1] / closes[-21] - 1) * 100, 2) if len(closes) >= 21 else None
            vol_ratio = round(volumes[-1] / (sum(volumes[-21:-1]) / 20), 2) if len(volumes) >= 21 and sum(volumes[-21:-1]) > 0 else None
            pv = {
                "date": str(last["trade_date"]),
                "close": close,
                "ma20": ma20,
                "close_vs_ma20_pct": round((close / ma20 - 1) * 100, 2) if ma20 else None,
                "pct_20d": pct20,
                "volume_ratio": vol_ratio,
                "turnover": last["turnover"],
            }
        rps_val = None
        for r in _rps_rank_table(date, 120):
            if r["code"] == code:
                rps_val = r["rps"]
                break
        return {
            "code": code,
            "date": date,
            "fundamental": {
                "roe_pct": round(fin["roe"] * 100, 2) if fin and fin["roe"] is not None else None,
                "net_margin_pct": round(fin["net_profit_margin"] * 100, 2) if fin and fin["net_profit_margin"] is not None else None,
                "yoy_profit_pct": round(fin["yoy_net_profit"] * 100, 2) if fin and fin["yoy_net_profit"] is not None else None,
                "eps_ttm": fin["eps_ttm"] if fin else None,
            },
            "valuation": {
                "pe_ttm": round(val["pe_ttm"], 2) if val and val["pe_ttm"] is not None else None,
                "pb_mrq": round(val["pb_mrq"], 2) if val and val["pb_mrq"] is not None else None,
            },
            "price_volume": pv,
            "rps120": rps_val,
        }


class GetMarketSentiment(Tool):
    name = "get_market_sentiment"
    description = (
        "市场情绪温度计（消息面/情绪代理）：当日涨跌家数、涨跌停家数、连板高度（最高连续涨停板数）、"
        "涨跌家数比、涨停/跌停比、大涨(≥5%)/大跌(≤-5%)家数、总成交额及近 N 日环比。"
        "用于判断短线情绪强弱与市场热度。date 必须传当前决策日。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "交易日 YYYY-MM-DD（回测必须传决策日）"},
            "lookback": {"type": "integer", "description": "成交额环比的回看交易日数，默认5"},
        },
        "required": ["date"],
    }

    def execute(self, date: str, lookback: int = 5, **kwargs: Any) -> dict[str, Any]:
        lookback = min(max(int(lookback), 1), 20)
        snap = db.one(
            """
            SELECT COUNT(*) AS total,
              SUM(CASE WHEN pct_change > 0 THEN 1 ELSE 0 END) AS up,
              SUM(CASE WHEN pct_change < 0 THEN 1 ELSE 0 END) AS down,
              SUM(CASE WHEN pct_change >= 9.9 THEN 1 ELSE 0 END) AS limit_up,
              SUM(CASE WHEN pct_change <= -9.9 THEN 1 ELSE 0 END) AS limit_down,
              SUM(CASE WHEN pct_change >= 5 THEN 1 ELSE 0 END) AS up5,
              SUM(CASE WHEN pct_change <= -5 THEN 1 ELSE 0 END) AS down5,
              ROUND(SUM(amount), 0) AS amount
            FROM daily WHERE trade_date = ?
            """,
            [date],
        )
        if not snap or not snap.get("total"):
            return {"date": date, "error": "无当日行情数据"}

        recent = [str(r["trade_date"]) for r in db.rows(
            "SELECT DISTINCT trade_date FROM daily WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT ?",
            [date, lookback + 1],
        )]
        amount_prev = None
        if len(recent) >= 2:
            prev = db.one(
                "SELECT ROUND(SUM(amount), 0) AS a FROM daily WHERE trade_date = ?", [recent[-1]]
            )
            amount_prev = prev["a"] if prev else None

        # 连板高度：每只股票「截至当日」的连续涨停天数，取全市场最大值
        rows = db.rows(
            """
            WITH recent AS (
                SELECT DISTINCT trade_date FROM daily WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT ?
            )
            SELECT code, trade_date, pct_change FROM daily
            WHERE trade_date IN (SELECT trade_date FROM recent)
            """,
            [date, 15],
        )
        by_code: dict[str, dict[str, float]] = {}
        for r in rows:
            by_code.setdefault(r["code"], {})[str(r["trade_date"])] = r["pct_change"] or 0.0
        days_desc = sorted({str(r["trade_date"]) for r in rows}, reverse=True)
        max_streak = 0
        for code, dmap in by_code.items():
            limit = 19.8 if code.startswith(("300", "301", "688")) else 9.8
            streak = 0
            for d in days_desc:
                pct = dmap.get(d)
                if pct is None or pct < limit:
                    break
                streak += 1
            max_streak = max(max_streak, streak)

        up = snap["up"] or 0
        down = snap["down"] or 0
        limit_up = snap["limit_up"] or 0
        limit_down = snap["limit_down"] or 0
        amount = snap["amount"] or 0
        amount_change_pct = round((amount / amount_prev - 1) * 100, 2) if amount_prev else None
        return {
            "date": date,
            "up": up,
            "down": down,
            "up_down_ratio": round(up / down, 2) if down else None,
            "limit_up": limit_up,
            "limit_down": limit_down,
            "limit_up_down_ratio": round(limit_up / limit_down, 2) if limit_down else None,
            "max_limit_up_streak": max_streak,
            "up5": snap["up5"] or 0,
            "down5": snap["down5"] or 0,
            "amount_yi": round(amount / 1e8, 1),
            "amount_change_pct": amount_change_pct,
        }


class ScreenFundamentalTrend(Tool):
    name = "screen_fundamental_trend"
    description = (
        "按「基本面趋势改善」筛选：ROE 连续 N 个季度逐季上升、且归母净利同比连续 N 个季度为正、"
        "最新一期 ROE 不低于阈值。用于捕捉盈利能力持续改善、成长性确定的公司。"
        "date 必须传当前决策日（只用该日期之前已披露财报，避免未来数据）。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "截止日期 YYYY-MM-DD（回测必须传决策日）"},
            "periods": {"type": "integer", "description": "考察的连续季度数，默认4，最小2，最大8"},
            "min_roe": {"type": "number", "description": "最新一期最小ROE（百分比，8=8%），默认8"},
            "top_n": {"type": "integer", "description": "返回前 N 只，默认50，最大200"},
        },
        "required": ["date"],
    }

    def execute(self, date: str, periods: int = 4, min_roe: float = 8, top_n: int = 50, **kwargs: Any) -> dict[str, Any]:
        periods = min(max(int(periods), 2), 8)
        min_roe_dec = float(min_roe) / 100.0
        top_n = min(max(int(top_n), 1), 200)
        rows = db.rows(
            """
            WITH ranked AS (
                SELECT code, report_date, roe, yoy_net_profit,
                       ROW_NUMBER() OVER (PARTITION BY code ORDER BY report_date DESC) AS rn
                FROM finances
                WHERE pub_date <= ?
            ),
            nperiod AS (
                SELECT code, roe, yoy_net_profit, rn,
                       LAG(roe) OVER (PARTITION BY code ORDER BY rn) AS prev_roe
                FROM ranked WHERE rn <= ?
            ),
            agg AS (
                SELECT code,
                       MAX(CASE WHEN rn = 1 THEN roe END) AS latest_roe,
                       COUNT(*) AS n,
                       SUM(CASE WHEN rn = 1 OR (prev_roe IS NOT NULL AND roe < prev_roe) THEN 1 ELSE 0 END) AS rising_cnt,
                       SUM(CASE WHEN COALESCE(yoy_net_profit, 0) > 0 THEN 1 ELSE 0 END) AS yoy_pos_cnt
                FROM nperiod
                GROUP BY code
            )
            SELECT code, latest_roe, n
            FROM agg
            WHERE n = ? AND rising_cnt = n AND yoy_pos_cnt = n AND latest_roe >= ?
            ORDER BY latest_roe DESC NULLS LAST LIMIT ?
            """,
            [date, periods, periods, min_roe_dec, top_n],
        )
        result = [
            {
                "code": r["code"],
                "roe_pct": round(r["latest_roe"] * 100, 2),
                "periods": r["n"],
            }
            for r in rows
        ]
        return {"rows": result, "count": len(result), "date": date, "periods": periods, "min_roe": min_roe}


class ScreenQualityLeaders(Tool):
    name = "screen_quality_leaders"
    description = (
        "一键筛选「优质龙头」候选：基本面过关（ROE 达标）+ 估值合理（PE 为正且不超过上限）"
        "+ 趋势向上（收盘价站上 20 日线、近 20 日上涨）+ 相对强度高（RPS120）。"
        "按 RPS120 降序返回前 N 只，每只含 ROE/PE/收盘价/偏离20日线/近5日涨幅/近20日涨幅/换手/RPS120。"
        "默认只做基础过滤，返回候选可能很多；要得到可直接买入的精筛短名单，请传 min_rps（如95/98）、"
        "min_deviation/max_deviation（偏离20日线区间，如5~20）、max_pct_5d（如15防追高）、"
        "max_pct_20d（如40）、max_turnover（如30剔除爆炒）。date 必须传当前决策日，避免未来数据。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "决策日期 YYYY-MM-DD（回测必须传决策日）"},
            "min_roe": {"type": "number", "description": "最小ROE（百分比，5=5%），默认5"},
            "max_pe": {"type": "number", "description": "最大PE（正数），默认80"},
            "min_rps": {"type": "number", "description": "最小RPS120（0~100），如95/98，默认0不限"},
            "min_deviation": {"type": "number", "description": "偏离20日线最小百分比（如5=5%），默认不限"},
            "max_deviation": {"type": "number", "description": "偏离20日线最大百分比（如20=20%），默认不限"},
            "max_pct_5d": {"type": "number", "description": "近5日累计涨幅上限百分比（如15防追高），默认不限"},
            "max_pct_20d": {"type": "number", "description": "近20日累计涨幅上限百分比（如40），默认不限"},
            "max_turnover": {"type": "number", "description": "换手率上限百分比（如30剔除爆炒），默认不限"},
            "top_n": {"type": "integer", "description": "返回前 N 只，默认20，最大50"},
        },
        "required": ["date"],
    }

    def execute(
        self,
        date: str,
        min_roe: float = 5,
        max_pe: float = 80,
        min_rps: float | None = None,
        min_deviation: float | None = None,
        max_deviation: float | None = None,
        max_pct_5d: float | None = None,
        max_pct_20d: float | None = None,
        max_turnover: float | None = None,
        top_n: int = 20,
        **kwargs: Any,
    ) -> dict[str, Any]:
        top_n = min(max(int(top_n), 1), 50)
        min_roe_dec = float(min_roe) / 100.0
        max_pe = float(max_pe)

        # RPS120 全市场排名（复用缓存）
        rps_map = {r["code"]: r["rps"] for r in _rps_rank_table(date, 120)}

        # 每只股票最新财报 ROE（pub_date <= date）
        fin_rows = db.rows(
            """
            SELECT code, roe FROM (
                SELECT code, roe, ROW_NUMBER() OVER (PARTITION BY code ORDER BY report_date DESC) AS rn
                FROM finances WHERE pub_date <= ?
            ) t WHERE rn = 1
            """,
            [date],
        )
        roe_map = {r["code"]: r["roe"] for r in fin_rows if r["roe"] is not None}

        # 每只股票最新价量 + 趋势（trade_date <= date）
        d_rows = db.rows(
            """
            SELECT code, close, pe_ttm, turnover, ma20, pct_5d, pct_20d FROM (
                SELECT code, close, pe_ttm, turnover,
                    AVG(close) OVER (PARTITION BY code ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS ma20,
                    (close / LAG(close, 5) OVER (PARTITION BY code ORDER BY trade_date) - 1) * 100 AS pct_5d,
                    (close / LAG(close, 20) OVER (PARTITION BY code ORDER BY trade_date) - 1) * 100 AS pct_20d,
                    ROW_NUMBER() OVER (PARTITION BY code ORDER BY trade_date DESC) AS rn
                FROM daily WHERE trade_date <= ?
            ) t WHERE rn = 1
            """,
            [date],
        )

        cands = []
        for r in d_rows:
            code = r["code"]
            roe = roe_map.get(code)
            rps = rps_map.get(code)
            if roe is None or rps is None:
                continue
            pe = r["pe_ttm"]
            close = r["close"]
            ma20 = r["ma20"]
            pct5 = r["pct_5d"]
            pct20 = r["pct_20d"]
            turnover = r["turnover"]
            deviation = (close / ma20 - 1) * 100 if (close and ma20) else None
            # 基础过滤
            if roe < min_roe_dec:
                continue
            if pe is None or pe <= 0 or pe >= max_pe:
                continue
            if not close or not ma20 or close <= ma20:
                continue
            if pct20 is None or pct20 <= 0:
                continue
            # 精筛（可选，全部默认不限制）
            if min_rps is not None and rps < float(min_rps):
                continue
            if deviation is not None:
                if min_deviation is not None and deviation < float(min_deviation):
                    continue
                if max_deviation is not None and deviation > float(max_deviation):
                    continue
            if max_pct_5d is not None and pct5 is not None and pct5 > float(max_pct_5d):
                continue
            if max_pct_20d is not None and pct20 is not None and pct20 > float(max_pct_20d):
                continue
            if max_turnover is not None and turnover is not None and turnover > float(max_turnover):
                continue
            cands.append(
                {
                    "code": code,
                    "roe_pct": round(roe * 100, 2),
                    "pe_ttm": round(pe, 2),
                    "close": close,
                    "close_vs_ma20_pct": round(deviation, 2) if deviation is not None else None,
                    "pct_5d": round(pct5, 2) if pct5 is not None else None,
                    "pct_20d": round(pct20, 2),
                    "turnover": turnover,
                    "rps120": rps,
                }
            )

        cands.sort(key=lambda x: x["rps120"], reverse=True)
        return {"rows": cands[:top_n], "count": len(cands), "date": date, "top_n": top_n}
