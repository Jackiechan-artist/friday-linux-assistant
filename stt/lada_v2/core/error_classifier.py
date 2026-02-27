"""
LADA - Error Classifier
Structured error taxonomy.

Instead of "something failed" → know exactly WHAT failed
and which recovery strategy to apply.

Error classes:
  ELEMENT_NOT_FOUND  → switch method (accessibility → cv → ocr)
  WINDOW_NOT_OPEN    → reopen app
  TIMEOUT            → increase wait, retry
  PROCESS_DEAD       → relaunch
  PERMISSION_DENIED  → escalate or skip
  BROWSER_CRASH      → restart browser
  NETWORK_ERROR      → retry with backoff
  UNKNOWN            → full reset
"""

import re
from enum import Enum
from dataclasses import dataclass
from typing import Optional
from utils.logger import LADALogger

logger = LADALogger("ERROR_CLASSIFIER")


class ErrorClass(Enum):
    ELEMENT_NOT_FOUND  = "element_not_found"
    WINDOW_NOT_OPEN    = "window_not_open"
    TIMEOUT            = "timeout"
    PROCESS_DEAD       = "process_dead"
    PERMISSION_DENIED  = "permission_denied"
    BROWSER_CRASH      = "browser_crash"
    NETWORK_ERROR      = "network_error"
    SCHEMA_INVALID     = "schema_invalid"
    ACCESSIBILITY_FAIL = "accessibility_fail"
    CV_NO_MATCH        = "cv_no_match"
    COMMAND_FAILED     = "command_failed"
    UNKNOWN            = "unknown"


# Which recovery strategy to apply for each error class
RECOVERY_STRATEGY: dict[ErrorClass, str] = {
    ErrorClass.ELEMENT_NOT_FOUND:  "switch_method",
    ErrorClass.WINDOW_NOT_OPEN:    "reopen_app",
    ErrorClass.TIMEOUT:            "retry_with_longer_wait",
    ErrorClass.PROCESS_DEAD:       "relaunch_process",
    ErrorClass.PERMISSION_DENIED:  "skip_or_escalate",
    ErrorClass.BROWSER_CRASH:      "restart_browser",
    ErrorClass.NETWORK_ERROR:      "retry_with_backoff",
    ErrorClass.SCHEMA_INVALID:     "replan",
    ErrorClass.ACCESSIBILITY_FAIL: "switch_to_cv",
    ErrorClass.CV_NO_MATCH:        "switch_to_ocr",
    ErrorClass.COMMAND_FAILED:     "alternative_command",
    ErrorClass.UNKNOWN:            "full_reset",
}

# How many retries are sensible for each class
RETRY_BUDGET: dict[ErrorClass, int] = {
    ErrorClass.ELEMENT_NOT_FOUND:  3,
    ErrorClass.WINDOW_NOT_OPEN:    2,
    ErrorClass.TIMEOUT:            3,
    ErrorClass.PROCESS_DEAD:       1,
    ErrorClass.PERMISSION_DENIED:  0,    # don't retry — will keep failing
    ErrorClass.BROWSER_CRASH:      1,
    ErrorClass.NETWORK_ERROR:      4,
    ErrorClass.SCHEMA_INVALID:     1,
    ErrorClass.ACCESSIBILITY_FAIL: 2,
    ErrorClass.CV_NO_MATCH:        2,
    ErrorClass.COMMAND_FAILED:     2,
    ErrorClass.UNKNOWN:            1,
}


@dataclass
class ClassifiedError:
    error_class   : ErrorClass
    original_msg  : str
    strategy      : str
    retry_budget  : int
    details       : Optional[str] = None

    def __str__(self):
        return (
            f"[{self.error_class.value}] "
            f"strategy={self.strategy} "
            f"retries_left={self.retry_budget} "
            f"msg={self.original_msg[:60]!r}"
        )

    def should_retry(self) -> bool:
        return self.retry_budget > 0

    def consume_retry(self) -> "ClassifiedError":
        self.retry_budget = max(0, self.retry_budget - 1)
        return self


