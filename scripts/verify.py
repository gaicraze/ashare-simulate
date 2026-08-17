"""P0 验证：查询接口 + FastAPI 路由冒烟测试。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core import config  # noqa: E402
from app.data import lake, query  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

print("== lake summary ==")
print(lake.table_summary(config.DB_PATH))
print("== daily range ==")
print(query.get_daily_date_range(config.DB_PATH))
print("== sample daily 600519 ==")
for r in query.get_daily(config.DB_PATH, code="600519", limit=3):
    print(r)
print("== stock list head ==")
print(query.get_stock_list(config.DB_PATH)[:3])

print("\n== API smoke test ==")
client = TestClient(app)
print("health:", client.get("/api/health").json())
r = client.get("/api/data/summary").json()
print("summary.tables:", r["tables"])
print("summary.daily:", r["daily"])
r = client.get("/api/data/daily", params={"code": "600519", "limit": 2}).json()
print("daily api rows:", len(r["rows"]), "| first:", r["rows"][0] if r["rows"] else None)
r = client.get("/api/data/stocks").json()
print("stocks api count:", len(r["rows"]))
print("\nALL OK")
