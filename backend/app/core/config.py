"""应用配置。"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]  # backend/
load_dotenv(BASE_DIR / ".env")

# 数据湖
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
DB_PATH = DATA_DIR / "duckdb" / "market.duckdb"
PARQUET_DIR = DATA_DIR / "parquet"

# 网络代理（用于访问 GitHub / HuggingFace 等；留空表示直连）
PROXY = os.getenv("PROXY", "") or None

# 在线补数数据源（P7 增量阶段启用）
AKSHARE_ENABLED = os.getenv("AKSHARE_ENABLED", "0") == "1"

# LLM（P2 阶段启用）
LLM_API_BASE = os.getenv("LLM_API_BASE", "")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    PARQUET_DIR.mkdir(parents=True, exist_ok=True)
