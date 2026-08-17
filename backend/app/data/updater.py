"""数据湖补齐与动态更新编排。

两大入口：
- ``incremental_update``：每交易日收盘后，用腾讯快照把全市场当日行情
  （OHLCV / 成交额 / 换手 / PE / PB / 流通市值 / 名称）增量写入 daily / stocks。
- ``backfill_daily_fields``：用腾讯历史后复权 K 线补齐 daily 的
  adj_factor（后复权因子）、amount（成交额）、turnover（换手率）、
  float_mktcap（流通市值，由 换手率×成交量×收盘价 推导）。

另有 ``backfill_stock_meta`` 回填 stocks 的 name / industry / list_date 与 sectors 表。

所有写入均为 UPSERT（幂等），可安全重跑；回填进度与更新日志持久化到
``data/update_state.json``。
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ..core import config
from . import lake, sources

STATE_FILE = config.DATA_DIR / "update_state.json"


# --------------------------------------------------------------------------- #
# 状态 / 日志
# --------------------------------------------------------------------------- #
def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_history(entry: dict, keep: int = 50) -> None:
    state = _load_state()
    hist = state.get("history", [])
    hist.insert(0, entry)
    state["history"] = hist[:keep]
    state["last_update"] = entry
    _save_state(state)


def get_update_status() -> dict:
    """返回当前更新状态（供 API / 前端）。"""
    state = _load_state()
    return {
        "last_update": state.get("last_update"),
        "history": state.get("history", []),
        "backfill": state.get("backfill"),
        "moneyflow": state.get("moneyflow_backfill"),
        "auto_update": _auto_enabled(),
    }


# --------------------------------------------------------------------------- #
# 增量更新（每交易日）
# --------------------------------------------------------------------------- #
def _auto_enabled() -> bool:
    import os

    return os.getenv("DATA_AUTO_UPDATE", "1") == "1"


def incremental_update() -> dict:
    """拉取全市场当日快照并 UPSERT 进 daily / stocks，返回统计。"""
    t0 = time.time()
    conn = lake.get_connection(config.DB_PATH)
    try:
        codes = [r[0] for r in conn.execute("SELECT code FROM stocks ORDER BY code").fetchall()]
    finally:
        conn.close()

    quotes = sources.fetch_quotes(codes)
    if not quotes:
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "kind": "incremental",
            "ok": False,
            "error": "未获取到任何行情快照",
            "latest_trade_date": None,
        }
        _append_history(entry)
        return entry

    conn = lake.get_connection(config.DB_PATH)
    try:
        inserted = 0
        name_updated = 0
        latest_date = ""
        for d in quotes:
            td = d["trade_date"]
            if len(td) != 8 or not td.isdigit():
                continue
            td_fmt = f"{td[:4]}-{td[4:6]}-{td[6:8]}"
            latest_date = max(latest_date, td_fmt)
            conn.execute(
                """
                INSERT INTO daily (code, trade_date, open, high, low, close, volume,
                                   amount, pct_change, turnover, pe_ttm, pb_mrq, float_mktcap)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (code, trade_date) DO UPDATE SET
                    open = excluded.open, high = excluded.high, low = excluded.low,
                    close = excluded.close, volume = excluded.volume,
                    amount = COALESCE(excluded.amount, daily.amount),
                    pct_change = excluded.pct_change, turnover = excluded.turnover,
                    pe_ttm = COALESCE(excluded.pe_ttm, daily.pe_ttm),
                    pb_mrq = COALESCE(excluded.pb_mrq, daily.pb_mrq),
                    float_mktcap = COALESCE(excluded.float_mktcap, daily.float_mktcap)
                """,
                [d["code"], td_fmt, d["open"], d["high"], d["low"], d["close"],
                 d["volume"], d["amount"], d["pct_change"], d["turnover"],
                 d["pe_ttm"], d["pb_mrq"], d["float_mktcap"]],
            )
            inserted += 1
            if d.get("name"):
                conn.execute(
                    "UPDATE stocks SET name = ? WHERE code = ?", [d["name"], d["code"]]
                )
                name_updated += 1
        row = conn.execute("SELECT MAX(trade_date), COUNT(*) FROM daily").fetchone()
    finally:
        conn.close()

    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "kind": "incremental",
        "ok": True,
        "quotes": len(quotes),
        "inserted": inserted,
        "name_updated": name_updated,
        "latest_trade_date": latest_date or None,
        "db_latest": str(row[0]) if row and row[0] else None,
        "db_rows": int(row[1]) if row else 0,
        "elapsed_sec": round(time.time() - t0, 1),
    }
    _append_history(entry)
    return entry


# --------------------------------------------------------------------------- #
# 历史回填：daily 的 amount / turnover / adj_factor / float_mktcap
# --------------------------------------------------------------------------- #
def _derive_float_mktcap(volume: float | None, close: float | None, turnover: float | None) -> float | None:
    """流通市值 ≈ 成交量(股) × 收盘价 × 100 / 换手率(%)。换手率<=0 时不可导。"""
    if not volume or not close or not turnover or turnover <= 0:
        return None
    return volume * close * 100.0 / turnover


def _backfill_one(code: str, start: str, end: str) -> tuple[str, int, str | None]:
    """单只股票回填（在线程池中并行执行，各自持有独立连接）。

    每只股票一次性批量 UPDATE（注册临时 DataFrame → 一条 UPDATE），
    避免逐行 UPDATE 造成的写锁争用。
    """
    import pandas as pd

    conn = lake.get_connection(config.DB_PATH)
    try:
        bars = sources.fetch_kline_full(code, start, end, fq="hfq")
        if not bars:
            return code, 0, None
        # 注意：DuckDB 的 DATE 列取出为 datetime.date，K 线的 date 为字符串，
        # 统一转成字符串做键匹配，避免类型不一致导致全部跳过。
        local = {
            str(r[0]): r[1]
            for r in conn.execute(
                "SELECT trade_date, close FROM daily WHERE code = ?", [code]
            ).fetchall()
        }
        recs: list[dict[str, Any]] = []
        for b in bars:
            d = b["date"]
            if d not in local:
                continue
            raw_close = local[d]
            adj = b["close"] / raw_close if (b["close"] and raw_close) else None
            fmc = _derive_float_mktcap(b["volume"], raw_close, b["turnover"])
            recs.append(
                {
                    "trade_date": d,
                    "amount": b["amount"],
                    "turnover": b["turnover"],
                    "adj_factor": adj,
                    "float_mktcap": fmc,
                }
            )
        if not recs:
            return code, 0, None

        df = pd.DataFrame(recs)
        conn.register("_bf_tmp", df)
        try:
            conn.execute(
                """
                UPDATE daily SET
                    amount      = COALESCE(_bf_tmp.amount, daily.amount),
                    turnover    = COALESCE(_bf_tmp.turnover, daily.turnover),
                    adj_factor  = COALESCE(_bf_tmp.adj_factor, daily.adj_factor),
                    float_mktcap = COALESCE(_bf_tmp.float_mktcap, daily.float_mktcap)
                FROM _bf_tmp
                WHERE daily.code = ? AND daily.trade_date = _bf_tmp.trade_date::DATE
                """,
                [code],
            )
        finally:
            conn.unregister("_bf_tmp")
        return code, len(recs), None
    except Exception as e:  # noqa: BLE001
        return code, 0, f"{type(e).__name__}: {e}"
    finally:
        conn.close()


def _backfill_workers() -> int:
    try:
        return max(1, int(os.getenv("DATA_BACKFILL_WORKERS", "6")))
    except ValueError:
        return 6


def backfill_daily_fields(codes: list[str] | None = None, resume: bool = True, force: bool = False) -> dict:
    """多线程拉取腾讯后复权日 K，补齐 daily 缺失字段（幂等、可断点续跑）。

    - adj_factor  = 后复权收盘 / 原始收盘（原始收盘取本地 daily）
    - amount      = 腾讯 K 线成交额（万元→元）
    - turnover    = 腾讯 K 线换手率（%）
    - float_mktcap= 由换手率推导（见 _derive_float_mktcap）
    """
    t0 = time.time()
    conn = lake.get_connection(config.DB_PATH)
    try:
        if codes is None:
            codes = [r[0] for r in conn.execute("SELECT code FROM stocks ORDER BY code").fetchall()]
        min_d, max_d = conn.execute(
            "SELECT MIN(trade_date), MAX(trade_date) FROM daily"
        ).fetchone()
        start = str(min_d)
        end = str(max_d)
    finally:
        conn.close()

    state = _load_state()
    done = set((state.get("backfill") or {}).get("done_codes", [])) if resume and not force else set()
    total = len(codes)
    remaining = [c for c in codes if c not in done]

    n_code = 0
    n_rows = 0
    errors: list[str] = []

    def save_progress(final: bool = False) -> None:
        state["backfill"] = {
            "done_codes": sorted(done),
            "total": total,
            "remaining": total - len(done),
            "rows_updated": n_rows,
            "errors": errors[-50:],
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "final": final,
        }
        _save_state(state)

    workers = _backfill_workers()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_backfill_one, c, start, end): c for c in remaining}
        for fut in concurrent.futures.as_completed(futs):
            code = futs[fut]
            try:
                c, rows, err = fut.result()
            except Exception as e:  # noqa: BLE001
                c, rows, err = code, 0, f"{type(e).__name__}: {e}"
            done.add(c)
            n_code += 1
            n_rows += rows
            if err:
                errors.append(f"{c}:{err}")
            if n_code % 20 == 0:
                save_progress()

    save_progress(final=True)
    return {
        "ok": True,
        "codes_processed": n_code,
        "rows_updated": n_rows,
        "errors": errors[-50:],
        "elapsed_sec": round(time.time() - t0, 1),
    }


# --------------------------------------------------------------------------- #
# 历史回填：moneyflow 资金流（新浪）
# --------------------------------------------------------------------------- #
def _backfill_moneyflow_one(code: str, num: int) -> tuple[str, int, str | None]:
    """单只股票资金流回填：先并行拉取网络数据，再短时持锁写库（锁冲突自动重试）。

    网络拉取不持有 DB 连接，避免在持锁期间阻塞其它 worker；写库阶段对
    跨进程锁冲突（后端进程也在读写同一 DuckDB）做几次退避重试。
    """
    import pandas as pd

    # 1) 网络拉取（不持 DB 锁，可并行）
    try:
        rows = sources.fetch_moneyflow(code, num=num)
    except Exception as e:  # noqa: BLE001
        return code, 0, f"{type(e).__name__}: {e}"
    if not rows:
        return code, 0, None

    recs = [
        {
            "trade_date": r["date"],
            "main_net_inflow": r["main_net_inflow"],
            "super_net_inflow": r["super_net_inflow"],
            "large_net_inflow": r["large_net_inflow"],
        }
        for r in rows
    ]

    # 2) 写库（短时持锁；跨进程锁冲突重试几次）
    last_err: Exception | None = None
    for attempt in range(4):
        conn = None
        try:
            conn = lake.get_connection(config.DB_PATH)
            df = pd.DataFrame(recs)
            conn.register("_mf_tmp", df)
            try:
                conn.execute(
                    """
                    INSERT INTO moneyflow (code, trade_date, main_net_inflow, super_net_inflow, large_net_inflow)
                    SELECT ?, trade_date::DATE, main_net_inflow, super_net_inflow, large_net_inflow
                    FROM _mf_tmp
                    ON CONFLICT (code, trade_date) DO UPDATE SET
                        main_net_inflow = excluded.main_net_inflow,
                        super_net_inflow = excluded.super_net_inflow,
                        large_net_inflow = excluded.large_net_inflow
                    """,
                    [code],
                )
            finally:
                conn.unregister("_mf_tmp")
            return code, len(recs), None
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(0.5 * (attempt + 1))
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
    return code, 0, f"{type(last_err).__name__}: {last_err}"


def backfill_moneyflow(
    codes: list[str] | None = None,
    num: int = 1500,
    resume: bool = True,
    force: bool = False,
    workers: int | None = None,
) -> dict:
    """多线程拉取新浪个股资金流，写入 moneyflow 表（幂等、可断点续跑）。

    每只股票单次请求 num 根历史（默认 1500，覆盖约 2020-06 至今，超出数据湖范围）。
    进度持久化到 data/update_state.json 的 moneyflow_backfill 字段。
    """
    t0 = time.time()
    conn = lake.get_connection(config.DB_PATH)
    try:
        if codes is None:
            codes = [r[0] for r in conn.execute("SELECT code FROM stocks ORDER BY code").fetchall()]
    finally:
        conn.close()

    state = _load_state()
    mf = state.get("moneyflow_backfill") or {}
    done = set(mf.get("done_codes", [])) if resume and not force else set()
    remaining = [c for c in codes if c not in done]
    total = len(codes)
    n_code = 0
    n_rows = 0
    errors: list[str] = []

    def save_progress(final: bool = False) -> None:
        state["moneyflow_backfill"] = {
            "done_codes": sorted(done),
            "total": total,
            "remaining": total - len(done),
            "rows": n_rows,
            "errors": errors[-50:],
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "final": final,
        }
        _save_state(state)

    workers = workers or _backfill_workers()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_backfill_moneyflow_one, c, num): c for c in remaining}
        for fut in concurrent.futures.as_completed(futs):
            c = futs[fut]
            try:
                code, rows, err = fut.result()
            except Exception as e:  # noqa: BLE001
                code, rows, err = c, 0, f"{type(e).__name__}: {e}"
            n_code += 1
            n_rows += rows
            if err:
                errors.append(f"{code}:{err}")
                # 失败（网络/锁冲突等）不标记完成，下次 resume 会重试
            else:
                done.add(c)
            if n_code % 20 == 0:
                save_progress()

    save_progress(final=True)
    return {
        "ok": True,
        "codes_processed": n_code,
        "rows": n_rows,
        "errors": errors[-50:],
        "elapsed_sec": round(time.time() - t0, 1),
    }


# --------------------------------------------------------------------------- #
# 补齐缺失交易日（数据湖日期空洞）
# --------------------------------------------------------------------------- #
def _find_gap_dates(recent_days: int = 90) -> list[str]:
    """找出最近 N 天内覆盖率显著偏低的交易日（即「日期空洞」）。

    只关注最新日期附近的空洞（用于「显示到最新」），历史早期的逐步上市
    造成的低覆盖不算空洞。
    """
    conn = lake.get_connection(config.DB_PATH)
    try:
        rows = conn.execute(
            "SELECT trade_date, COUNT(*) AS n FROM daily GROUP BY trade_date ORDER BY trade_date"
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return []
    max_n = max(n for _, n in rows)
    latest = str(rows[-1][0])
    cutoff = (date.fromisoformat(latest) - timedelta(days=recent_days)).isoformat()
    return [str(d) for d, n in rows if str(d) >= cutoff and n < max_n * 0.9]


def _fill_missing_one(code: str, gap_dates: list[str], end: str, count: int = 80) -> tuple[str, int, str | None]:
    """为单只股票补齐缺失交易日（原始 OHLC + 复权因子 + 换手/成交额/流通市值）。

    先查该股在缺口日期上的覆盖情况：已全部存在则直接跳过（省去网络请求），
    幂等、可安全重跑。
    """
    import pandas as pd

    conn = lake.get_connection(config.DB_PATH)
    try:
        ph = ",".join(["?"] * len(gap_dates))
        have = {
            str(r[0])
            for r in conn.execute(
                f"SELECT DISTINCT trade_date FROM daily WHERE code = ? AND trade_date IN ({ph})",
                [code, *gap_dates],
            ).fetchall()
        }
        missing = [d for d in gap_dates if d not in have]
        if not missing:
            return code, 0, None

        raw = sources.fetch_kline(code, gap_dates[0], end, fq="", count=count)
        if not raw:
            return code, 0, None
        hfq = {b["date"]: b["close"] for b in sources.fetch_kline(code, gap_dates[0], end, fq="hfq", count=count)}
        missing_set = set(missing)
        bars = [b for b in raw if b["date"] in missing_set]
        if not bars:
            return code, 0, None
        bars.sort(key=lambda b: b["date"])

        # 上一收盘价（用于计算涨跌幅）
        row = conn.execute(
            "SELECT close FROM daily WHERE code = ? AND trade_date < ? ORDER BY trade_date DESC LIMIT 1",
            [code, bars[0]["date"]],
        ).fetchone()
        prev_close = row[0] if row else None

        recs: list[dict[str, Any]] = []
        for b in bars:
            raw_close = b["close"]
            pct = (raw_close / prev_close - 1) * 100 if (raw_close and prev_close) else None
            hq = hfq.get(b["date"])
            adj = (hq / raw_close) if (hq and raw_close) else None
            fmc = _derive_float_mktcap(b["volume"], raw_close, b["turnover"])
            recs.append(
                {
                    "code": code,
                    "trade_date": b["date"],
                    "open": b["open"],
                    "high": b["high"],
                    "low": b["low"],
                    "close": raw_close,
                    "volume": b["volume"],
                    "amount": b["amount"],
                    "adj_factor": adj,
                    "pct_change": pct,
                    "turnover": b["turnover"],
                    "float_mktcap": fmc,
                }
            )
            prev_close = raw_close

        df = pd.DataFrame(recs)
        conn.register("_ins", df)
        try:
            conn.execute(
                """
                INSERT INTO daily
                    (code, trade_date, open, high, low, close, volume, amount,
                     adj_factor, pct_change, turnover, float_mktcap)
                SELECT code, trade_date::DATE, open, high, low, close, volume, amount,
                       adj_factor, pct_change, turnover, float_mktcap
                FROM _ins
                """
            )
        finally:
            conn.unregister("_ins")
        return code, len(recs), None
    except Exception as e:  # noqa: BLE001
        return code, 0, f"{type(e).__name__}: {e}"
    finally:
        conn.close()


def fill_missing_daily_days(codes: list[str] | None = None, workers: int | None = None) -> dict:
    """补齐数据湖的日期空洞（例如 2026-07-17 ~ 08-13 只有少量个股的缺口）。

    逐股拉取原始 K 线 + 后复权 K 线，对 daily 中缺失的 (code, trade_date) 行做 INSERT。
    幂等：已补齐的股票会被跳过，可安全重跑。
    """
    t0 = time.time()
    gap_dates = _find_gap_dates()
    if not gap_dates:
        return {"ok": True, "gap_dates": [], "inserted": 0, "elapsed_sec": 0.0}
    gap_end = max(gap_dates)

    conn = lake.get_connection(config.DB_PATH)
    try:
        if codes is None:
            codes = [r[0] for r in conn.execute("SELECT code FROM stocks ORDER BY code").fetchall()]
    finally:
        conn.close()

    workers = workers or _backfill_workers()
    n_code = 0
    n_rows = 0
    errors: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_fill_missing_one, c, gap_dates, gap_end): c for c in codes}
        for fut in concurrent.futures.as_completed(futs):
            c = futs[fut]
            try:
                code, rows, err = fut.result()
            except Exception as e:  # noqa: BLE001
                code, rows, err = c, 0, f"{type(e).__name__}: {e}"
            n_code += 1
            n_rows += rows
            if err:
                errors.append(f"{code}:{err}")

    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "kind": "fill_missing_days",
        "ok": True,
        "gap_dates": gap_dates,
        "codes_processed": n_code,
        "inserted": n_rows,
        "errors": errors[-50:],
        "elapsed_sec": round(time.time() - t0, 1),
    }
    _append_history(entry)
    return entry


# --------------------------------------------------------------------------- #
# stocks 元数据回填 + sectors
# --------------------------------------------------------------------------- #
def backfill_stock_meta() -> dict:
    """回填 stocks.name / industry / list_date，并重建 sectors 表。"""
    t0 = time.time()
    sl = sources.fetch_stock_list()
    industry = sources.fetch_industry_map()

    conn = lake.get_connection(config.DB_PATH)
    try:
        floor = str(conn.execute("SELECT MIN(trade_date) FROM daily").fetchone()[0])
        name_n = ind_n = 0
        for s in sl:
            code = s["code"]
            name = s.get("name")
            if name:
                conn.execute("UPDATE stocks SET name = ? WHERE code = ?", [name, code])
                name_n += 1
            if code in industry:
                conn.execute(
                    "UPDATE stocks SET industry = ? WHERE code = ?", [industry[code], code]
                )
                ind_n += 1

        # 上市日期近似：首个有成交量交易日（一次 GROUP BY，避免逐股扫描）。
        # 若首个交易日等于数据起点（2021-05-17），说明上市早于数据起点，置空。
        first_dates = {
            r[0]: str(r[1])
            for r in conn.execute(
                "SELECT code, MIN(trade_date) FROM daily WHERE volume > 0 GROUP BY code"
            ).fetchall()
        }
        list_n = 0
        for code, d in first_dates.items():
            if d != floor:
                conn.execute("UPDATE stocks SET list_date = ? WHERE code = ?", [d, code])
                list_n += 1

        # 重建 sectors（行业成分快照，快照日期=最新交易日；由行业映射反转，避免二次拉取）
        snap = str(conn.execute("SELECT MAX(trade_date) FROM daily").fetchone()[0])
        conn.execute("DELETE FROM sectors")
        sectors: dict[str, list[str]] = {}
        for code, ind in industry.items():
            sectors.setdefault(ind, []).append(code)
        sec_n = 0
        for sector, codes in sectors.items():
            for code in codes:
                conn.execute(
                    "INSERT INTO sectors (code, sector, trade_date) VALUES (?, ?, ?)",
                    [code, sector, snap],
                )
                sec_n += 1
        conn.commit()
    finally:
        conn.close()

    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "kind": "stock_meta",
        "ok": True,
        "name_updated": name_n,
        "industry_updated": ind_n,
        "list_date_updated": list_n,
        "sector_rows": sec_n,
        "elapsed_sec": round(time.time() - t0, 1),
    }
    _append_history(entry)
    return entry


# --------------------------------------------------------------------------- #
# 数据新鲜度
# --------------------------------------------------------------------------- #
def freshness() -> dict:
    conn = lake.get_connection(config.DB_PATH, read_only=True)
    try:
        latest = conn.execute("SELECT MAX(trade_date) FROM daily").fetchone()[0]
        n = conn.execute(
            "SELECT COUNT(*) FROM daily WHERE trade_date = (SELECT MAX(trade_date) FROM daily)"
        ).fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0]
    finally:
        conn.close()
    return {
        "latest_trade_date": str(latest) if latest else None,
        "stocks_on_latest_day": n,
        "stocks_total": total,
        "stale": n < total,
    }
