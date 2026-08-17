"""从 HuggingFace 下载 TraderHarness A-share 数据集到本地 parquet 目录。

用法:
    python scripts/download_data.py                 # 下载全部
    python scripts/download_data.py daily.parquet   # 只下载指定文件
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

BASE = "https://huggingface.co/datasets/ANTICH/traderharness-ashare-5y/resolve/main"
# 可选代理：优先读 DATA_PROXY，其次 PROXY；均未设置则直连
PROXY = os.getenv("DATA_PROXY") or os.getenv("PROXY") or None

FILES = [
    "daily.parquet",
    "fundamentals.parquet",
    "valuation.parquet",
    "index_300.parquet",
    "dividends.parquet",
    "announcements.parquet",
]

OUT_DIR = Path(__file__).resolve().parents[1] / "backend" / "data" / "parquet"


def download(name: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    url = f"{BASE}/{name}"
    out = out_dir / name
    if out.exists() and out.stat().st_size > 0:
        print(f"[skip] {name} 已存在 ({out.stat().st_size/1e6:.1f} MB)")
        return out
    print(f"[downloading] {name} ...")
    with httpx.Client(proxy=PROXY, follow_redirects=True, timeout=600.0) as client:
        with client.stream("GET", url) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or 0)
            done = 0
            with out.open("wb") as f:
                for chunk in r.iter_bytes(chunk_size=1 << 20):
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        print(f"\r  {name}: {done/1e6:.1f}/{total/1e6:.1f} MB", end="")
    print(f"\n[done] {name} -> {out} ({out.stat().st_size/1e6:.1f} MB)")
    return out


def main() -> None:
    names = sys.argv[1:] or FILES
    for n in names:
        try:
            download(n, OUT_DIR)
        except Exception as e:  # noqa: BLE001
            print(f"[error] {n}: {e}")


if __name__ == "__main__":
    main()
