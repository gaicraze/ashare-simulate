"""交易分析中心·操作建议的持久化：保存 / 列表 / 读取 / 删除 / 导出 Markdown。

建议以 JSON 文件落盘到 backend/data/trading/，文件名 advice_<时间戳>_<uuid>.json。
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from ..core import config

ADVICE_DIR = config.DATA_DIR / "trading"


def build_payload(result: dict) -> dict:
    """把 run_advice() 的返回整理成可持久化 payload（附带生成时间）。"""
    return {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model": result.get("model"),
        "strategy_id": result.get("strategy_id"),
        "strategy_name": result.get("strategy_name"),
        "mode": result.get("mode"),
        "market": result.get("market"),
        "candidates": result.get("candidates"),
        "positions": result.get("positions"),
        "account": result.get("account"),
        "portfolio_overview": result.get("portfolio_overview"),
        "notes": result.get("notes"),
        "pick_trace": result.get("pick_trace"),
        "report": result.get("report", ""),
    }


def save_advice(result: dict) -> Path:
    ADVICE_DIR.mkdir(parents=True, exist_ok=True)
    payload = build_payload(result)
    ts = time.strftime("%Y%m%d_%H%M%S")
    out = ADVICE_DIR / f"advice_{ts}_{uuid.uuid4().hex[:6]}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    return out


def list_advices() -> list[dict]:
    if not ADVICE_DIR.exists():
        return []
    items: list[dict] = []
    for f in sorted(ADVICE_DIR.glob("advice_*.json"), reverse=True):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            report = (d.get("report") or "").strip()
            mode = d.get("mode")
            items.append(
                {
                    "file": f.name,
                    "created_at": d.get("created_at"),
                    "strategy_name": d.get("strategy_name"),
                    "mode": mode,
                    "mode_label": "结合真实仓位" if mode == "portfolio" else "个股买入意见",
                    "model": d.get("model"),
                    "n_candidates": len(d.get("candidates") or []),
                    "n_positions": len(d.get("positions") or []),
                    "preview": report[:120].replace("\n", " "),
                }
            )
        except Exception:  # noqa: BLE001
            continue
    return items


def load_advice(filename: str) -> dict | None:
    p = ADVICE_DIR / filename
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    d["file"] = filename
    d["markdown"] = to_markdown(d)
    return d


def delete_advice(filename: str) -> bool:
    p = ADVICE_DIR / filename
    if p.exists():
        p.unlink()
        return True
    return False


def _cell(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, (int, float)):
        return f"{float(v):.2f}"
    return str(v)


def to_markdown(d: dict) -> str:
    """把一次建议渲染为 Markdown（报告正文 + 头部元信息），供导出下载。"""
    market = d.get("market") or {}
    clock = market.get("clock") or {}
    lines: list[str] = [
        "# 交易分析中心 · 操作建议",
        "",
        f"- 策略：{d.get('strategy_name') or '-'}",
        f"- 分析模式：{'结合真实仓位' if d.get('mode') == 'portfolio' else '个股买入意见'}",
        f"- 生成时间：{d.get('created_at') or '-'}",
        f"- 分析模型：{d.get('model') or '-'}",
        f"- 盘面时点：{clock.get('beijing_time') or '-'}（{clock.get('session') or '-'}）",
        f"- 数据口径：{'盘中实时' if market.get('data_mode') == 'intraday' else '最新交易日日线'}（最新交易日 {market.get('latest_trade_date') or '-'}）",
    ]
    if d.get("notes"):
        lines.append(f"- 补充说明：{d['notes']}")
    lines.append("")
    snap = market.get("snapshot") or {}
    if snap:
        lines += [
            "## 盘面概览",
            "",
            "| 指标 | 数值 |",
            "|---|---|",
            f"| 市场环境 | {market.get('regime') or '-'} |",
            f"| 上涨 / 下跌 | {snap.get('up')} / {snap.get('down')} |",
            f"| 涨停 / 跌停 | {snap.get('limit_up')} / {snap.get('limit_down')} |",
            f"| 平均涨跌幅(%) | {_cell(snap.get('avg_pct'))} |",
            f"| 总成交额(亿) | {_cell((snap.get('total_amount') or 0) / 1e8)} |",
            "",
        ]
    overview = d.get("portfolio_overview") or {}
    account = d.get("account") or {}
    if overview or (account and (account.get("principal") is not None or account.get("available_cash") is not None)):
        lines += [
            "## 账户与持仓概览",
            "",
            "| 指标 | 数值 |",
            "|---|---|",
            f"| 本金 | {_cell(overview.get('principal', account.get('principal')))} |",
            f"| 可用现金 | {_cell(overview.get('available_cash', account.get('available_cash')))} |",
            f"| 持仓市值 | {_cell(overview.get('positions_value'))} |",
            f"| 总资产 | {_cell(overview.get('total_assets'))} |",
            f"| 仓位比例(%) | {_cell(overview.get('position_ratio_pct'))} |",
            f"| 现金比例(%) | {_cell(overview.get('cash_ratio_pct'))} |",
            f"| 总盈亏 | {_cell(overview.get('total_pnl'))} |",
            f"| 总收益率(%) | {_cell(overview.get('total_pnl_pct'))} |",
            "",
        ]
    if d.get("positions"):
        lines += [
            "## 持仓一览",
            "",
            "| 代码 | 名称 | 数量 | 成本价 | 现价 | 浮盈亏(%) | 市值 |",
            "|---|---|---|---|---|---|---|",
        ]
        for p in d["positions"]:
            lines.append(
                f"| {p.get('code')} | {p.get('name') or '-'} | {p.get('quantity')} "
                f"| {_cell(p.get('cost_price'))} | {_cell(p.get('current_price'))} "
                f"| {_cell(p.get('pnl_pct'))} | {_cell(p.get('market_value'))} |"
            )
        lines.append("")
    lines += [
        "## 操作建议",
        "",
        d.get("report", "").strip(),
        "",
    ]
    return "\n".join(lines)
