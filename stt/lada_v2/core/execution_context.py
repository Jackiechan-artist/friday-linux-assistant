"""
LADA - Execution Context v2

Upgrades from audit:
  ✔ Tracks runtime state: active_window, focused_element, active_app
  ✔ Tracks last_success_method, retry_counter, system_load
  ✔ Controlled access (properties with validation, not raw dict)
  ✔ Thread-safe updates via property setters
  ✔ dry_run / safe_mode unchanged
  ✔ Context snapshot for debugging / crash dumps
"""

from __future__ import annotations
import threading
import time
import subprocess
from enum import Enum
from typing import Optional
from dataclasses import dataclass, field
from utils.logger import LADALogger

logger = LADALogger("EXEC_CONTEXT")


class ExecMode(Enum):
    LIVE      = "live"
    DRY_RUN   = "dry_run"
    SAFE_MODE = "safe_mode"


# Actions allowed in SAFE_MODE
SAFE_ACTIONS = {
    "verify_window", "focus_window", "get_text",
    "wait_for_element", "scroll", "set_volume", "set_brightness",
}
BLOCKED_IN_SAFE = {"run_command", "close_window", "type_text", "click_button"}


class ExecutionContext:
    """
    Tracks execution state for one task run.
    Provides controlled access — not a raw mutable dict.
    Thread-safe via internal lock.
    """

    def __init__(self, mode: ExecMode = ExecMode.LIVE, task_name: str = ""):
        self.mode       = mode
        self.task_name  = task_name
        self._lock      = threading.Lock()

        # ── Runtime state (controlled via properties) ──
        self._active_window     : str   = ""
        self._focused_element   : str   = ""
        self._active_app        : str   = ""
        self._last_success_method: str  = ""
        self._retry_counter     : int   = 0
        self._system_load       : float = 0.0   # CPU %
        self._step_start_time   : float = 0.0

        # ── Execution log ──
        self._blocked_count : int        = 0
        self._dry_run_log   : list       = []
        self._step_history  : list[dict] = []

    # ── Runtime state properties ──────────────────────────

    @property
    def active_window(self) -> str:
        with self._lock:
            return self._active_window

    @active_window.setter
    def active_window(self, title: str):
        with self._lock:
            if title != self._active_window:
                logger.debug(f"ActiveWindow: {self._active_window!r} → {title!r}")
                self._active_window = title

    @property
    def focused_element(self) -> str:
        with self._lock:
            return self._focused_element

    @focused_element.setter
    def focused_element(self, name: str):
        with self._lock:
            self._focused_element = name

    @property
    def active_app(self) -> str:
        with self._lock:
            return self._active_app

    @active_app.setter
    def active_app(self, app: str):
        with self._lock:
            if app != self._active_app:
                logger.debug(f"ActiveApp: {self._active_app!r} → {app!r}")
                self._active_app = app

    @property
    def last_success_method(self) -> str:
        with self._lock:
            return self._last_success_method

    @last_success_method.setter
    def last_success_method(self, method: str):
        with self._lock:
            self._last_success_method = method

    @property
    def retry_counter(self) -> int:
        with self._lock:
            return self._retry_counter

    def increment_retry(self):
        with self._lock:
            self._retry_counter += 1
            return self._retry_counter

    def reset_retry(self):
        with self._lock:
            self._retry_counter = 0

    @property
    def system_load(self) -> float:
        with self._lock:
            return self._system_load

    def refresh_system_load(self):
        """Update system_load from actual CPU reading."""
        try:
            import psutil
            with self._lock:
                self._system_load = psutil.cpu_percent(interval=None)
        except ImportError:
            pass

    # ── Sync from live system ─────────────────────────────

    def sync_active_window(self):
        """Read the currently active window title from wmctrl/xdotool."""
        try:
            r = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowname"],
                capture_output=True, text=True, timeout=2,
            )
            if r.returncode == 0:
                self.active_window = r.stdout.strip()
        except Exception:
            pass

    def record_step_start(self, action: str, value: str):
        """Call when a step begins."""
        with self._lock:
            self._step_start_time = time.monotonic()
        self.sync_active_window()
        self.refresh_system_load()

    def record_step_end(self, action: str, method: str, success: bool):
        """Call when a step completes."""
        elapsed = time.monotonic() - self._step_start_time
        entry = {
            "action":  action,
            "method":  method,
            "success": success,
            "elapsed_ms": round(elapsed * 1000, 1),
        }
        with self._lock:
            self._step_history.append(entry)
            if success:
                self._last_success_method = method
                self._retry_counter = 0

    # ── Execution mode gate ───────────────────────────────

    def can_execute(self, step: dict) -> bool:
        action = step.get("action", "")

        if self.mode == ExecMode.LIVE:
            return True

        if self.mode == ExecMode.DRY_RUN:
            self._record_dry(step, "would execute")
            return False

        if self.mode == ExecMode.SAFE_MODE:
            if action in BLOCKED_IN_SAFE:
                self._blocked_count += 1
                self._record_dry(step, "BLOCKED safe_mode")
                return False
            if action not in SAFE_ACTIONS:
                self._blocked_count += 1
                self._record_dry(step, "BLOCKED: not in safe whitelist")
                return False
            return True

        return True

    def simulate(self, plan: dict) -> list[dict]:
        """Simulate full plan in DRY_RUN, return log."""
        old = self.mode
        self.mode = ExecMode.DRY_RUN
        self._dry_run_log.clear()
        for step in plan.get("steps", []):
            self.can_execute(step)
        self.mode = old
        return list(self._dry_run_log)

    def _record_dry(self, step: dict, note: str):
        entry = {
            "action": step.get("action"),
            "value":  step.get("value"),
            "method": step.get("method"),
            "note":   note,
        }
        self._dry_run_log.append(entry)
        logger.info(
            f"[{self.mode.value.upper()}] "
            f"{step.get('action')}={step.get('value')!r} ← {note}"
        )

    # ── Snapshot ──────────────────────────────────────────

    def snapshot(self) -> dict:
        """Full context snapshot for crash dumps or debugging."""
        with self._lock:
            return {
                "mode":                self.mode.value,
                "task_name":           self.task_name,
                "active_window":       self._active_window,
                "focused_element":     self._focused_element,
                "active_app":          self._active_app,
                "last_success_method": self._last_success_method,
                "retry_counter":       self._retry_counter,
                "system_load_pct":     round(self._system_load, 1),
                "blocked_count":       self._blocked_count,
                "step_history_count":  len(self._step_history),
                "recent_steps":        self._step_history[-5:],
            }

    def get_report(self) -> dict:
        return self.snapshot()


# ── Factory ───────────────────────────────────────────────

def make_context(mode: str = "live", task_name: str = "") -> ExecutionContext:
    mode_map = {
        "live":      ExecMode.LIVE,
        "dry_run":   ExecMode.DRY_RUN,
        "dry":       ExecMode.DRY_RUN,
        "safe":      ExecMode.SAFE_MODE,
        "safe_mode": ExecMode.SAFE_MODE,
    }
    m = mode_map.get(mode.lower(), ExecMode.LIVE)
    return ExecutionContext(mode=m, task_name=task_name)
