"""
LADA - Step Executor
Executes ONE step atomically:
  - Routes to correct action layer
  - Applies timeout
  - Returns ActionResult (never raw bool)
  - Logs duration and result
"""

import asyncio
import time
from typing import Optional
from core.action_result import ActionResult
from utils.timeout import TimeoutManager
from utils.logger import LADALogger

logger = LADALogger("STEP_EXECUTOR")


class StepExecutor:
    """
    Executes a single plan step, routing to the correct action layer.
    Always returns ActionResult — never raises (errors are captured).
    """

    def __init__(
        self,
        ui_actions,
        browser_actions,
        system_actions,
        timeout_manager: Optional[TimeoutManager] = None,
        capabilities=None,
    ):
        self.ui_actions = ui_actions
        self.browser_actions = browser_actions
        self.system_actions = system_actions
        self.tm = timeout_manager or TimeoutManager()
        self.capabilities = capabilities

    async def execute(self, step: dict) -> ActionResult:
        """
        Execute one step and return ActionResult.
        Determines best executor based on action + method + capabilities.
        """
        action = step.get("action", "")
        value  = step.get("value", "")
        method = self._resolve_method(step)

        start_ms = time.monotonic() * 1000

        # Pick executor
        executor = self._pick_executor(action, method)

        try:
            coro = executor({**step, "method": method})
            raw = await self.tm.run(coro, action=action)

            duration_ms = time.monotonic() * 1000 - start_ms

            if raw:
                return ActionResult.ok(
                    action=action,
                    value=value,
                    method=method,
                    execution_time_ms=duration_ms,
                )
            else:
                return ActionResult.fail(
                    action=action,
                    value=value,
                    method=method,
                    error="Executor returned falsy result",
                    execution_time_ms=duration_ms,
                )

        except asyncio.TimeoutError:
            duration_ms = time.monotonic() * 1000 - start_ms
            return ActionResult.fail(
                action=action,
                value=value,
                method=method,
                error=f"TimeoutError after {duration_ms:.0f}ms",
                execution_time_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = time.monotonic() * 1000 - start_ms
            return ActionResult.fail(
                action=action,
                value=value,
                method=method,
                error=str(e),
                execution_time_ms=duration_ms,
            )

    def _resolve_method(self, step: dict) -> str:
        """
        Determine the actual execution method.
        Considers: step hint → capabilities → fallback.
        """
        hinted = step.get("method", "auto")

        if hinted and hinted != "auto":
            # Check if hinted method is actually available
            if self.capabilities and not self.capabilities.method_available(hinted):
                logger.debug(
                    f"Hinted method '{hinted}' not available. "
                    f"Using capability best-match."
                )
                return self._capability_best(step.get("action", ""))
            return hinted

        # Auto-resolve from capabilities
        if self.capabilities:
            return self.capabilities.best_method_for(step.get("action", ""))

        # Hardcoded defaults
        action = step.get("action", "")
        browser_native = {"navigate", "find_and_click", "type_text",
                          "scroll", "wait_for_element", "get_text"}
        system_native  = {"set_volume", "set_brightness", "run_command",
                          "focus_window", "close_window", "open_terminal",
                          "open_menu", "verify_window", "navigate_folder"}  # ← FIXED: route to system

        if action in browser_native:
            return "browser"
        if action in system_native:
            return "system"
        return "accessibility"

    def _capability_best(self, action: str) -> str:
        """Best available method from capabilities."""
        if self.capabilities:
            return self.capabilities.best_method_for(action)
        return "system"

    def _pick_executor(self, action: str, method: str):
        """Return the correct executor function for this action + method."""

        # Browser-native actions → always BrowserActions
        browser_actions = {
            "navigate", "wait_for_element", "scroll",
            "get_text", "find_and_click",
        }
        if method == "browser" or action in browser_actions:
            return self.browser_actions.execute

        # System-native → always SystemActions
        system_actions = {
            "set_volume", "set_brightness", "run_command",
            "focus_window", "close_window", "open_terminal",
            "open_menu", "verify_window",  # ← FIXED: these belong to system
        }
        if method == "system" or action in system_actions:
            return self.system_actions.execute

        # Default: UIActions (handles accessibility + cv + controlled mouse)
        return self.ui_actions.execute
