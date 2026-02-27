"""
LADA - Execution Audit
Structured JSON audit trail for every task.
Writes crash dumps on failure.
Enables step replay for debugging.
"""

import json
import time
from pathlib import Path
from typing import Optional, List
from datetime import datetime
from utils.logger import LADALogger

logger = LADALogger("AUDIT")

AUDIT_DIR = Path(__file__).parent.parent / "memory" / "audit"


class AuditEvent:
    TASK_START    = "task_start"
    TASK_END      = "task_end"
    STEP_START    = "step_start"
    STEP_END      = "step_end"
    VERIFY_START  = "verify_start"
    VERIFY_END    = "verify_end"
    RECOVERY      = "recovery"
    ROLLBACK      = "rollback"
    WATCHDOG      = "watchdog_alert"
    CONTEXT_SNAP  = "context_snapshot"


class ExecutionAudit:
    """
    Records a structured JSON audit log for each task execution.
    On failure, writes a crash dump with full context.
    """

    def __init__(self):
        self._events: List[dict]  = []
        self._task_id: str        = ""
        self._task_name: str      = ""
        self._start_time: float   = 0.0
        self._active: bool        = False
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)

    # ── Task lifecycle ─────────────────────────────────────

    def start_task(self, task_name: str, command: str, mode: str = "live"):
        self._task_id   = f"{task_name}_{int(time.time())}"
        self._task_name = task_name
        self._start_time = time.monotonic()
        self._events.clear()
        self._active = True
        self._record(AuditEvent.TASK_START, {
            "task_name": task_name,
            "command":   command,
            "mode":      mode,
        })

    def end_task(self, success: bool, error: str = "", context_snap: dict = None):
        elapsed = time.monotonic() - self._start_time
        self._record(AuditEvent.TASK_END, {
            "success":    success,
            "error":      error,
            "elapsed_s":  round(elapsed, 2),
            "total_steps": sum(
                1 for e in self._events
                if e["event"] == AuditEvent.STEP_START
            ),
        })
        if context_snap:
            self._record(AuditEvent.CONTEXT_SNAP, context_snap)

        self._save_log(success)
        if not success:
            self._write_crash_dump(error, context_snap)
        self._active = False

    # ── Step events ────────────────────────────────────────

    def step_start(self, step_id: str, action: str, value: str, method: str):
        self._record(AuditEvent.STEP_START, {
            "step_id": step_id,
            "action":  action,
            "value":   value,
            "method":  method,
        })

    def step_end(
        self,
        step_id: str,
        action:  str,
        success: bool,
        method_used: str = "",
        error:   str = "",
        dur_ms:  float = 0.0,
        error_code: str = "",
    ):
        self._record(AuditEvent.STEP_END, {
            "step_id":    step_id,
            "action":     action,
            "success":    success,
            "method":     method_used,
            "error":      error,
            "error_code": error_code,
            "dur_ms":     round(dur_ms, 1),
        })

    def recovery_event(self, step_id: str, strategy: str, attempt: int):
        self._record(AuditEvent.RECOVERY, {
            "step_id":  step_id,
            "strategy": strategy,
            "attempt":  attempt,
        })

    def rollback_event(self, rollback_results: list):
        self._record(AuditEvent.ROLLBACK, {
            "count":   len(rollback_results),
            "results": rollback_results,
        })

    def watchdog_event(self, event_type: str, message: str):
        self._record(AuditEvent.WATCHDOG, {
            "type":    event_type,
            "message": message,
        })

    # ── Internal ───────────────────────────────────────────

    def _record(self, event: str, data: dict):
        if not self._active and event != AuditEvent.TASK_START:
            return
        entry = {
            "ts":    round(time.monotonic() - self._start_time, 3),
            "event": event,
            **data,
        }
        self._events.append(entry)

    def _save_log(self, success: bool):
        """Write full audit log to disk."""
        try:
            status   = "ok" if success else "fail"
            filename = f"{self._task_id}_{status}.json"
            path     = AUDIT_DIR / filename
            payload  = {
                "task_id":   self._task_id,
                "task_name": self._task_name,
                "saved_at":  datetime.now().isoformat(),
                "events":    self._events,
            }
            with open(path, "w") as f:
                json.dump(payload, f, indent=2)
            logger.debug(f"Audit log saved: {path.name}")
        except Exception as e:
            logger.debug(f"Audit save error: {e}")

    def _write_crash_dump(self, error: str, context: Optional[dict]):
        """Write a crash dump with full context for failed tasks."""
        try:
            filename = f"CRASH_{self._task_id}.json"
            path     = AUDIT_DIR / filename
            dump = {
                "crash_at":    datetime.now().isoformat(),
                "task_id":     self._task_id,
                "task_name":   self._task_name,
                "error":       error,
                "context":     context or {},
                "event_count": len(self._events),
                "last_events": self._events[-10:],
                "full_log":    self._events,
            }
            with open(path, "w") as f:
                json.dump(dump, f, indent=2)
            logger.warning(f"CRASH DUMP written: {path.name}")
        except Exception as e:
            logger.debug(f"Crash dump error: {e}")

    # ── Replay support ─────────────────────────────────────

    def get_replay_steps(self) -> List[dict]:
        """
        Extract the successful step sequence for replay.
        Returns list of step dicts in execution order.
        """
        replay = []
        for e in self._events:
            if e["event"] == AuditEvent.STEP_END and e.get("success"):
                replay.append({
                    "action": e.get("action"),
                    "method": e.get("method"),
                    "dur_ms": e.get("dur_ms"),
                })
        return replay

    @classmethod
    def load_crash_dump(cls, path: str) -> dict:
        """Load a crash dump for post-mortem analysis."""
        with open(path) as f:
            return json.load(f)

    @classmethod
    def list_crash_dumps(cls) -> List[Path]:
        return sorted(AUDIT_DIR.glob("CRASH_*.json"), key=lambda p: p.stat().st_mtime)
