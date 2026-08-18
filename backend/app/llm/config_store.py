"""LLM 模型配置存储：providers 列表 + 用途(role)路由，持久化到本地 JSON。"""
from __future__ import annotations

import json
import os
import uuid

from ..core import config

CONFIG_FILE = config.DATA_DIR / "llm_config.json"

# 用途角色（哪些场景用到 LLM，各可单独指定模型）
ROLES = [
    {"id": "backtest", "label": "回测执行（逐日决策，建议用快速模型）"},
    {"id": "strategy_gen", "label": "策略生成（建议用强模型）"},
    {"id": "decide", "label": "策略决策（单回合测试）"},
    {"id": "report", "label": "报告总结"},
    {"id": "optimize", "label": "策略优化（建议用非推理模型，输出结构化结果）"},
    {"id": "analysis", "label": "个股深度分析（建议用强模型）"},
    {"id": "trading", "label": "交易分析中心（盘中/盘后操作建议，建议用强模型）"},
    {"id": "knowledge", "label": "知识吸收与结构化（建议用强模型）"},
]


def _init_from_env() -> dict:
    providers: list[dict] = []
    if os.getenv("DEEPSEEK_API_KEY"):
        providers.append(
            {
                "id": "deepseek",
                "name": "DeepSeek",
                "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
                "api_key": os.getenv("DEEPSEEK_API_KEY", ""),
                "model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
                "enabled": True,
            }
        )
    if os.getenv("MINIMAX_API_KEY"):
        providers.append(
            {
                "id": "minimax",
                "name": "Minimax",
                "base_url": os.getenv("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1").rstrip("/"),
                "api_key": os.getenv("MINIMAX_API_KEY", ""),
                "model": os.getenv("MINIMAX_MODEL", "MiniMax-M2"),
                "enabled": True,
            }
        )
    has_ds = any(p["id"] == "deepseek" for p in providers)
    has_mm = any(p["id"] == "minimax" for p in providers)
    first = providers[0]["id"] if providers else None
    roles = {
        "backtest": "minimax" if has_mm else first,
        # 策略生成/优化/知识结构化都需要输出长文本或干净 JSON：推理模型会把 token
        # 花在 reasoning 上导致 content 为空，故默认走非推理模型（minimax）。
        "strategy_gen": "minimax" if has_mm else first,
        "decide": "deepseek" if has_ds else first,
        "report": "deepseek" if has_ds else first,
        "optimize": "minimax" if has_mm else first,
        "analysis": "deepseek" if has_ds else first,
        "trading": "deepseek" if has_ds else first,
        "knowledge": "minimax" if has_mm else first,
    }
    return {"providers": providers, "roles": roles}


def _default_role_provider(providers: list[dict], role: str) -> str | None:
    """给缺失的角色一个合理缺省。

    optimize / knowledge 需要输出结构化 JSON：推理模型会把 token 花在 reasoning 上
    导致 content 为空，故默认走非推理模型（minimax）；其余走首个 provider。
    """
    if not providers:
        return None
    if role in ("optimize", "knowledge", "strategy_gen"):
        mm = next((p for p in providers if p["id"] == "minimax"), None)
        return (mm or providers[0])["id"]
    return providers[0]["id"]


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(cfg.get("providers"), list) and isinstance(cfg.get("roles"), dict):
                # 回填新增角色（如 optimize），避免旧配置缺失导致路由回退到首个 provider
                roles = cfg.get("roles", {})
                changed = False
                for r in ROLES:
                    if r["id"] not in roles:
                        roles[r["id"]] = _default_role_provider(cfg["providers"], r["id"])
                        changed = True
                if changed:
                    save_config(cfg)
                return cfg
        except Exception:  # noqa: BLE001
            pass
    cfg = _init_from_env()
    save_config(cfg)
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def add_provider(name: str, base_url: str, api_key: str, model: str) -> dict:
    cfg = load_config()
    p = {
        "id": "p_" + uuid.uuid4().hex[:6],
        "name": name,
        "base_url": base_url.rstrip("/"),
        "api_key": api_key,
        "model": model,
        "enabled": True,
    }
    cfg["providers"].append(p)
    save_config(cfg)
    return p


def update_provider(pid: str, **fields) -> dict | None:
    cfg = load_config()
    for p in cfg["providers"]:
        if p["id"] == pid:
            for k, v in fields.items():
                if v is not None:
                    p[k] = v
            save_config(cfg)
            return p
    return None


def delete_provider(pid: str) -> bool:
    cfg = load_config()
    new = [p for p in cfg["providers"] if p["id"] != pid]
    if len(new) == len(cfg["providers"]):
        return False
    cfg["providers"] = new
    for k, v in list(cfg["roles"].items()):
        if v == pid:
            cfg["roles"][k] = new[0]["id"] if new else None
    save_config(cfg)
    return True


def set_role(role: str, provider_id: str | None) -> None:
    cfg = load_config()
    cfg["roles"][role] = provider_id
    save_config(cfg)
