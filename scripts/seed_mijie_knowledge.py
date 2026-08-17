# -*- coding: utf-8 -*-
"""把 Mi姐股市投资体系知识节点录入知识中心（实时 API，幂等）。

用法:
    cd backend && .venv/bin/python ../scripts/seed_mijie_knowledge.py [--base http://127.0.0.1:8000]

- 按标题去重：已存在的节点跳过，不重复入库；
- 数据源: app/knowledge/seed_mijie.py（与种子库同源，重启后端后 sync_seed 也会自动同步）。
"""
from __future__ import annotations

import argparse
import sys

import httpx

BASE = "http://127.0.0.1:8000"
API = "/api/knowledge"

# 允许直接运行（不在 backend 目录时也能 import 数据源）
sys.path.insert(0, ".")
try:
    from app.knowledge.seed_mijie import MIJIE_KNOWLEDGE
except ImportError:
    sys.path.insert(0, "../backend")
    from app.knowledge.seed_mijie import MIJIE_KNOWLEDGE


def main() -> int:
    ap = argparse.ArgumentParser(description="录入 Mi姐投资体系知识节点")
    ap.add_argument("--base", default=BASE, help=f"后端地址（默认 {BASE}）")
    args = ap.parse_args()
    url = args.base.rstrip("/") + API

    r = httpx.get(url, timeout=15)
    r.raise_for_status()
    existing = {n.get("title") for n in r.json().get("nodes", [])}

    created = skipped = failed = 0
    for item in MIJIE_KNOWLEDGE:
        title = item["title"]
        if title in existing:
            print(f"  [skip] {title}")
            skipped += 1
            continue
        payload = {k: v for k, v in item.items() if v not in ("", None)}
        resp = httpx.post(url, json=payload, timeout=20)
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if resp.status_code == 200 and not body.get("error"):
            print(f"  [ok]   {title}")
            created += 1
        else:
            print(f"  [FAIL] {title} -> {resp.status_code} {str(body)[:160]}")
            failed += 1

    print(f"\n完成：新建 {created}，跳过（已存在）{skipped}，失败 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
