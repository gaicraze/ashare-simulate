"""P7 每日增量更新：用腾讯行情接口批量获取全市场当日收盘数据，追加到 DuckDB。

用法：python scripts/update_daily.py （建议每个交易日收盘后运行一次）
说明：腾讯接口提供当日快照，用于「每日增量」；历史中间日期补齐需东财历史接口
（当前受代理 SSL 限制，见 scripts/update_daily_eastmoney.py 或网络改善后运行）。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core import config  # noqa: E402
from app.data import lake, updater  # noqa: E402

PROXY = os.getenv("DATA_PROXY") or os.getenv("PROXY") or None


def tx_symbol(code: str) -> str:
    if code.startswith(("6", "68")):
        return "sh" + code
    return "sz" + code


def parse_quote(text: str) -> dict | None:
    """解析腾讯行情返回，提取 OHLCV 等。"""
    if "=" not in text:
        return None
    payload = text.split("=", 1)[1].strip().strip(";").strip('"')
    parts = payload.split("~")
    if len(parts) < 40:
        return None
    try:
        return {
            "code": parts[2],
            "trade_date": parts[30][:8],  # 时间戳前8位 YYYYMMDD
            "open": float(parts[5]),
            "close": float(parts[3]),
            "high": float(parts[33]),
            "low": float(parts[34]),
            "volume": float(parts[6]) * 100,       # 手 -> 股
            "amount": float(parts[37]) * 10000,    # 万元 -> 元
            "pct_change": float(parts[32]),
            "turnover": float(parts[38]),
        }
    except (ValueError, IndexError):
        return None


def main() -> None:
    # 防护：收盘前不落库（与 backend/app/data/updater.py 的 incremental_update 一致）。
    if not updater._market_closed_today():
        print("盘中不落库：请在收盘后（北京时间 15:00 后）运行本脚本，避免把盘中价当作收盘价写入。")
        return

    conn = lake.get_connection(config.DB_PATH)
    try:
        codes = [r[0] for r in conn.execute("SELECT code FROM stocks ORDER BY code").fetchall()]
    finally:
        conn.close()

    print(f"全市场 {len(codes)} 只，开始获取当日快照…")
    quotes = []
    with httpx.Client(proxy=PROXY, timeout=15) as client:
        for i in range(0, len(codes), 60):
            chunk = codes[i : i + 60]
            q = ",".join(tx_symbol(c) for c in chunk)
            for attempt in range(3):
                try:
                    r = client.get(f"https://qt.gtimg.cn/q={q}")
                    text = r.content.decode("gbk", errors="ignore")
                    for line in text.strip().split(";"):
                        line = line.strip()
                        if line:
                            d = parse_quote(line)
                            if d:
                                quotes.append(d)
                    break
                except Exception as e:  # noqa: BLE001
                    if attempt == 2:
                        print(f"  [warn] 批次 {i//60} 失败: {e}")
                    time.sleep(0.5)
            if (i // 60 + 1) % 20 == 0:
                print(f"  已处理 {min(i + 60, len(codes))}/{len(codes)} 只")
            time.sleep(0.1)

    print(f"获取到 {len(quotes)} 条当日行情")

    conn = lake.get_connection(config.DB_PATH)
    try:
        inserted = 0
        for d in quotes:
            trade_date = f"{d['trade_date'][:4]}-{d['trade_date'][4:6]}-{d['trade_date'][6:8]}"
            conn.execute(
                """
                INSERT INTO daily (code, trade_date, open, high, low, close, volume, amount, pct_change, turnover)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (code, trade_date) DO UPDATE SET
                    open = excluded.open, high = excluded.high, low = excluded.low, close = excluded.close,
                    volume = excluded.volume, amount = excluded.amount,
                    pct_change = excluded.pct_change, turnover = excluded.turnover
                """,
                [d["code"], trade_date, d["open"], d["high"], d["low"], d["close"],
                 d["volume"], d["amount"], d["pct_change"], d["turnover"]],
            )
            inserted += 1
        print(f"落库 {inserted} 条")
        r = conn.execute("SELECT MAX(trade_date), COUNT(*) FROM daily").fetchone()
        print(f"数据湖最新交易日: {r[0]}, 总行数: {r[1]}")
    finally:
        conn.close()

    # 同步补齐指数日线，避免指数数据滞后于个股
    print("补齐指数日线…")
    idx = updater.incremental_index_update()
    if idx.get("ok"):
        print(f"指数更新完成: {idx.get('end')} / {idx.get('codes')}")
    else:
        print(f"[warn] 指数更新失败: {idx.get('error') or idx.get('codes')}")


if __name__ == "__main__":
    main()
