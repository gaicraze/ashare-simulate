"""资金流回填：拉取新浪个股资金流（超大单/大单/主力净流入）写入 moneyflow 表。

幂等、可断点续跑（进度在 data/update_state.json 的 moneyflow_backfill 字段）。
也可经后端 API POST /data/backfill/moneyflow 触发（在后端进程内执行，避免跨进程 DB 锁）。

用法（backend 目录下，用 backend/.venv）：
    python ../scripts/backfill_moneyflow.py            # 全市场，续跑
    python ../scripts/backfill_moneyflow.py --force    # 全量重跑
    python ../scripts/backfill_moneyflow.py 600519 000001  # 指定代码
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.data import updater  # noqa: E402


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--force"]
    force = "--force" in sys.argv
    codes = args if args else None
    result = updater.backfill_moneyflow(codes=codes, num=1500, resume=True, force=force)
    print(result)


if __name__ == "__main__":
    main()
