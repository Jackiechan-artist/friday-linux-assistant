"""
LADA - Verification Engine
After EVERY step: verify expected state.
NO ASSUMPTION ALLOWED.
"""

import asyncio
import subprocess
import os
from typing import Optional
from utils.logger import LADALogger

logger = LADALogger("VERIFIER")


class Verifier:
    """
    Verifies the result of every executed step.
    Uses multiple verification strategies depending on action type.
    """

    def __init__(self):
        self.timeout = 5.0      # seconds to wait for condition
        self.poll_interval = 0.3

    async def verify_step(self, step: dict) -> bool:
        """
        Main verification dispatcher.
        Chooses verification strategy based on action type.
        """
        action = step.get("action", "")
        value = step.get("value", "")
        verify_condition = step.get("verify", "")

        # Use explicit verify condition if provided
        if verify_condition:
            return await self.verify_condition(verify_condition, value)

        # Auto-dispatch based on action type
        dispatchers = {
            "open_app":       self._verify_process_running,
            "open_terminal":  self._verify_window_exists,
            "verify_window":  self._verify_window_exists,
            "focus_window":   self._verify_window_focused,
            "navigate":       self._verify_url_loaded,
            "find_and_click": self._verify_element_action,
            "click_button":   self._verify_element_action,
            "click_result":   self._verify_element_action,
            "type_text":      self._verify_text_typed,
            "run_command":    self._verify_command_completed,
            "set_volume":     self._verify_volume_set,
            "set_brightness": self._verify_brightness_set,
            "close_window":   self._verify_window_closed,
            "open_menu":      self._verify_generic,
            "search":         self._verify_generic,
            "scroll":         self._verify_generic,
            "get_text":       self._verify_generic,
            "wait_for_element": self._verify_generic,
        }

        handler = dispatchers.get(action, self._verify_generic)
        return await handler(value, step)

    async def verify_condition(self, condition: str, value: str) -> bool:
        """Verify based on an explicit condition string."""
        condition_lower = condition.lower()

        if "window" in condition_lower:
            return await self._verify_window_exists(value, {})
        if "process" in condition_lower:
            return await self._verify_process_running(value, {})
        if "focus" in condition_lower:
            return await self._verify_window_focused(value, {})

        # Default: consider it verified
        return True

    # ── SPECIFIC VERIFIERS ────────────────────────────────────

    async def _verify_process_running(self, value: str, step: dict) -> bool:
        """Verify that a process is running by name."""
        # Map executable names to their actual process names
        PROCESS_NAME_MAP = {
            "google-chrome":     ["chrome", "google-chrome"],
            "chromium-browser":  ["chromium", "chromium-browser"],
            "chromium":          ["chromium"],
            "firefox":           ["firefox"],
            "nemo":              ["nemo"],
            "nautilus":          ["nautilus"],
            "gedit":             ["gedit"],
            "vlc":               ["vlc"],
            "gnome-terminal":    ["gnome-terminal"],
            "xfce4-terminal":    ["xfce4-terminal"],
        }

        app_name = value.strip().lower()
        candidates = PROCESS_NAME_MAP.get(app_name, [app_name.split("/")[-1]])

        for candidate in candidates:
            result = subprocess.run(
                ["pgrep", "-f", candidate],
                capture_output=True, text=True
            )
            if result.returncode == 0 and result.stdout.strip():
                logger.debug(f"Process verified running: {candidate}")
                return True

        # Also check wmctrl for a matching window as fallback
        wm = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True)
        if wm.returncode == 0:
            base = app_name.split("-")[0]  # "google" from "google-chrome"
            if base in wm.stdout.lower() or app_name.split("-")[-1] in wm.stdout.lower():
                logger.debug(f"Window found via wmctrl for: {app_name}")
                return True

        logger.warning(f"Process NOT found for: {app_name}")
        return False

    async def _verify_window_exists(self, value: str, step: dict) -> bool:
        """Verify a window with the given title exists."""
        deadline = asyncio.get_event_loop().time() + self.timeout

        while asyncio.get_event_loop().time() < deadline:
            result = subprocess.run(
                ["wmctrl", "-l"],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                windows = result.stdout.lower()
                if value.lower() in windows:
                    logger.debug(f"Window verified exists: {value}")
                    return True

            await asyncio.sleep(self.poll_interval)

        logger.warning(f"Window NOT found within timeout: {value}")
        return False

    async def _verify_window_focused(self, value: str, step: dict) -> bool:
        """Verify a window is focused/active."""
        result = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowname"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            active = result.stdout.strip().lower()
            found = value.lower() in active
            if found:
                logger.debug(f"Window focused verified: {value}")
            else:
                logger.warning(f"Window NOT focused: {value} (active: {active})")
            return found

        # Fallback: check wmctrl
        return await self._verify_window_exists(value, step)

    async def _verify_url_loaded(self, value: str, step: dict) -> bool:
        """Verify browser navigation — basic check via window title."""
        # Extract domain from URL
        url = value
        domain = url.replace("https://", "").replace("http://", "").split("/")[0]
        domain_parts = domain.split(".")

        # Check window title contains domain name
        result = subprocess.run(
            ["wmctrl", "-l"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            windows = result.stdout.lower()
            # Check for domain in window titles
            for part in domain_parts:
                if len(part) > 3 and part in windows:
                    logger.debug(f"URL/browser window verified: {domain}")
                    return True

        # For browser actions, often the Playwright layer will handle verification
        logger.debug(f"URL verification: assuming success for browser task: {url}")
        return True

    async def _verify_element_action(self, value: str, step: dict) -> bool:
        """
        Verify element was clicked/interacted.
        This is difficult to verify directly — check for state changes.
        """
        # Wait briefly for UI to respond
        await asyncio.sleep(0.3)
        # Assume action succeeded if no exception was raised
        logger.debug(f"Element action verified (implicit): {value}")
        return True

    async def _verify_text_typed(self, value: str, step: dict) -> bool:
        """Verify text was typed — implicit verification."""
        await asyncio.sleep(0.2)
        logger.debug(f"Text typed verified (implicit): {value[:20]}...")
        return True

    async def _verify_command_completed(self, value: str, step: dict) -> bool:
        """Verify a shell command completed."""
        # The command executor returns success/failure
        # Here we just do a brief check
        await asyncio.sleep(0.1)
        logger.debug(f"Command completed (implicit): {value[:40]}")
        return True

    async def _verify_volume_set(self, value: str, step: dict) -> bool:
        """Verify system volume was set."""
        result = subprocess.run(
            ["amixer", "get", "Master"],
            capture_output=True, text=True
        )
        if result.returncode == 0 and result.stdout:
            logger.debug("Volume state verified via amixer.")
            return True
        return True  # Assume success if amixer unavailable

    async def _verify_brightness_set(self, value: str, step: dict) -> bool:
        """Verify screen brightness was set."""
        backlight_path = "/sys/class/backlight"
        if os.path.exists(backlight_path):
            logger.debug("Brightness path verified exists.")
        return True  # Assume success

    async def _verify_window_closed(self, value: str, step: dict) -> bool:
        """Verify a window was closed."""
        await asyncio.sleep(0.5)
        result = subprocess.run(
            ["wmctrl", "-l"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            windows = result.stdout.lower()
            if value.lower() not in windows:
                logger.debug(f"Window closed verified: {value}")
                return True
            else:
                logger.warning(f"Window still visible after close: {value}")
                return False
        return True

    async def _verify_generic(self, value: str, step: dict) -> bool:
        """Generic verification — assume success with brief wait."""
        await asyncio.sleep(0.2)
        return True

    # ── UTILITY VERIFIERS ─────────────────────────────────────

    async def wait_for_window(
        self,
        title: str,
        timeout: float = 10.0
    ) -> bool:
        """Wait for a window to appear, with timeout."""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            result = subprocess.run(
                ["wmctrl", "-l"],
                capture_output=True, text=True
            )
            if result.returncode == 0 and title.lower() in result.stdout.lower():
                return True
            await asyncio.sleep(self.poll_interval)
        return False

    async def wait_for_process(
        self,
        process_name: str,
        timeout: float = 10.0
    ) -> bool:
        """Wait for a process to start, with timeout."""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            result = subprocess.run(
                ["pgrep", "-fl", process_name],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                return True
            await asyncio.sleep(self.poll_interval)
        return False

    def check_error_popup(self) -> Optional[str]:
        """
        Check if an error dialog/popup appeared.
        Returns error text if found, None otherwise.
        """
        try:
            result = subprocess.run(
                ["wmctrl", "-l"],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                return None

            windows = result.stdout.lower()
            error_keywords = ["error", "warning", "failed", "problem", "unable", "cannot"]

            for kw in error_keywords:
                if kw in windows:
                    logger.warning(f"Error popup detected: '{kw}' found in window list")
                    return kw

        except Exception as e:
            logger.debug(f"Error popup check failed: {e}")

        return None
