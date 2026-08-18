"""交易分析中心 API 路由。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel

from ..strategy import store as strategy_store
from ..tools import db
from ..trading import advisor, market as market_mod, positions as positions_store, store as advice_store

router = APIRouter()


class PositionUpsertRequest(BaseModel):
    code: str
    name: str | None = None
    quantity: float
    cost_price: float


class AccountUpdateRequest(BaseModel):
    principal: float | None = None  # 本金（元）
    available_cash: float | None = None  # 可用现金（元）


class TradingAdviceRequest(BaseModel):
    strategy_id: str
    mode: str = "stock"  # stock=个股买入意见（不结合仓位）；portfolio=结合真实仓位
    scope: str | None = None  # stock 模式下可选：指定某只股票代码/名称
    pull_intraday: bool = True  # 盘中自动拉取实时数据


def _resolve_name(code: str) -> str | None:
    row = db.one("SELECT name FROM stocks WHERE code = ?", [code])
    return row["name"] if row else None


@router.get("/trading/market")
def trading_market() -> dict:
    """盘面环境：交易时段 + 最新交易日快照 +（盘中）实时指数。"""
    return market_mod.market_context_jsonable(market_mod.market_context(pull_intraday=True))


@router.get("/trading/positions")
def list_positions() -> dict:
    return {"positions": positions_store.list_positions(), "account": positions_store.get_account()}


@router.post("/trading/positions")
def upsert_position(req: PositionUpsertRequest) -> dict:
    code = (req.code or "").strip()
    if not (len(code) == 6 and code.isdigit()):
        return {"ok": False, "error": "请输入6位股票代码"}
    if req.quantity <= 0 or req.cost_price <= 0:
        return {"ok": False, "error": "数量和成本价必须大于0"}
    name = (req.name or "").strip() or _resolve_name(code)
    p = positions_store.upsert_position(code, name or None, req.quantity, req.cost_price)
    return {"ok": True, "position": p}


@router.delete("/trading/positions/{pid}")
def delete_position(pid: str) -> dict:
    return {"deleted": positions_store.delete_position(pid)}


@router.get("/trading/account")
def get_account() -> dict:
    """账户资金情况：本金 / 可用现金。"""
    return {"account": positions_store.get_account()}


@router.post("/trading/account")
def update_account(req: AccountUpdateRequest) -> dict:
    """更新账户资金（本金 / 可用现金，仅更新传入字段）。"""
    if req.principal is not None and req.principal < 0:
        return {"ok": False, "error": "本金不能为负"}
    if req.available_cash is not None and req.available_cash < 0:
        return {"ok": False, "error": "可用现金不能为负"}
    account = positions_store.update_account(req.principal, req.available_cash)
    return {"ok": True, "account": account}


@router.post("/trading/advice")
def trading_advice(req: TradingAdviceRequest) -> dict:
    """生成操作建议：策略 + 最新盘面（盘中拉取实时数据）+（可选）真实持仓。"""
    strategy = strategy_store.get_strategy(req.strategy_id)
    if strategy is None:
        return {"ok": False, "error": "策略不存在，请先在「策略中心」创建或选择策略"}
    if req.mode not in ("stock", "portfolio"):
        return {"ok": False, "error": f"未知分析模式：{req.mode}"}
    try:
        result = advisor.run_advice(strategy, mode=req.mode, scope=req.scope, pull_intraday=req.pull_intraday)
        file = advice_store.save_advice(result).name
        full = advice_store.load_advice(file)
        return {"ok": True, **full}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@router.get("/trading/advice/history")
def advice_history() -> dict:
    return {"items": advice_store.list_advices()}


@router.get("/trading/advice/result")
def advice_result(file: str = Query(..., description="建议结果文件名")) -> dict:
    d = advice_store.load_advice(file)
    if d is None:
        return {"ok": False, "error": "结果不存在"}
    return {"ok": True, **d}


@router.delete("/trading/advice/result")
def advice_delete(file: str = Query(..., description="建议结果文件名")) -> dict:
    return {"deleted": advice_store.delete_advice(file)}


@router.get("/trading/advice/export")
def advice_export(file: str = Query(..., description="建议结果文件名")) -> Response:
    d = advice_store.load_advice(file)
    if d is None:
        return Response(content="结果不存在", status_code=404)
    filename = Path(file).stem + ".md"
    return Response(
        content=d["markdown"],
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
