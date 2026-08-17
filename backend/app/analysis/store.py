"""个股深度分析报告的持久化：保存 / 列表 / 读取 / 删除 / 导出 Markdown。

报告以 JSON 文件形式落盘到数据目录下的 analysis/ 子目录，
文件名形如 analysis_<code>_<时间戳>_<uuid>.json，便于前端查阅历史与导出。
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from ..core import config

ANALYSIS_DIR = config.DATA_DIR / "analysis"


def _cell(v: Any) -> str:
    """把单元格值格式化为 Markdown 表格文本。"""
    if v is None:
        return "-"
    if isinstance(v, (int, float)):
        return f"{float(v):.2f}"
    return str(v)


def build_payload(result: dict) -> dict:
    """把 analyze() 的返回结果整理成可持久化的 payload。"""
    data = result.get("data", {})
    stock = data.get("stock", {}) or {}
    return {
        "code": stock.get("code") or "unknown",
        "name": stock.get("name"),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model": result.get("model"),
        "data": data,
        "report": result.get("report", ""),
    }


def save_analysis(result: dict) -> Path:
    """保存一次分析结果，返回落盘文件路径。"""
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    payload = build_payload(result)
    code = payload["code"]
    ts = time.strftime("%Y%m%d_%H%M%S")
    out = ANALYSIS_DIR / f"analysis_{code}_{ts}_{uuid.uuid4().hex[:6]}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    return out


def list_analyses() -> list[dict]:
    """列出全部历史分析（按落盘时间倒序），仅返回元信息 + 摘要预览。"""
    if not ANALYSIS_DIR.exists():
        return []
    items: list[dict] = []
    for f in sorted(ANALYSIS_DIR.glob("analysis_*.json"), reverse=True):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            stock = (d.get("data") or {}).get("stock") or {}
            report = (d.get("report") or "").strip()
            items.append(
                {
                    "file": f.name,
                    "code": d.get("code") or stock.get("code"),
                    "name": d.get("name") or stock.get("name"),
                    "created_at": d.get("created_at"),
                    "model": d.get("model"),
                    "preview": report[:120].replace("\n", " "),
                }
            )
        except Exception:  # noqa: BLE001
            continue
    return items


def load_analysis(filename: str) -> dict | None:
    """读取一次历史分析的完整内容，附上导出用的 Markdown。"""
    p = ANALYSIS_DIR / filename
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    d["file"] = filename
    d["markdown"] = to_markdown(d)
    return d


def delete_analysis(filename: str) -> bool:
    p = ANALYSIS_DIR / filename
    if p.exists():
        p.unlink()
        return True
    return False


def to_markdown(d: dict) -> str:
    """把一次分析结果渲染为 Markdown 文本（供导出下载）。"""
    data = d.get("data", {}) or {}
    stock = data.get("stock", {}) or {}
    quote = data.get("quote", {}) or {}
    tech = data.get("technical", {}) or {}
    mf = data.get("moneyflow", {}) or {}
    market = data.get("market", {}) or {}
    rps = data.get("rps", {}) or {}
    fundamentals = data.get("fundamentals") or []
    fin = fundamentals[0] if fundamentals else {}

    lines: list[str] = [
        "# 个股深度分析报告",
        "",
        f"- 股票：{stock.get('name') or '-'}（{stock.get('code') or '-'}）",
    ]
    if stock.get("industry"):
        lines.append(f"- 所属行业：{stock['industry']}")
    lines += [
        f"- 生成时间：{d.get('created_at') or '-'}",
        f"- 分析模型：{d.get('model') or '-'}",
        f"- 数据截止：{quote.get('trade_date') or tech.get('date') or '-'}",
        "",
        "## 关键指标",
        "",
        "| 指标 | 数值 |",
        "|---|---|",
        f"| 最新收盘价 | {_cell(quote.get('close'))} |",
        f"| 涨跌幅(%) | {_cell(quote.get('pct_change'))} |",
        f"| PE(TTM) | {_cell(quote.get('pe_ttm'))} |",
        f"| PB(MRQ) | {_cell(quote.get('pb_mrq'))} |",
        f"| ROE(最新财报,%) | {_cell(fin.get('roe_pct'))} |",
        f"| 净利同比(%) | {_cell(fin.get('yoy_net_profit_pct'))} |",
        f"| 近20日主力净流入(亿) | {_cell(mf.get('main_net_inflow_sum_yi'))} |",
        f"| RPS120强度 | {_cell(rps.get('rps120'))} |",
        f"| 距52周高点(%) | {_cell(tech.get('distance_from_high_pct'))} |",
        f"| 大盘环境 | {_cell(market.get('regime'))} |",
    ]
    if data.get("sectors"):
        lines.append(f"| 板块/概念 | {'、'.join(data['sectors'])} |")
    lines += [
        "",
        "## 深度研究报告",
        "",
        d.get("report", "").strip(),
        "",
        "> 本报告由 AI 自动生成，仅供研究参考，不构成任何投资建议。",
    ]
    return "\n".join(lines)
