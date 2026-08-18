"""数据湖自动增量更新调度器。

每个交易日收盘后自动触发一次 ``updater.incremental_update``，实现「动态更新
每个交易数据」：全市场当日 OHLCV / 成交额 / 换手 / PE / PB / 流通市值 / 名称
随最新行情自动落库，无需人工干预。调度为轻量后台线程（不依赖 APScheduler），
随 FastAPI 生命周期启停；也可手动调用 ``run_once``。
"""
from __future__ import annotations

import os
import threading
from datetime import datetime, time as dtime

from . import updater


class DataUpdateScheduler:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._running = False
        self._last_run_ts: str | None = None
        self._last_run_ok: bool | None = None
        self._last_error: str | None = None

    @property
    def enabled(self) -> bool:
        return os.getenv("DATA_AUTO_UPDATE", "1") == "1"

    def _run_after(self) -> tuple[int, int]:
        """收盘后允许运行的最早时刻（UTC，默认 08:00 = 北京时间 16:00）。"""
        s = os.getenv("DATA_UPDATE_AFTER", "08:00")
        try:
            h, m = s.split(":")
            return int(h), int(m)
        except ValueError:
            return 8, 0

    def _poll_seconds(self) -> int:
        try:
            return int(os.getenv("DATA_UPDATE_POLL", "300"))
        except ValueError:
            return 300

    def _already_run_today(self) -> bool:
        status = updater.get_update_status()
        last = status.get("last_update") or {}
        if last.get("kind") != "incremental":
            return False
        # 仅「成功」的增量更新才算已完成当日更新；失败或盘中拦截（ok=False）不阻塞后续重试。
        if not last.get("ok"):
            return False
        ts = last.get("ts", "")
        return ts[:10] == datetime.now().strftime("%Y-%m-%d")

    def run_once(self) -> dict:
        """立即执行一次增量更新（线程安全，供 API 手动触发复用）。"""
        with self._lock:
            try:
                result = updater.incremental_update()
                self._last_run_ts = datetime.now().isoformat(timespec="seconds")
                self._last_run_ok = bool(result.get("ok"))
                self._last_error = result.get("error")
                return result
            except Exception as e:  # noqa: BLE001
                self._last_run_ts = datetime.now().isoformat(timespec="seconds")
                self._last_run_ok = False
                self._last_error = f"{type(e).__name__}: {e}"
                return {"ok": False, "error": self._last_error}

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self.enabled and self._should_run_now():
                    self.run_once()
            except Exception:  # noqa: BLE001
                pass
            self._stop.wait(self._poll_seconds())

    def _should_run_now(self) -> bool:
        now = datetime.now()
        if now.weekday() >= 5:  # 周末
            return False
        h, m = self._run_after()
        if (now.hour, now.minute) < (h, m):
            return False
        if self._already_run_today():
            return False
        return True

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="data-update-scheduler", daemon=True)
        self._thread.start()
        self._running = True

    def stop(self) -> None:
        self._stop.set()
        self._running = False

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "running": self._running,
            "last_run_ts": self._last_run_ts,
            "last_run_ok": self._last_run_ok,
            "last_error": self._last_error,
        }


scheduler = DataUpdateScheduler()
