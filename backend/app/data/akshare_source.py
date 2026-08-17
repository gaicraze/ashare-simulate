"""akshare 数据源适配层（可选数据源，与 sources.py 同构）。

设计要点：
- 与 ``sources.py`` 的腾讯/新浪直连源返回**同构 dict**，可无缝替换；
- akshare 为懒加载：未安装或不可用时全部函数返回空，不影响系统运行；
- 所有接口失败返回空而非抛异常（由调用方决定降级）；
- 主接口走东财（akshare 默认），东财不可达时自动降级到新浪接口；
- 单位约定与 sources.py 一致：volume=股，amount=元，pct_change/turnover=%，市值=元。
"""
from __future__ import annotations

from typing import Iterable

# 东财日线列名（stock_zh_a_hist）
_EM_HIST_COLS = [
    "日期", "股票代码", "开盘", "收盘", "最高", "最低",
    "成交量", "成交额", "振幅", "涨跌幅", "涨跌额", "换手率",
]
# 新浪日线列名（stock_zh_a_daily）
_SINA_DAILY_COLS = ["date", "open", "high", "low", "close", "volume", "amount", "outstanding_share", "turnover"]


def available() -> bool:
    """akshare 是否已安装。"""
    try:
        import akshare  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _ak():
    """懒加载 akshare；未安装抛 ImportError（由调用方捕获）。"""
    import akshare as ak
    return ak


def _f(x) -> float | None:
    """安全转 float。"""
    try:
        if x is None:
            return None
        v = float(x)
        return v
    except (TypeError, ValueError):
        return None


def _symbol(code: str) -> str:
    """6 位代码 → akshare 新浪符号（sh/sz 前缀）。"""
    return ("sh" if code.startswith(("6", "68", "9")) else "sz") + code


def _em_market(code: str) -> str:
    """6 位代码 → 东财资金流市场参数。"""
    return "sh" if code.startswith(("6", "68", "9")) else "sz"


def _to_bar(row) -> dict | None:
    """东财日线行 → 统一 bar dict。

    注意：akshare 的「换手率」返回小数（0.0024 = 0.24%），
    而项目约定（schema 注释 / _derive_float_mktcap）为百分比，故 ×100。
    """
    try:
        d = str(row["日期"])
        close = _f(row["收盘"])
        volume = _f(row["成交量"])
        amount = _f(row["成交额"])
        turn = _f(row["换手率"])
        return {
            "date": d,
            "open": _f(row["开盘"]),
            "close": close,
            "high": _f(row["最高"]),
            "low": _f(row["最低"]),
            "volume": volume * 100 if volume is not None else None,   # 手 -> 股
            "amount": amount,
            "turnover": turn * 100 if turn is not None else None,     # 小数 -> %
            "pct_change": _f(row["涨跌幅"]),
        }
    except (KeyError, ValueError, IndexError):
        return None


# --------------------------------------------------------------------------- #
# 历史日 K
# --------------------------------------------------------------------------- #
def fetch_akshare_kline(code: str, start: str, end: str, fq: str = "hfq") -> list[dict]:
    """单只股票历史日 K（与 sources.fetch_tencent_kline 同构）。

    返回每条 bar：date / open / close / high / low / volume(股) /
    turnover(%) / amount(元) / pct_change(%)。
    ``fq`` 语义对齐腾讯源："hfq"=后复权，"qfq"=前复权，""=不复权。
    主接口为东财 stock_zh_a_hist；失败自动降级到新浪 stock_zh_a_daily。
    """
    bars = _hist_em(code, start, end, fq)
    if not bars:
        bars = _hist_sina(code, start, end, fq)
    return bars


def fetch_akshare_kline_full(code: str, start: str, end: str, fq: str = "hfq") -> list[dict]:
    """完整历史日 K（东财接口按日期区间一次取全，无需分页）。"""
    return fetch_akshare_kline(code, start, end, fq=fq)


def _hist_em(code: str, start: str, end: str, fq: str) -> list[dict]:
    try:
        ak = _ak()
        df = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
            adjust=fq or "",
        )
    except Exception:  # noqa: BLE001
        return []
    if df is None or df.empty:
        return []
    bars: list[dict] = []
    for _, row in df.iterrows():
        b = _to_bar(row)
        if b:
            bars.append(b)
    return bars


