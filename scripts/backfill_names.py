"""从腾讯行情接口批量获取 A 股名称，回填 DuckDB stocks.name。"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core import config  # noqa: E402
from app.data import lake  # noqa: E402

PROXY = os.getenv("DATA_PROXY") or os.getenv("PROXY") or None


def tx_symbol(code: str) -> str:
    if code.startswith(("6", "68")):
        return "sh" + code
    if code.startswith(("0", "3")):
        return "sz" + code
    return "bj" + code


def fetch_names(codes: list[str], batch: int = 60) -> dict[str, str]:
    names: dict[str, str] = {}
    with httpx.Client(proxy=PROXY, timeout=30) as client:
        for i in range(0, len(codes), batch):
            chunk = codes[i : i + batch]
            q = ",".join(tx_symbol(c) for c in chunk)
            try:
                r = client.get(f"https://qt.gtimg.cn/q={q}")
                text = r.content.decode("gbk", errors="ignore")
                for line in text.strip().split(";"):
                    line = line.strip()
                    if "=" not in line:
                        continue
                    # v_sh600519="1~贵州茅台~600519~..."
                    payload = line.split("=", 1)[1].strip('"')
                    parts = payload.split("~")
                    if len(parts) >= 3 and len(parts[2]) == 6:
                        names[parts[2]] = parts[1]
            except Exception as e:  # noqa: BLE001
                print(f"  [warn] 批次 {i//batch} 失败: {e}")
            time.sleep(0.2)
    return names


def main() -> None:
    conn = lake.get_connection(config.DB_PATH)
    try:
        codes = [r[0] for r in conn.execute("SELECT code FROM stocks ORDER BY code").fetchall()]
        print(f"共 {len(codes)} 只股票，开始批量获取名称…")
        names = fetch_names(codes)
        print(f"获取到 {len(names)} 个名称")
        updated = 0
        for code, name in names.items():
            conn.execute("UPDATE stocks SET name = ? WHERE code = ?", [name, code])
            updated += 1
        conn.execute("COMMIT") if False else None
        print(f"回填完成：{updated} 只")
        # 验证
        r = conn.execute("SELECT code, name FROM stocks WHERE name IS NOT NULL LIMIT 5").fetchall()
        for code, name in r:
            print(f"  {code} {name}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
