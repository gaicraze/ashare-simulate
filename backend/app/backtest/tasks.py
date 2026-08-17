"""回测任务管理：运行中任务（内存）+ 历史任务（扫描结果文件）+ 终止/删除。

任务台账持久化到磁盘（backend/data/backtest_tasks_state.json），后端进程重启后，
未完成的任务会以「interrupted」状态恢复，避免进行中的回测任务凭空消失。
"""
from __future__ import annotations

import json
import threading
import time
import uuid

from ..core import config
from .runner import RESULT_DIR

RUNNING: dict[str, dict] = {}
STOP_FLAGS: dict[str, threading.Event] = {}

# 任务台账文件放在 data 目录下，避免与 data/backtest/*.json 结果文件扫描冲突
STATE_FILE = config.DATA_DIR / "backtest_tasks_state.json"

# 台账只存轻量字段（progress 可能很大，且进程死亡后无法恢复，不持久化）
_LEDGER_FIELDS = ("task_id", "status", "created_at", "params", "metrics", "result_file", "error")


def _persist() -> None:
    """把当前内存任务台账（不含 progress）写入磁盘。"""
    try:
        ledger = [{k: t.get(k) for k in _LEDGER_FIELDS} for t in RUNNING.values()]
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(ledger, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except Exception:  # noqa: BLE001
        pass


def _recover() -> None:
    """启动时恢复上次进程遗留的任务：running → interrupted，其余原样保留。"""
    try:
        if not STATE_FILE.exists():
            return
        ledger = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return
    for t in ledger:
        if not isinstance(t, dict) or not t.get("task_id"):
            continue
        tid = t["task_id"]
        if tid in RUNNING:
            continue
        if t.get("status") == "running":
            t["status"] = "interrupted"
            t["error"] = "后端进程重启，任务中断（可重新运行）"
        t.setdefault("progress", None)
        RUNNING[tid] = t
    # 立即落盘，避免「恢复后又被下一次重启覆盖」导致中断任务再次丢失
    if ledger:
        _persist()


def create_task(params: dict) -> str:
    task_id = uuid.uuid4().hex[:8]
    STOP_FLAGS[task_id] = threading.Event()
    RUNNING[task_id] = {
        "task_id": task_id,
        "status": "running",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "params": params,
        "progress": None,
        "metrics": None,
        "result_file": None,
        "error": None,
    }
    _persist()
    return task_id


def update_progress(task_id: str, progress: dict) -> None:
    t = RUNNING.get(task_id)
    if t:
        t["progress"] = progress


def complete_task(task_id: str, result_file: str, metrics: dict) -> None:
    t = RUNNING.get(task_id)
    if t:
        t["status"] = "done"
        t["result_file"] = result_file
        t["metrics"] = metrics
        _persist()


def fail_task(task_id: str, error: str) -> None:
    t = RUNNING.get(task_id)
    if t:
        t["status"] = "error"
        t["error"] = error
        _persist()


def should_stop(task_id: str) -> bool:
    ev = STOP_FLAGS.get(task_id)
    return ev.is_set() if ev else True


def stop_task(task_id: str) -> bool:
    ev = STOP_FLAGS.get(task_id)
    if ev is None:
        return False
    ev.set()
    t = RUNNING.get(task_id)
    if t and t["status"] == "running":
        t["status"] = "stopped"
        _persist()
    return True


def remove_task(task_id: str) -> bool:
    """删除任务：运行中任务终止并从内存移除；历史任务删除结果文件。"""
    if task_id in RUNNING:
        stop_task(task_id)
        del RUNNING[task_id]
        STOP_FLAGS.pop(task_id, None)
        _persist()
        return True
    if task_id.startswith("hist_"):
        stem = task_id[len("hist_"):]
        f = RESULT_DIR / f"{stem}.json"
        if f.exists():
            f.unlink()
            return True
    return False


def get_task(task_id: str) -> dict:
    return RUNNING.get(task_id, {"task_id": task_id, "status": "unknown"})


def list_all_tasks() -> list[dict]:
    """运行中任务 + 历史任务（扫描结果文件），按时间倒序。

    已完成任务同时存在于内存台账与结果文件，这里按 result_file 去重，
    只保留内存里的那份（其 task_id 为启动时返回的 uuid，前端据此选中）。
    """
    tasks = list(RUNNING.values())
    seen_files = {t.get("result_file") for t in tasks if t.get("result_file")}
    if RESULT_DIR.exists():
        for f in sorted(RESULT_DIR.glob("*.json"), reverse=True):
            if f.name in seen_files:
                continue
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                tasks.append(
                    {
                        "task_id": "hist_" + f.stem,
                        "status": "done",
                        "created_at": "",
                        "params": d.get("params"),
                        "progress": None,
                        "metrics": d.get("metrics"),
                        "result_file": f.name,
                        "error": None,
                    }
                )
            except Exception:  # noqa: BLE001
                continue
    # 按发起时间（created_at）降序；无 created_at 的历史任务（扫描结果文件）排到末尾，
    # 它们之间仍保持按结果文件名（回测区间）降序的稳定顺序。
    tasks.sort(key=lambda t: t.get("created_at") or "", reverse=True)
    return tasks


# 模块加载时恢复上次进程遗留任务（把「进行中却随进程消失」的任务标记为 interrupted）
_recover()
