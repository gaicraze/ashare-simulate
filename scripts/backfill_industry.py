"""P7 数据补齐：用 akshare 获取 A 股行业分类，回填 DuckDB stocks.industry。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core import config  # noqa: E402
from app.data import lake  # noqa: E402


def fetch_industry_map() -> dict[str, str]:
    """返回 {股票代码: 行业名称}。"""
    import os

    proxy = os.getenv("DATA_PROXY") or os.getenv("PROXY")
    if proxy:
        os.environ.setdefault("HTTP_PROXY", proxy)
        os.environ.setdefault("HTTPS_PROXY", proxy)
    import akshare as ak

    # 1. 获取行业板块列表
    board_df = ak.stock_board_industry_name_em()
    board_names = board_df["板块名称"].tolist()
    print(f"行业板块数: {len(board_names)}")

    industry: dict[str, str] = {}
    for i, name in enumerate(board_names):
        try:
            cons = ak.stock_board_industry_cons_em(symbol=name)
            for _, row in cons.iterrows():
                code = str(row["代码"])
                industry[code] = name
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] 行业 {name} 失败: {e}")
        if (i + 1) % 20 == 0:
            print(f"  已处理 {i + 1}/{len(board_names)} 个行业，累计 {len(industry)} 只股票")
        time.sleep(0.2)
    return industry


def main() -> None:
    industry = fetch_industry_map()
    print(f"\n获取到 {len(industry)} 只股票的行业")

    conn = lake.get_connection(config.DB_PATH)
    try:
        updated = 0
        for code, name in industry.items():
            conn.execute("UPDATE stocks SET industry = ? WHERE code = ?", [name, code])
            updated += 1
        print(f"回填 {updated} 只")
        # 验证
        r = conn.execute("SELECT code, name, industry FROM stocks WHERE industry IS NOT NULL LIMIT 5").fetchall()
        for code, name, ind in r:
            print(f"  {code} {name} -> {ind}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
