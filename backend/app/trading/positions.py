"""真实持仓与账户资金管理：本地 JSON 持久化。

- positions.json：持仓股票列表，字段 id / code / name / quantity(股) / cost_price(成本价，元) / updated_at。
- account.json：账户资金情况，字段 principal(本金，元) / available_cash(可用现金，元) / updated_at。

供「交易分析中心·结合真实仓位」模式做买卖/加减仓/现金管理建议。
"""
from __future__ import annotations

import json
import time
import uuid

from ..core import config

POSITION_FILE = config.DATA_DIR / "positions.json"
ACCOUNT_FILE = config.DATA_DIR / "account.json"


def _load() -> list[dict]:
    if not POSITION_FILE.exists():
        return []
    try:
        data = json.loads(POSITION_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001
        return []


def _save(positions: list[dict]) -> None:
    POSITION_FILE.parent.mkdir(parents=True, exist_ok=True)
    POSITION_FILE.write_text(json.dumps(positions, ensure_ascii=False, indent=2), encoding="utf-8")


def list_positions() -> list[dict]:
    return _load()


def upsert_position(code: str, name: str | None, quantity: float, cost_price: float) -> dict:
    """按 code 幂等 upsert：同股重复提交视为更新持仓。"""
    positions = _load()
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    for p in positions:
        if p["code"] == code:
            p["name"] = name or p.get("name")
            p["quantity"] = float(quantity)
            p["cost_price"] = float(cost_price)
            p["updated_at"] = now
            _save(positions)
            return p
    p = {
        "id": uuid.uuid4().hex[:8],
        "code": code,
        "name": name,
        "quantity": float(quantity),
        "cost_price": float(cost_price),
        "updated_at": now,
    }
    positions.append(p)
    _save(positions)
    return p


def delete_position(pid: str) -> bool:
    positions = _load()
    new = [p for p in positions if p["id"] != pid]
    if len(new) == len(positions):
        return False
    _save(new)
    return True


def clear_positions() -> int:
    n = len(_load())
    _save([])
    return n


# --------------------------------------------------------------------------- #
# 账户资金：本金 / 可用现金
# --------------------------------------------------------------------------- #
def get_account() -> dict:
    """返回账户资金情况。未设置时 principal / available_cash 为 None。"""
    if not ACCOUNT_FILE.exists():
        return {"principal": None, "available_cash": None}
    try:
        data = json.loads(ACCOUNT_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"principal": None, "available_cash": None}
    return {
        "principal": data.get("principal"),
        "available_cash": data.get("available_cash"),
        "updated_at": data.get("updated_at"),
    }


def update_account(principal: float | None = None, available_cash: float | None = None) -> dict:
    """更新账户资金（仅更新传入的非空字段）。"""
    cur = get_account()
    if principal is not None:
        cur["principal"] = float(principal)
    if available_cash is not None:
        cur["available_cash"] = float(available_cash)
    cur["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    ACCOUNT_FILE.parent.mkdir(parents=True, exist_ok=True)
    ACCOUNT_FILE.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    return cur
