"""
LADA - Timeout Manager
Wraps any coroutine / blocking call with strict timeouts.
If a step hangs → TimeoutError is raised, not infinite wait.
"""

import asyncio
import functools
import time
from typing import Optional, Any, Callable, TypeVar
from utils.logger import LADALogger

logger = LADALogger("TIMEOUT")

T = TypeVar("T")

# ── Default timeout budgets (seconds) ──────────────────────
TIMEOUTS = {
    "open_app":                  12.0,
    "open_terminal":             8.0,
    "navigate":                  30.0,
    "navigate_folder":           45.0,   # file manager open + AT-SPI poll
    "youtube_navigate_and_play": 60.0,   # page load + AT-SPI poll + click
    "find_and_click":            8.0,
    "click_button":              6.0,
    "click_result":              6.0,
    "type_text":                 5.0,
    "search":                    5.0,
    "verify_window":             15.0,
    "focus_window":              5.0,
    "close_window":              5.0,
    "wait_for_element":          20.0,
    "set_volume":                5.0,
    "set_brightness":            5.0,
    "run_command":               30.0,
    "scroll":                    3.0,
    "get_text":                  5.0,
    "open_menu":                 5.0,
    "default":                   15.0,   # fallback for unknown actions
}

# Hard cap — no single step can take longer than this
HARD_MAX_SECONDS = 60.0


class TimeoutManager:
    """
    Applies per-action timeouts to async coroutines.
    Usage:
        tm = TimeoutManager()
        result = await tm.run(coro, action="navigate")
    """

    def __init__(self, multiplier: float = 1.0):
        """
        multiplier: scale all timeouts (e.g. 2.0 for slower machines)
        """
        self.multiplier = multiplier

    def get_timeout(self, action: str) -> float:
        """Get timeout in seconds for a given action."""
        raw = TIMEOUTS.get(action, TIMEOUTS["default"])
        capped = min(raw * self.multiplier, HARD_MAX_SECONDS)
        return capped

    async def run(
        self,
        coro,
        action: str = "default",
        timeout_override: Optional[float] = None,
        label: str = "",
    ) -> Any:
        """
        Await a coroutine with timeout.

        Raises asyncio.TimeoutError if exceeded.
        """
        timeout_secs = timeout_override or self.get_timeout(action)
        display = label or action

        try:
            result = await asyncio.wait_for(coro, timeout=timeout_secs)
            return result
        except asyncio.TimeoutError:
            logger.error(
                f"TIMEOUT: '{display}' exceeded {timeout_secs:.1f}s"
            )
            raise

    async def run_safe(
        self,
        coro,
        action: str = "default",
        timeout_override: Optional[float] = None,
        fallback: Any = None,
        label: str = "",
    ) -> Any:
        """
        Same as run() but returns `fallback` instead of raising on timeout.
        """
        try:
            return await self.run(
                coro,
                action=action,
                timeout_override=timeout_override,
                label=label,
            )
        except asyncio.TimeoutError:
            return fallback

    async def wait_for_condition(
        self,
        condition_fn: Callable[[], bool],
        timeout_secs: float = 10.0,
        poll_interval: float = 0.3,
        label: str = "condition",
    ) -> bool:
        """
        Poll condition_fn() until True or timeout.
        Never use sleep() in a loop directly — use this.
        """
        deadline = asyncio.get_event_loop().time() + timeout_secs
        while asyncio.get_event_loop().time() < deadline:
            try:
                if condition_fn():
                    logger.debug(f"Condition met: {label}")
                    return True
            except Exception as e:
                logger.debug(f"Condition check error ({label}): {e}")
            await asyncio.sleep(poll_interval)

        logger.warning(f"Condition timeout after {timeout_secs}s: {label}")
        return False

    async def wait_for_async_condition(
        self,
        async_condition_fn: Callable[[], Any],
        timeout_secs: float = 10.0,
        poll_interval: float = 0.3,
        label: str = "async_condition",
    ) -> bool:
        """
        Poll an async condition function until it returns truthy or timeout.
        """
        deadline = asyncio.get_event_loop().time() + timeout_secs
        while asyncio.get_event_loop().time() < deadline:
            try:
                result = await async_condition_fn()
                if result:
                    logger.debug(f"Async condition met: {label}")
                    return True
            except Exception as e:
                logger.debug(f"Async condition error ({label}): {e}")
            await asyncio.sleep(poll_interval)

        logger.warning(f"Async condition timeout after {timeout_secs}s: {label}")
        return False


# ── Convenience decorator ──────────────────────────────────

def with_timeout(action: str = "default", multiplier: float = 1.0):
    """
    Decorator: wrap an async function with LADA timeout.

    @with_timeout("navigate")
    async def go_to_url(url): ...
    """
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            tm = TimeoutManager(multiplier=multiplier)
            return await tm.run(fn(*args, **kwargs), action=action)
        return wrapper
    return decorator


# ── Module-level singleton ─────────────────────────────────
_default_tm = TimeoutManager()


async def timed(coro, action: str = "default", fallback=None):
    """Quick access: await timed(some_coro(), action='navigate')"""
    return await _default_tm.run_safe(coro, action=action, fallback=fallback)
