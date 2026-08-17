"""在线数据源客户端：腾讯行情 + 新浪行情（默认），可选 akshare（东财/新浪）。

设计要点：
- 默认不依赖 akshare / 东财（当前容器网络下东财 push2his 不可达），直接走 HTTP 接口；
- 腾讯 `qt.gtimg.cn` 提供当日快照（含成交额/换手/PE/PB/流通市值）；
- 腾讯 `proxy.finance.qq.com/.../newfqkline` 提供历史日 K（含换手率/成交额/后复权价）；
- 新浪提供 A 股列表（含流通市值/PE/PB）与行业分类（含成分）；
- 可通过环境变量切换数据源：`DATA_SOURCE=akshare` 或旧开关 `AKSHARE_ENABLED=1`
  走 akshare（见 akshare_source.py，失败自动降级到新浪），默认走腾讯/新浪直连；
- 全部接口统一超时、重试、可选代理，失败返回空而非抛异常（由调用方决定降级）。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Iterable

import httpx

# 数据源（容器内可直连；如需代理可设环境变量 DATA_PROXY）
DATA_PROXY = None

TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q={q}"
TENCENT_KLINE_URL = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
SINA_LIST_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData"
)
SINA_INDUSTRY_URL = "https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"


def _proxy() -> str | None:
    import os

    return os.getenv("DATA_PROXY") or None


def _client(timeout: float = 20.0) -> httpx.Client:
    proxy = _proxy()
    return httpx.Client(timeout=timeout, follow_redirects=True, proxy=proxy)


def _get_json(
    url: str,
    params: dict[str, Any] | None = None,
    *,
    timeout: float = 20.0,
    retries: int = 3,
    referer: str | None = None,
) -> Any:
    """GET 并解析 JSON，带重试；失败返回 None。"""
    headers = {"User-Agent": "Mozilla/5.0"} if referer else None
    if referer:
        headers = {"User-Agent": "Mozilla/5.0", "Referer": referer}
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with _client(timeout) as client:
                r = client.get(url, params=params, headers=headers)
                r.raise_for_status()
                return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.4 * (attempt + 1))
    return None


# --------------------------------------------------------------------------- #
# 腾讯：当日全市场快照
# --------------------------------------------------------------------------- #
def tencent_symbol(code: str) -> str:
    """A 股代码 → 腾讯市场前缀。"""
    if code.startswith(("6", "68")):
        return "sh" + code
    return "sz" + code


def _f(x: str) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v


def parse_tencent_quote(text: str) -> dict | None:
    """解析腾讯行情快照单行（v_sh600519="..."），提取落库所需字段。"""
    if "=" not in text:
        return None
    payload = text.split("=", 1)[1].strip().strip(";").strip('"')
    parts = payload.split("~")
    if len(parts) < 50:
        return None
    try:
        # 字段索引见腾讯行情文档：3=现价 4=昨收 5=今开 6=成交量(手) 30=时间
        # 32=涨跌幅 33=最高 34=最低 37=成交额(万) 38=换手 39=PE(TTM) 44=流通市值(亿)
        # 45=总市值(亿) 46=PB
        code = parts[2]
        if not (len(code) == 6 and code.isdigit()):
            return None
        close = _f(parts[3])
        prev = _f(parts[4])
        open_ = _f(parts[5])
        volume = _f(parts[6])  # 手
        ts = parts[30] if len(parts) > 30 else ""
        high = _f(parts[33])
        low = _f(parts[34])
        amount = _f(parts[37])  # 万元
        turnover = _f(parts[38])  # %
        pe_ttm = _f(parts[39])
        float_mktcap = _f(parts[44])  # 亿元
        pb_mrq = _f(parts[46])
        name = parts[1]
        return {
            "code": code,
            "name": name,
            "trade_date": ts[:8] if len(ts) >= 8 else "",
            "open": open_,
            "close": close,
            "prev_close": prev,
            "high": high,
            "low": low,
            "volume": volume * 100 if volume is not None else None,  # 手 -> 股
            "amount": amount * 10000 if amount is not None else None,  # 万元 -> 元
            "pct_change": _f(parts[32]) if len(parts) > 32 else None,
            "turnover": turnover,
            "pe_ttm": pe_ttm,
            "pb_mrq": pb_mrq,
            "float_mktcap": float_mktcap * 1e8 if float_mktcap is not None else None,  # 亿 -> 元
        }
    except (ValueError, IndexError):
        return None


def fetch_tencent_quotes(codes: Iterable[str], batch: int = 60) -> list[dict]:
    """批量获取全市场当日快照。返回解析成功的 quote 列表。"""
    codes = list(codes)
    quotes: list[dict] = []
    with _client(15.0) as client:
        for i in range(0, len(codes), batch):
            chunk = codes[i : i + batch]
            q = ",".join(tencent_symbol(c) for c in chunk)
            for attempt in range(3):
                try:
                    r = client.get(TENCENT_QUOTE_URL.format(q=q))
                    text = r.content.decode("gbk", errors="ignore")
                    for line in text.strip().split(";"):
                        line = line.strip()
                        if line:
                            d = parse_tencent_quote(line)
                            if d:
                                quotes.append(d)
                    break
                except Exception:  # noqa: BLE001
                    if attempt == 2:
                        break
                    time.sleep(0.3)
            time.sleep(0.05)
    return quotes


# --------------------------------------------------------------------------- #
# 腾讯：历史日 K（后复权 + 换手率 + 成交额）
# --------------------------------------------------------------------------- #
def fetch_tencent_kline(
    code: str,
    start: str,
    end: str,
    fq: str = "hfq",
    count: int = 600,
) -> list[dict]:
    """获取单只股票历史日 K。

    返回每条 bar：
        date / open / close / high / low / volume(股) / turnover(%) / amount(元)
    其中 fq='hfq' 时 open/close/high/low 为后复权价（用于计算复权因子）。
    接口单次最多返回约 640 根，需调用方按日期窗口分页。
    """
    symbol = tencent_symbol(code)
    url = TENCENT_KLINE_URL
    params = {
        "param": f"{symbol},day,{start},{end},{count},{fq}",
    }
    data = _get_json(url, params, timeout=25.0, retries=3, referer="https://gu.qq.com/")
    if not data or data.get("code") != 0:
        return []
    node = (data.get("data") or {}).get(symbol) or {}
    key = f"{fq}day" if fq else "day"
    arr = node.get(key) or node.get("day") or []
    bars: list[dict] = []
    for row in arr:
        if not isinstance(row, list) or len(row) < 9:
            continue
        try:
            d = str(row[0])
            close = _f(row[2])
            volume = _f(row[5])  # 手
            turnover = _f(row[7])  # %
            amount = _f(row[8])  # 万元
            bars.append(
                {
                    "date": d,
                    "open": _f(row[1]),
                    "close": close,
                    "high": _f(row[3]),
                    "low": _f(row[4]),
                    "volume": volume * 100 if volume is not None else None,
                    "turnover": turnover,
                    "amount": amount * 10000 if amount is not None else None,
                }
            )
        except (ValueError, IndexError):
            continue
    return bars


def fetch_tencent_kline_full(code: str, start: str, end: str, fq: str = "hfq") -> list[dict]:
    """分页拉取单只股票完整历史日 K。

    注意：该接口忽略 start，返回 `count` 根「以 end 为终点、向前回溯」的 K 线
    （单次上限约 640 根），因此这里以 end 为锚点向后翻页，再按日期区间过滤去重。
    """
    import datetime as _dt

    start_d = _dt.date.fromisoformat(start)
    end_d = _dt.date.fromisoformat(end)
    merged: dict[str, dict] = {}
    cur_end = end
    guard = 0
    while guard < 20:
        guard += 1
        bars = fetch_tencent_kline(code, start, cur_end, fq=fq)
        if not bars:
            break
        batch_min = None
        for b in bars:
            if start <= b["date"] <= end:
                merged[b["date"]] = b
            if batch_min is None or b["date"] < batch_min:
                batch_min = b["date"]
        if batch_min is None or batch_min <= start:
            break
        # 下一批以「本批最早一根」的前一天为终点，向前回溯
        nxt = _dt.date.fromisoformat(batch_min) - _dt.timedelta(days=1)
        if nxt.isoformat() >= cur_end:
            break
        cur_end = nxt.isoformat()
    return [merged[k] for k in sorted(merged)]


# --------------------------------------------------------------------------- #
# 新浪：个股资金流（超大单/大单/主力净流入）
# --------------------------------------------------------------------------- #
SINA_MONEYFLOW_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "MoneyFlow.ssl_qsfx_lscjfb"
)


def fetch_sina_moneyflow(code: str, num: int = 1500) -> list[dict]:
    """获取单只股票历史资金流（超大单/大单/主力净流入）。

    返回按日期升序的列表：
        [{date, super_net_inflow, large_net_inflow, main_net_inflow, net_inflow}]
    其中 main_net_inflow = super_net_inflow + large_net_inflow（主力 = 超大单 + 大单），
    net_inflow 为全部分类净流入合计。单次最多约 num 根（约 1300 个交易日，num=1500 一次取全）。
    """
    symbol = tencent_symbol(code)
    data = _get_json(
        SINA_MONEYFLOW_URL,
        params={"page": 1, "num": num, "sort": "opendate", "asc": 0, "daima": symbol},
        timeout=25.0,
        retries=3,
        referer="https://finance.sina.com.cn/",
    )
    if not isinstance(data, list):
        return []
    rows: list[dict] = []
    for r in data:
        if not isinstance(r, dict):
            continue
        d = str(r.get("opendate", "")).strip()
        # opendate 形如 "2026-08-14"（已含连字符）
        if len(d) != 10 or d[4] != "-" or d[7] != "-":
            continue
        super_net = _f(r.get("r0_net"))
        large_net = _f(r.get("r1_net"))
        if super_net is not None and large_net is not None:
            main_net = super_net + large_net
        else:
            main_net = super_net if super_net is not None else large_net
        rows.append(
            {
                "date": d,
                "super_net_inflow": super_net,
                "large_net_inflow": large_net,
                "main_net_inflow": main_net,
                "net_inflow": _f(r.get("netamount")),
            }
        )
    rows.sort(key=lambda x: x["date"])
    return rows


# --------------------------------------------------------------------------- #
# 新浪：A 股列表 / 行业分类
# --------------------------------------------------------------------------- #
def fetch_sina_node(node: str, page: int, num: int = 100) -> list[dict]:
    data = _get_json(
        SINA_LIST_URL,
        params={"page": page, "num": num, "sort": "symbol", "asc": 1, "node": node},
        timeout=25.0,
        retries=3,
        referer="https://finance.sina.com.cn/",
    )
    if data is None:
        return []
    if isinstance(data, dict):  # 单条或错误
        return []
    return list(data) if isinstance(data, list) else []


def fetch_sina_stock_list() -> list[dict]:
    """拉取全市场 A 股列表（含名称/流通市值/PE/PB）。"""
    rows: list[dict] = []
    page = 1
    while True:
        chunk = fetch_sina_node("hs_a", page, 100)
        if not chunk:
            break
        for r in chunk:
            code = str(r.get("code", ""))
            if not (len(code) == 6 and code.isdigit()):
                continue
            rows.append(
                {
                    "code": code,
                    "name": r.get("name"),
                    "float_mktcap": _f(r.get("nmc")) * 10000 if _f(r.get("nmc")) is not None else None,  # 万 -> 元
                    "total_mktcap": _f(r.get("mktcap")) * 10000 if _f(r.get("mktcap")) is not None else None,
                    "pe_ttm": _f(r.get("per")),
                    "pb_mrq": _f(r.get("pb")),
                    "turnover": _f(r.get("turnoverratio")),
                }
            )
        page += 1
        if page > 60:  # 安全上限
            break
    return rows


def fetch_sina_industry_nodes() -> dict[str, str]:
    """返回 {板块节点代码: 行业名称}。"""
    r = None
    for attempt in range(3):
        try:
            with _client(20.0) as client:
                resp = client.get(
                    SINA_INDUSTRY_URL,
                    headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"},
                )
                r = resp.content.decode("gbk", errors="ignore")
                break
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    if not r:
        return {}
    m = re.search(r"=\s*(\{.*\})", r, re.S)
    if not m:
        return {}
    try:
        d = json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}
    nodes: dict[str, str] = {}
    for node, val in d.items():
        parts = val.split(",")
        if len(parts) >= 2:
            nodes[node] = parts[1]
    return nodes


def fetch_sina_industry_map() -> dict[str, str]:
    """返回 {股票代码: 行业名称}（遍历各行业成分）。"""
    nodes = fetch_sina_industry_nodes()
    mapping: dict[str, str] = {}
    for node, industry in nodes.items():
        page = 1
        while True:
            chunk = fetch_sina_node(node, page, 100)
            if not chunk:
                break
            for r in chunk:
                code = str(r.get("code", ""))
                if len(code) == 6 and code.isdigit():
                    mapping[code] = industry
            page += 1
            if page > 20:
                break
            time.sleep(0.05)
    return mapping


def fetch_sina_sectors() -> dict[str, list[str]]:
    """返回 {行业名称: [成分股代码]}，用于 sectors 表。"""
    nodes = fetch_sina_industry_nodes()
    sectors: dict[str, list[str]] = {}
    for node, industry in nodes.items():
        codes: list[str] = []
        page = 1
        while True:
            chunk = fetch_sina_node(node, page, 100)
            if not chunk:
                break
            for r in chunk:
                code = str(r.get("code", ""))
                if len(code) == 6 and code.isdigit():
                    codes.append(code)
            page += 1
            if page > 20:
                break
            time.sleep(0.05)
        sectors[industry] = codes
    return sectors


# --------------------------------------------------------------------------- #
# 数据源分发：腾讯/新浪直连（默认） ⇄ akshare（可选）
# --------------------------------------------------------------------------- #
# 切换方式（优先级从高到低）：
#   1. DATA_SOURCE=akshare | tencent   —— 显式指定
#   2. AKSHARE_ENABLED=1               —— P7 遗留开关，等价 DATA_SOURCE=akshare
#   3. 默认 tencent（腾讯/新浪直连）
def _use_akshare() -> bool:
    import os

    ds = os.getenv("DATA_SOURCE", "").strip().lower()
    if ds == "akshare":
        return True
    if ds == "tencent":
        return False
    try:
        from ..core import config

        return bool(config.AKSHARE_ENABLED)
    except Exception:  # noqa: BLE001
        return False


def data_source_status() -> dict:
    """当前数据源与 akshare 可用性（供 API / 前端展示）。"""
    try:
        from . import akshare_source

        ak_ok = akshare_source.available()
    except Exception:  # noqa: BLE001
        ak_ok = False
    return {
        "active": "akshare" if _use_akshare() else "tencent",
        "akshare_installed": ak_ok,
        "akshare_enabled": _use_akshare(),
    }


def fetch_kline(code: str, start: str, end: str, fq: str = "hfq", count: int = 600) -> list[dict]:
    """历史日 K（分发）：akshare 时走 akshare_source（东财→新浪降级），空则降级腾讯。"""
    if _use_akshare():
        from . import akshare_source

        bars = akshare_source.fetch_akshare_kline(code, start, end, fq=fq)
        if bars:
            return bars
    return fetch_tencent_kline(code, start, end, fq=fq, count=count)


def fetch_kline_full(code: str, start: str, end: str, fq: str = "hfq") -> list[dict]:
    """完整历史日 K（分发）：akshare 时一次取全，空则降级腾讯分页。"""
    if _use_akshare():
        from . import akshare_source

        bars = akshare_source.fetch_akshare_kline_full(code, start, end, fq=fq)
        if bars:
            return bars
    return fetch_tencent_kline_full(code, start, end, fq=fq)


def fetch_quotes(codes: Iterable[str]) -> list[dict]:
    """当日快照（分发）：akshare 走东财 spot_em（→新浪降级），空则降级腾讯。"""
    if _use_akshare():
        from . import akshare_source

        quotes = akshare_source.fetch_akshare_quotes(codes)
        if quotes:
            return quotes
    return fetch_tencent_quotes(codes)


def fetch_moneyflow(code: str, num: int = 1500) -> list[dict]:
    """个股资金流（分发）：akshare 走东财个股资金流，空则降级新浪。"""
    if _use_akshare():
        from . import akshare_source

        rows = akshare_source.fetch_akshare_moneyflow(code)
        if rows:
            return rows
    return fetch_sina_moneyflow(code, num=num)


def fetch_stock_list() -> list[dict]:
    """A 股列表（分发）：akshare 走东财 spot_em，空则降级新浪。"""
    if _use_akshare():
        from . import akshare_source

        rows = akshare_source.fetch_akshare_stock_list()
        if rows:
            return rows
    return fetch_sina_stock_list()


def fetch_industry_map() -> dict[str, str]:
    """{代码: 行业}（分发）：akshare 走东财行业板块，空则降级新浪。"""
    if _use_akshare():
        from . import akshare_source

        mapping = akshare_source.fetch_akshare_industry_map()
        if mapping:
            return mapping
    return fetch_sina_industry_map()


def fetch_sectors() -> dict[str, list[str]]:
    """{行业: [成分股]}（分发）。"""
    if _use_akshare():
        from . import akshare_source

        sectors = akshare_source.fetch_akshare_sectors()
        if sectors:
            return sectors
    return fetch_sina_sectors()


def fetch_index_hist(code: str, start: str, end: str) -> list[dict]:
    """指数日 K（新能力，仅 akshare 提供；腾讯直连暂不支持时返回空）。"""
    if _use_akshare():
        from . import akshare_source

        return akshare_source.fetch_akshare_index_hist(code, start, end)
    return []