class ErrorClassifier:
    """
    Classifies exception messages and action results
    into structured error classes.
    """

    # ── Pattern matching rules ─────────────────────────────
    # (regex pattern, ErrorClass)
    _PATTERNS = [
        # Timeout patterns
        (r"timeout|timed? out|asyncio\.timeouterror",   ErrorClass.TIMEOUT),

        # Element / UI not found
        (r"element not found|no such element|"
         r"locator.*not visible|element.*invisible|"
         r"find_element|elementnotfound",               ErrorClass.ELEMENT_NOT_FOUND),

        # Window not open
        (r"window not found|wmctrl.*fail|"
         r"no window.*title|window.*does not exist",    ErrorClass.WINDOW_NOT_OPEN),

        # Process dead
        (r"process.*dead|pgrep.*fail|"
         r"no such process|process not running",        ErrorClass.PROCESS_DEAD),

        # Permission
        (r"permission denied|operation not permitted|"
         r"access denied|sudo required",                ErrorClass.PERMISSION_DENIED),

        # Browser crash
        (r"browser.*crash|playwright.*closed|"
         r"page.*crashed|connection refused.*browser|"
         r"target closed",                              ErrorClass.BROWSER_CRASH),

        # Network
        (r"connection refused|network.*error|"
         r"dns.*fail|http.*error [45]\d\d|"
         r"ssl.*error|name.*resolution",                ErrorClass.NETWORK_ERROR),

        # Accessibility
        (r"pyatspi|at-spi|dbus.*error|"
         r"accessibility.*fail|atspi",                  ErrorClass.ACCESSIBILITY_FAIL),

        # CV no match
        (r"template.*not found|no match|"
         r"matchtemplate|confidence.*below",            ErrorClass.CV_NO_MATCH),

        # Schema
        (r"schema.*fail|validation.*fail|"
         r"missing.*field|invalid.*json",               ErrorClass.SCHEMA_INVALID),

        # Command failed
        (r"command.*fail|returncode.*[1-9]|"
         r"subprocess.*error|exit code",                ErrorClass.COMMAND_FAILED),
    ]

    def classify(
        self,
        error_msg: str,
        action:    str  = "",
        method:    str  = "",
    ) -> ClassifiedError:
        """
        Classify an error message into a structured ClassifiedError.
        """
        msg_lower = error_msg.lower()
        matched_class = self._match_patterns(msg_lower)

        # Contextual overrides based on action + method
        if matched_class == ErrorClass.UNKNOWN:
            matched_class = self._contextual_classify(action, method, msg_lower)

        strategy     = RECOVERY_STRATEGY[matched_class]
        retry_budget = RETRY_BUDGET[matched_class]

        classified = ClassifiedError(
            error_class  = matched_class,
            original_msg = error_msg,
            strategy     = strategy,
            retry_budget = retry_budget,
        )

        logger.debug(f"Classified: {classified}")
        return classified

    def classify_result(
        self,
        result,           # ActionResult
        action:  str = "",
        method:  str = "",
    ) -> ClassifiedError:
        """Classify from an ActionResult object."""
        if result and result.success:
            return ClassifiedError(
                error_class  = ErrorClass.UNKNOWN,
                original_msg = "",
                strategy     = "none",
                retry_budget = 0,
            )
        error_msg = getattr(result, "error", "") if result else "null result"
        return self.classify(error_msg, action=action, method=method)

    def _match_patterns(self, msg: str) -> ErrorClass:
        for pattern, error_class in self._PATTERNS:
            if re.search(pattern, msg, re.IGNORECASE):
                return error_class
        return ErrorClass.UNKNOWN

    def _contextual_classify(
        self,
        action: str,
        method: str,
        msg:    str,
    ) -> ErrorClass:
        """
        Apply contextual heuristics when pattern matching fails.
        """
        # Accessibility method with generic failure → accessibility fail
        if method == "accessibility":
            return ErrorClass.ACCESSIBILITY_FAIL

        # CV method with generic failure → no match
        if method == "cv":
            return ErrorClass.CV_NO_MATCH

        # Browser action generic fail → could be element not found
        browser_actions = {"find_and_click", "click_button", "navigate",
                           "type_text", "wait_for_element"}
        if action in browser_actions:
            return ErrorClass.ELEMENT_NOT_FOUND

        # Window-related action fail → window not open
        if action in {"focus_window", "verify_window", "close_window"}:
            return ErrorClass.WINDOW_NOT_OPEN

        return ErrorClass.UNKNOWN

    def get_fallback_method(
        self,
        error_class: ErrorClass,
        current_method: str,
    ) -> Optional[str]:
        """
        Given an error class, suggest which method to try next.
        """
        fallback_map = {
            ErrorClass.ACCESSIBILITY_FAIL: {
                "accessibility": "cv",
                "cv":            "system",
            },
            ErrorClass.CV_NO_MATCH: {
                "cv":            "accessibility",
                "accessibility": "system",
            },
            ErrorClass.ELEMENT_NOT_FOUND: {
                "accessibility": "browser",
                "browser":       "cv",
                "cv":            "system",
            },
            ErrorClass.BROWSER_CRASH: {
                "browser":       "accessibility",
            },
        }
        method_map = fallback_map.get(error_class, {})
        return method_map.get(current_method)
