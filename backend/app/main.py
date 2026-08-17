"""FastAPI 入口。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import knowledge_routes, routes
from .core import config
from .data import scheduler
from .knowledge import store as knowledge_store


@asynccontextmanager
async def lifespan(_: FastAPI):
    scheduler.scheduler.start()
    # 知识中心：建表 + 同步种子知识库（幂等，含旧库升级到新分类体系）
    try:
        knowledge_store.sync_seed()
    except Exception as e:  # noqa: BLE001
        print(f"[knowledge] 初始化失败：{type(e).__name__}: {e}")
    yield
    scheduler.scheduler.stop()


app = FastAPI(title="大A交易策略真实模拟器", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes.router, prefix="/api")
app.include_router(knowledge_routes.router, prefix="/api")


@app.get("/")
def root() -> dict:
    return {"service": "ashare-simulator", "status": "ok", "version": "0.1.0"}


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
