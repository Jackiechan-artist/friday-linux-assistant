"""
LADA - ActionResult (Deterministic Contract)

Every executor in LADA returns ActionResult.
No plain bool. No bare exception. No None.

Fields:
  success          : did the action succeed?
  error_code       : machine-readable error category
  recovery_hint    : what recovery strategy to try
  execution_time_ms: actual time taken in milliseconds
  metadata         : freeform data (screenshot path, element info, etc.)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Any
from datetime import datetime
import time


# ── Standard error codes ──────────────────────────────────
class ECode:
    """Machine-readable error codes. Matches ErrorClassifier.ErrorClass."""
    NONE              = ""
    ELEMENT_NOT_FOUND = "element_not_found"
    WINDOW_NOT_OPEN   = "window_not_open"
    TIMEOUT           = "timeout"
    PROCESS_DEAD      = "process_dead"
    PERMISSION_DENIED = "permission_denied"
    BROWSER_CRASH     = "browser_crash"
    NETWORK_ERROR     = "network_error"
    SCHEMA_INVALID    = "schema_invalid"
    ACCESSIBILITY_FAIL = "accessibility_fail"
    CV_NO_MATCH       = "cv_no_match"
    COMMAND_FAILED    = "command_failed"
    UNKNOWN           = "unknown"


# ── Recovery hints ────────────────────────────────────────
class RHint:
    """Human + machine readable recovery hint."""
    NONE               = ""
    SWITCH_METHOD      = "switch_method"
    REOPEN_APP         = "reopen_app"
    RETRY_LONGER_WAIT  = "retry_with_longer_wait"
    RELAUNCH_PROCESS   = "relaunch_process"
    SKIP               = "skip_or_escalate"
    RESTART_BROWSER    = "restart_browser"
    RETRY_BACKOFF      = "retry_with_backoff"
    REPLAN             = "replan"
    SWITCH_CV          = "switch_to_cv"
    SWITCH_OCR         = "switch_to_ocr"
    ALT_COMMAND        = "alternative_command"
    FULL_RESET         = "full_reset"


# Error code → recommended recovery hint
_CODE_TO_HINT: dict[str, str] = {
    ECode.ELEMENT_NOT_FOUND:  RHint.SWITCH_METHOD,
    ECode.WINDOW_NOT_OPEN:    RHint.REOPEN_APP,
    ECode.TIMEOUT:            RHint.RETRY_LONGER_WAIT,
    ECode.PROCESS_DEAD:       RHint.RELAUNCH_PROCESS,
    ECode.PERMISSION_DENIED:  RHint.SKIP,
    ECode.BROWSER_CRASH:      RHint.RESTART_BROWSER,
    ECode.NETWORK_ERROR:      RHint.RETRY_BACKOFF,
    ECode.SCHEMA_INVALID:     RHint.REPLAN,
    ECode.ACCESSIBILITY_FAIL: RHint.SWITCH_CV,
    ECode.CV_NO_MATCH:        RHint.SWITCH_OCR,
    ECode.COMMAND_FAILED:     RHint.ALT_COMMAND,
    ECode.UNKNOWN:            RHint.FULL_RESET,
}


@dataclass
class ActionResult:
    """
    Deterministic contract returned by every executor.
    Never raise — always return ActionResult.
    """
    success           : bool
    action            : str   = ""
    value             : str   = ""
    method            : str   = ""
    error             : str   = ""
    error_code        : str   = ECode.NONE
    recovery_hint     : str   = RHint.NONE
    execution_time_ms : float = 0.0
    metadata          : dict  = field(default_factory=dict)
    attempt           : int   = 1
    timestamp         : str   = field(
        default_factory=lambda: datetime.now().isoformat()
    )

    def __bool__(self) -> bool:
        return self.success

    def __repr__(self) -> str:
        mark = "✓" if self.success else "✗"
        base = (
            f"ActionResult({mark} {self.action}={self.value!r} "
            f"method={self.method} {self.execution_time_ms:.0f}ms"
        )
        if not self.success:
            base += f" [{self.error_code}] hint={self.recovery_hint!r}"
            if self.error:
                base += f" err={self.error[:50]!r}"
        return base + ")"

    # ── Factory methods ──────────────────────────────────

    @classmethod
    def ok(
        cls,
        action            : str  = "",
        value             : str  = "",
        method            : str  = "",
        execution_time_ms : float = 0.0,
        metadata          : Optional[dict] = None,
    ) -> "ActionResult":
        return cls(
            success=True,
            action=action,
            value=value,
            method=method,
            execution_time_ms=execution_time_ms,
            metadata=metadata or {},
            error_code=ECode.NONE,
            recovery_hint=RHint.NONE,
        )

    @classmethod
    def fail(
        cls,
        action            : str  = "",
        value             : str  = "",
        method            : str  = "",
        error             : str  = "",
        error_code        : str  = ECode.UNKNOWN,
        recovery_hint     : str  = "",
        execution_time_ms : float = 0.0,
        metadata          : Optional[dict] = None,
    ) -> "ActionResult":
        hint = recovery_hint or _CODE_TO_HINT.get(error_code, RHint.FULL_RESET)
        return cls(
            success=False,
            action=action,
            value=value,
            method=method,
            error=error,
            error_code=error_code,
            recovery_hint=hint,
            execution_time_ms=execution_time_ms,
            metadata=metadata or {},
        )

    @classmethod
    def from_exception(
        cls,
        exc    : Exception,
        action : str = "",
        value  : str = "",
        method : str = "",
        t_start: Optional[float] = None,
    ) -> "ActionResult":
        """Build a fail result from a caught exception."""
        elapsed = (time.monotonic() - t_start) * 1000 if t_start else 0.0
        err_msg = str(exc)

        # Auto-classify exception type
        exc_name = type(exc).__name__.lower()
        if "timeout" in exc_name:
            code = ECode.TIMEOUT
        elif "permission" in exc_name or "access" in err_msg.lower():
            code = ECode.PERMISSION_DENIED
        elif "filenotfound" in exc_name or "notfound" in exc_name:
            code = ECode.ELEMENT_NOT_FOUND
        else:
            code = ECode.UNKNOWN

        return cls.fail(
            action=action,
            value=value,
            method=method,
            error=err_msg,
            error_code=code,
            execution_time_ms=elapsed,
        )

    # ── Utilities ────────────────────────────────────────

    def with_attempt(self, n: int) -> "ActionResult":
        self.attempt = n
        return self

    def with_metadata(self, key: str, val: Any) -> "ActionResult":
        self.metadata[key] = val
        return self

    def to_dict(self) -> dict:
        return {
            "success":           self.success,
            "action":            self.action,
            "value":             self.value,
            "method":            self.method,
            "error":             self.error,
            "error_code":        self.error_code,
            "recovery_hint":     self.recovery_hint,
            "execution_time_ms": round(self.execution_time_ms, 1),
            "metadata":          self.metadata,
            "attempt":           self.attempt,
            "timestamp":         self.timestamp,
        }

    @property
    def needs_recovery(self) -> bool:
        return not self.success and self.recovery_hint != RHint.NONE

    @property
    def is_retryable(self) -> bool:
        return not self.success and self.error_code not in (
            ECode.PERMISSION_DENIED,
            ECode.SCHEMA_INVALID,
        )
