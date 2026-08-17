"""把超过 GitHub 单文件限制的 parquet 拆分为 <50MB 分片（供 git 提交）。

GitHub 对单文件有 100MB 硬限制、50MB 警告线。本项目 `daily.parquet`（约 77MB）
与 `valuation.parquet`（约 143MB）会被拆成若干 `*.parquet.partNN` 分片随仓库分发；
用户首次运行 `scripts/rebuild_lake.py`（或一键部署脚本）时会自动把分片重组回完整文件。

用法（仅维护者需要，在更新数据后重新分片）：
    python scripts/split_data.py
"""
from __future__ import annotations

from pathlib import Path

CHUNK_SIZE = 40 * 1024 * 1024  # 40 MiB（≈41.9 MB），显著低于 GitHub 50MB 警告线

PARQUET_DIR = Path(__file__).resolve().parents[1] / "backend" / "data" / "parquet"

# 需要拆分的大文件（其余 parquet 均远小于 50MB，直接提交）
SPLIT_FILES = ["daily.parquet", "valuation.parquet"]


def main() -> None:
    for name in SPLIT_FILES:
        src = PARQUET_DIR / name
        if not src.exists() or src.stat().st_size == 0:
            print(f"[skip] {name} 不存在或为空")
            continue

        # 清理旧分片，避免残留
        for old in sorted(PARQUET_DIR.glob(f"{name}.part*")):
            old.unlink()

        data = src.read_bytes()
        n = (len(data) + CHUNK_SIZE - 1) // CHUNK_SIZE
        for i in range(n):
            part = data[i * CHUNK_SIZE : (i + 1) * CHUNK_SIZE]
            (PARQUET_DIR / f"{name}.part{i:02d}").write_bytes(part)

        print(f"[done] {name}: {len(data) / 1e6:.1f} MB -> {n} 个分片")


if __name__ == "__main__":
    main()
