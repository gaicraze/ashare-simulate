"""策略存储：本地 JSON 文件持久化。"""
from __future__ import annotations

import json
import time
import uuid

from ..core import config

STRATEGY_FILE = config.DATA_DIR / "strategies.json"


def _load() -> list[dict]:
    if not STRATEGY_FILE.exists():
        return []
    try:
        data = json.loads(STRATEGY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001
        return []


def _save(strategies: list[dict]) -> None:
    STRATEGY_FILE.parent.mkdir(parents=True, exist_ok=True)
    STRATEGY_FILE.write_text(json.dumps(strategies, ensure_ascii=False, indent=2), encoding="utf-8")


def list_strategies() -> list[dict]:
    return _load()


def get_strategy(sid: str) -> dict | None:
    for s in _load():
        if s["id"] == sid:
            return s
    return None


def create_strategy(name: str, text: str, config: dict | None = None) -> dict:
    strategies = _load()
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    s = {"id": uuid.uuid4().hex[:8], "name": name, "text": text, "created_at": now, "updated_at": now}
    if config is not None:
        s["config"] = config
    strategies.append(s)
    _save(strategies)
    return s


def update_strategy(
    sid: str, name: str | None = None, text: str | None = None, config: dict | None = None
) -> dict | None:
    strategies = _load()
    for s in strategies:
        if s["id"] == sid:
            if name is not None:
                s["name"] = name
            if text is not None:
                s["text"] = text
            if config is not None:
                s["config"] = config
            s["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            _save(strategies)
            return s
    return None


def delete_strategy(sid: str) -> bool:
    strategies = _load()
    new = [s for s in strategies if s["id"] != sid]
    if len(new) == len(strategies):
        return False
    _save(new)
    return True