def _hist_sina(code: str, start: str, end: str, fq: str) -> list[dict]:
    try:
        ak = _ak()
        df = ak.stock_zh_a_daily(
            symbol=_symbol(code),
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
            adjust=fq or "",
        )
    except Exception:  # noqa: BLE001
        return []
    if df is None or df.empty:
        return []
    bars: list[dict] = []
    prev_close: float | None = None
    for _, row in df.iterrows():
        close = _f(row.get("close"))
        try:
            d = str(row["date"])[:10]
        except (KeyError, ValueError):
            continue
        pct = None
        if close is not None and prev_close:
            pct = (close / prev_close - 1) * 100.0
        prev_close = close
        bars.append(
            {
                "date": d,
                "open": _f(row.get("open")),
                "close": close,
                "high": _f(row.get("high")),
                "low": _f(row.get("low")),
                "volume": _f(row.get("volume")),          # 新浪已是股
                "amount": _f(row.get("amount")),
                "turnover": (_f(row.get("turnover")) or 0) * 100,  # 小数 -> %
                "pct_change": pct,
            }
        )
    return bars


# --------------------------------------------------------------------------- #
# 全市场当日快照（东财 spot_em）
# --------------------------------------------------------------------------- #
def _latest_trade_date() -> str:
    """用 600519 最近一根日 K 的日期作为交易日（东财接口快照不带日期字段）。"""
    try:
        ak = _ak()
        df = ak.stock_zh_a_hist(symbol="600519", period="daily", start_date="19900101", end_date="", adjust="")
        if df is not None and not df.empty:
            return str(df.iloc[-1]["日期"])[:10]
    except Exception:  # noqa: BLE001
        pass
    from datetime import date

    return date.today().isoformat()


def fetch_akshare_quotes(codes: Iterable[str]) -> list[dict]:
    """全市场当日快照（与 sources.fetch_tencent_quotes 同构）。

    主接口东财 spot_em 一次取全市场；失败自动降级到新浪快照
    （stock_zh_a_spot，无 PE/PB/市值，由调用方 COALESCE 保留旧值）。
    trade_date 取最近交易日。
    """
    quotes = _quotes_em(codes)
    if not quotes:
        quotes = _quotes_sina(codes)
    return quotes


def _quotes_em(codes: set[str]) -> list[dict]:
    try:
        ak = _ak()
        df = ak.stock_zh_a_spot_em()
    except Exception:  # noqa: BLE001
        return []
    if df is None or df.empty:
        return []
    td = _latest_trade_date().replace("-", "")
    quotes: list[dict] = []
    for _, row in df.iterrows():
        code = str(row.get("代码", ""))
        if code not in codes:
            continue
        close = _f(row.get("最新价"))
        prev = _f(row.get("昨收"))
        volume = _f(row.get("成交量"))        # 手
        float_mc = _f(row.get("流通市值"))    # 元
        quotes.append(
            {
                "code": code,
                "name": str(row.get("名称", "")),
                "trade_date": td,
                "open": _f(row.get("今开")),
                "close": close,
                "prev_close": prev,
                "high": _f(row.get("最高")),
                "low": _f(row.get("最低")),
                "volume": volume * 100 if volume is not None else None,   # 手 -> 股
                "amount": _f(row.get("成交额")),                           # 元
                "pct_change": _f(row.get("涨跌幅")),
                "turnover": _f(row.get("换手率")),
                "pe_ttm": _f(row.get("市盈率-动态")),
                "pb_mrq": _f(row.get("市净率")),
                "float_mktcap": float_mc,
            }
        )
    return quotes


def _quotes_sina(codes: set[str]) -> list[dict]:
    try:
        ak = _ak()
        df = ak.stock_zh_a_spot()
    except Exception:  # noqa: BLE001
        return []
    if df is None or df.empty:
        return []
    td = _latest_trade_date().replace("-", "")
    quotes: list[dict] = []
    for _, row in df.iterrows():
        # 新浪快照代码带市场前缀（sh600519 / sz000001 / bj920519），归一化为 6 位
        raw = str(row.get("代码", "")).strip()
        code = raw[-6:] if len(raw) > 6 and raw[:2] in ("sh", "sz", "bj") else raw
        if code not in codes:
            continue
        close = _f(row.get("最新价"))
        prev = _f(row.get("昨收"))
        volume = _f(row.get("成交量"))        # 股（与成交额交叉验证一致）
        quotes.append(
            {
                "code": code,
                "name": str(row.get("名称", "")),
                "trade_date": td,
                "open": _f(row.get("今开")),
                "close": close,
                "prev_close": prev,
                "high": _f(row.get("最高")),
                "low": _f(row.get("最低")),
                "volume": volume,
                "amount": _f(row.get("成交额")),
                "pct_change": _f(row.get("涨跌幅")),
                "turnover": None,
                "pe_ttm": None,
                "pb_mrq": None,
                "float_mktcap": None,
            }
        )
    return quotes


