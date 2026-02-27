"""
LADA - Retry Policy v3
Dynamic fallback: failure counter per method adjusts priority.

Static priority (v2):
  accessibility → cv → system  (always same)

Dynamic priority (v3):
  - If accessibility fails 3 times → deprioritize it
  - Elevate CV if it has been succeeding
  - Use ErrorClassifier to pick best next method
"""

import asyncio
import random
import time
from typing import Callable, Optional, List
from dataclasses import dataclass
from core.error_classifier import ErrorClassifier
from utils.logger import LADALogger

logger = LADALogger("RETRY_POLICY")

# ── Static fallback chains (baseline) ─────────────────────
METHOD_PRIORITY: dict[str, List[str]] = {
    "open_app":          ["system", "accessibility", "cv"],
    "open_terminal":     ["system"],
    "find_and_click":    ["accessibility", "browser", "cv"],
    "click_button":      ["accessibility", "browser", "cv"],
    "click_result":      ["accessibility", "browser", "cv"],
    "type_text":         ["accessibility", "browser", "system"],
    "search":            ["accessibility", "browser"],
    "navigate":          ["browser"],
    "focus_window":      ["system", "accessibility"],
    "close_window":      ["system", "accessibility"],
    "set_volume":        ["system"],
    "set_brightness":    ["system"],
    "run_command":       ["system"],
    "verify_window":     ["system", "accessibility"],
    "wait_for_element":  ["accessibility", "browser"],
    "scroll":            ["browser", "system", "cv"],
    "get_text":          ["accessibility", "browser", "cv"],
    "open_menu":         ["system", "accessibility"],
}
DEFAULT_PRIORITY = ["accessibility", "system", "browser", "cv"]

# If a method fails this many times → move it to end of chain
FAILURE_DEMOTION_THRESHOLD = 3


@dataclass
class RetryConfig:
    max_attempts:   int   = 3
    base_delay_s:   float = 0.5
    backoff_factor: float = 1.5
    max_delay_s:    float = 4.0
    jitter:         bool  = True


class RetryPolicy:
    """
    Smart retry with dynamic method priority.
    Tracks per-method failure counts within a task session.
    Demotes methods that repeatedly fail.
    """

    def __init__(
        self,
        config:     Optional[RetryConfig] = None,
        classifier: Optional[ErrorClassifier] = None,
    ):
        self.config     = config or RetryConfig()
        self.classifier = classifier or ErrorClassifier()

        # Session-level failure counters: action+method → count
        # Resets per Orchestrator.run() call
        self._fail_counts: dict[str, int] = {}

    def reset_session(self):
        """Call at start of each task to reset session counters."""
        self._fail_counts.clear()

    def get_fallback_chain(self, action: str, current_method: str) -> List[str]:
        """
        Return methods to try after current_method fails.
        Applies dynamic demotion based on session failure counts.
        """
        baseline = METHOD_PRIORITY.get(action, DEFAULT_PRIORITY)

        # Remove current method from chain
        candidates = [m for m in baseline if m != current_method]

        # Sort: deprioritize heavily-failed methods
        def sort_key(method):
            key     = f"{action}:{method}"
            fails   = self._fail_counts.get(key, 0)
            return fails   # lower = better

        candidates.sort(key=sort_key)
        return candidates

    def _record_failure(self, action: str, method: str):
        key = f"{action}:{method}"
        self._fail_counts[key] = self._fail_counts.get(key, 0) + 1
        count = self._fail_counts[key]
        if count >= FAILURE_DEMOTION_THRESHOLD:
            logger.warning(
                f"Method '{method}' demoted for '{action}' "
                f"({count} failures this session)"
            )

    async def execute_with_retry(
        self,
        fn: Callable,
        step: dict,
        label: str = "",
    ):
        """
        Execute fn(step) with smart retry + dynamic fallback.
        Returns ActionResult or None on total failure.
        """
        action   = step.get("action", "unknown")
        method   = step.get("method", "accessibility")
        name     = label or f"{action}={step.get('value', '')!r}"
        cfg      = self.config

        # ── Level 1: Retry same method ─────────────────────
        for attempt in range(1, cfg.max_attempts + 1):
            try:
                result = await fn({**step, "method": method})
                logger.debug(f"[{name}] raw result={result!r} bool={bool(result) if result is not None else 'None'}")
                if result:
                    return result

                # Record failure
                self._record_failure(action, method)
                logger.debug(
                    f"[{name}] Attempt {attempt}/{cfg.max_attempts} "
                    f"failed (method={method}) result={result!r}"
                )

            except asyncio.TimeoutError:
                self._record_failure(action, method)
                logger.warning(f"[{name}] Timeout (attempt {attempt}, method={method})")
            except Exception as e:
                self._record_failure(action, method)
                logger.debug(f"[{name}] Exception attempt {attempt}: {e}")

            if attempt < cfg.max_attempts:
                await asyncio.sleep(self._delay(attempt))

        # ── Level 2: Dynamic fallback methods ──────────────
        fallbacks = self.get_fallback_chain(action, method)
        logger.info(
            f"[{name}] Level 2 fallbacks: {fallbacks}"
        )

        for fb_method in fallbacks:
            if self._fail_counts.get(f"{action}:{fb_method}", 0) >= FAILURE_DEMOTION_THRESHOLD:
                logger.debug(f"[{name}] Skipping demoted method: {fb_method}")
                continue

            logger.info(f"[{name}] Trying fallback method: {fb_method}")
            try:
                result = await fn({**step, "method": fb_method})
                if result:
                    logger.info(f"[{name}] Fallback succeeded: {fb_method}")
                    return result
                self._record_failure(action, fb_method)
            except Exception as e:
                self._record_failure(action, fb_method)
                logger.debug(f"[{name}] Fallback {fb_method} exception: {e}")
            await asyncio.sleep(0.5)

        # Last resort: if single-method action (run_command, set_volume), try one more time
        single_method_actions = {"run_command", "set_volume", "set_brightness", "open_terminal"}
        if action in single_method_actions:
            logger.info(f"[{name}] Last resort attempt for system-only action")
            try:
                result = await fn({**step, "method": "system"})
                if result:
                    return result
            except Exception:
                pass

        logger.error(f"[{name}] All retries and fallbacks exhausted.")
        return None

    def _delay(self, attempt: int) -> float:
        cfg   = self.config
        delay = min(cfg.base_delay_s * (cfg.backoff_factor ** (attempt - 1)), cfg.max_delay_s)
        if cfg.jitter:
            delay *= 0.8 + random.random() * 0.4
        return delay

    def get_session_stats(self) -> dict:
        return {
            "failure_counts": dict(self._fail_counts),
            "demoted_methods": [
                k for k, v in self._fail_counts.items()
                if v >= FAILURE_DEMOTION_THRESHOLD
            ],
        }