# --------------------------------------------------------------------------- #
# 个股资金流（东财 stock_individual_fund_flow）
# --------------------------------------------------------------------------- #
def fetch_akshare_moneyflow(code: str) -> list[dict]:
    """单只股票历史资金流（与 sources.fetch_sina_moneyflow 同构）。

    返回按日期升序：[{date, super_net_inflow, large_net_inflow, main_net_inflow, net_inflow}]，
    单位元；main = super + large。
    """
    try:
        ak = _ak()
        df = ak.stock_individual_fund_flow(stock=code, market=_em_market(code))
    except Exception:  # noqa: BLE001
        return []
    if df is None or df.empty:
        return []
    rows: list[dict] = []
    for _, r in df.iterrows():
        d = str(r.get("日期", ""))[:10]
        if len(d) != 10:
            continue
        super_net = _f(r.get("超大单净流入-净额"))
        large_net = _f(r.get("大单净流入-净额"))
        main_net = _f(r.get("主力净流入-净额"))
        if main_net is None and super_net is not None and large_net is not None:
            main_net = super_net + large_net
        rows.append(
            {
                "date": d,
                "super_net_inflow": super_net,
                "large_net_inflow": large_net,
                "main_net_inflow": main_net,
                "net_inflow": main_net,
            }
        )
    rows.sort(key=lambda x: x["date"])
    return rows


# --------------------------------------------------------------------------- #
# 股票列表 / 行业分类（东财）
# --------------------------------------------------------------------------- #
def fetch_akshare_stock_list() -> list[dict]:
    """全市场 A 股列表（与 sources.fetch_sina_stock_list 同构）。"""
    try:
        ak = _ak()
        df = ak.stock_zh_a_spot_em()
    except Exception:  # noqa: BLE001
        return []
    if df is None or df.empty:
        return []
    rows: list[dict] = []
    for _, r in df.iterrows():
        code = str(r.get("代码", ""))
        if len(code) != 6 or not code.isdigit():
            continue
        rows.append(
            {
                "code": code,
                "name": str(r.get("名称", "")),
                "float_mktcap": _f(r.get("流通市值")),
                "total_mktcap": _f(r.get("总市值")),
                "pe_ttm": _f(r.get("市盈率-动态")),
                "pb_mrq": _f(r.get("市净率")),
                "turnover": _f(r.get("换手率")),
            }
        )
    return rows


def fetch_akshare_industry_map() -> dict[str, str]:
    """返回 {股票代码: 行业名称}（东财行业板块，与 scripts/backfill_industry.py 同法）。"""
    mapping: dict[str, str] = {}
    try:
        ak = _ak()
        boards = ak.stock_board_industry_name_em()
    except Exception:  # noqa: BLE001
        return mapping
    if boards is None or boards.empty:
        return mapping
    import time

    for _, b in boards.iterrows():
        name = str(b.get("板块名称", ""))
        if not name:
            continue
        try:
            cons = ak.stock_board_industry_cons_em(symbol=name)
            if cons is not None and not cons.empty:
                for _, c in cons.iterrows():
                    code = str(c.get("代码", ""))
                    if len(code) == 6 and code.isdigit():
                        mapping[code] = name
        except Exception:  # noqa: BLE001
            continue
        time.sleep(0.1)  # 控制频率，避免被东财限流
    return mapping


def fetch_akshare_sectors() -> dict[str, list[str]]:
    """返回 {行业名称: [成分股代码]}（用于 sectors 表）。"""
    mapping = fetch_akshare_industry_map()
    sectors: dict[str, list[str]] = {}
    for code, industry in mapping.items():
        sectors.setdefault(industry, []).append(code)
    return sectors


# --------------------------------------------------------------------------- #
# 指数日 K（东财 index_zh_a_hist）—— 补齐 indices 表的新能力
# --------------------------------------------------------------------------- #
def fetch_akshare_index_hist(code: str, start: str, end: str) -> list[dict]:
    """指数日 K（上证 000001 / 深证 399001 / 创业板 399006 等）。

    返回：[{date, open, close, high, low, volume, amount, pct_change}]。
    """
    try:
        ak = _ak()
        df = ak.index_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
        )
    except Exception:  # noqa: BLE001
        return []
    if df is None or df.empty:
        return []
    bars: list[dict] = []
    for _, row in df.iterrows():
        bars.append(
            {
                "date": str(row.get("日期", ""))[:10],
                "open": _f(row.get("开盘")),
                "close": _f(row.get("收盘")),
                "high": _f(row.get("最高")),
                "low": _f(row.get("最低")),
                "volume": _f(row.get("成交量")),
                "amount": _f(row.get("成交额")),
                "pct_change": _f(row.get("涨跌幅")),
            }
        )
    return bars
