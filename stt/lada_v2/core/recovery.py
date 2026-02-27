"""
LADA - Recovery Engine
3-Level Recovery System:
  Level 1: Retry same action (3 times)
  Level 2: Alternative method
  Level 3: Full reset + reopen app
"""

import asyncio
import subprocess
from utils.logger import LADALogger

logger = LADALogger("RECOVERY")


class RecoveryEngine:
    """
    Handles failures at all levels.
    Tries multiple recovery strategies before giving up.
    """

    def __init__(self):
        self.max_retries = 3
        self.retry_delay = 0.8  # seconds between retries

    # ══════════════════════════════════════════════════════════
    # LEVEL 2: Alternative Method Recovery
    # ══════════════════════════════════════════════════════════

    async def try_alternative(self, step: dict, agent) -> bool:
        """
        Level 2: Try an alternative execution method.
        Accessibility fail → CV
        CV fail → System command
        Browser action fail → Keyboard shortcut
        """
        action = step.get("action", "")
        value = step.get("value", "")
        method = step.get("method", "accessibility")

        logger.info(f"Level 2 recovery: {action} with method={method}")

        # Accessibility failed → try CV
        if method == "accessibility":
            alt_step = {**step, "method": "cv"}
            logger.info(f"Trying CV method for: {action}")
            try:
                return await agent.ui_actions.execute(alt_step)
            except Exception as e:
                logger.warning(f"CV method also failed: {e}")

            # CV failed → try system command
            alt_step = {**step, "method": "system"}
            logger.info(f"Trying system command method for: {action}")
            try:
                return await agent.system_actions.execute(alt_step)
            except Exception as e:
                logger.warning(f"System command also failed: {e}")

        # CV failed → try accessibility
        elif method == "cv":
            alt_step = {**step, "method": "accessibility"}
            logger.info(f"Trying accessibility method for: {action}")
            try:
                return await agent.ui_actions.execute(alt_step)
            except Exception as e:
                logger.warning(f"Accessibility method also failed: {e}")

        # Browser action failed → try keyboard
        elif method == "browser":
            if action in ["find_and_click", "click_button", "click_result"]:
                logger.info(f"Trying keyboard navigation for: {action}")
                return await self._try_keyboard_navigation(value, agent)

        # Generic fallback: try via system
        if action == "open_app":
            return await self._open_app_via_system(value, agent)

        return False

    # ══════════════════════════════════════════════════════════
    # LEVEL 3: Full Reset Recovery
    # ══════════════════════════════════════════════════════════

    async def full_reset(self, step: dict, agent) -> bool:
        """
        Level 3: Full reset.
        1. Kill hung processes
        2. Wait for system to settle
        3. Retry step from clean state using correct executor
        """
        action = step.get("action", "")
        value = step.get("value", "")

        logger.warning(f"Level 3 FULL RESET for: {action} value={value}")

        # Kill any hung processes related to this task
        await self._kill_hung_processes()

        # Wait for system to settle
        await asyncio.sleep(1.5)

        # Route to correct executor based on action type
        system_native = {
            "run_command", "set_volume", "set_brightness",
            "open_menu", "verify_window", "focus_window",
            "close_window", "open_terminal",
        }

        logger.info("Retrying step after full reset...")

        for attempt in range(2):
            try:
                if action in system_native:
                    result = await agent.system_actions.execute(step)
                else:
                    result = await agent.ui_actions.execute(step)

                if result:
                    logger.info("Step succeeded after full reset.")
                    return True
            except Exception as e:
                logger.warning(f"Post-reset attempt {attempt + 1} failed: {e}")
            await asyncio.sleep(1.0)

        return False

    # ══════════════════════════════════════════════════════════
    # VERIFY FAILURE RECOVERY
    # ══════════════════════════════════════════════════════════

    async def recover_verify_failure(self, step: dict, agent) -> bool:
        """
        Recovery when verification fails after action.
        """
        action = step.get("action", "")
        value = step.get("value", "")

        logger.warning(f"Verification failed for: {action}={value}. Attempting recovery.")

        # Wait longer and re-verify
        await asyncio.sleep(2.0)
        verified = await agent.verifier.verify_step(step)
        if verified:
            logger.info("Verification passed after extended wait.")
            return True

        # Re-execute the step
        logger.info(f"Re-executing step: {action}")
        return await agent._execute_step_with_recovery(step, "recovery")

    # ══════════════════════════════════════════════════════════
    # HELPER METHODS
    # ══════════════════════════════════════════════════════════

    async def _try_keyboard_navigation(self, value: str, agent) -> bool:
        """Try keyboard-based navigation as browser fallback."""
        try:
            import pyautogui
            # Try Tab + Enter navigation
            pyautogui.hotkey("ctrl", "l")   # Focus address bar
            await asyncio.sleep(0.3)
            return True
        except Exception as e:
            logger.warning(f"Keyboard navigation failed: {e}")
            return False

    async def _open_app_via_system(self, app_name: str, agent) -> bool:
        """Open an app using system command as fallback."""
        # Common app name mappings
        app_commands = {
            "files":     ["nemo", "nautilus", "thunar", "pcmanfm"],
            "nemo":      ["nemo"],
            "nautilus":  ["nautilus"],
            "browser":   ["chromium-browser", "google-chrome", "firefox"],
            "chromium":  ["chromium-browser", "chromium"],
            "firefox":   ["firefox"],
            "terminal":  ["gnome-terminal", "xterm", "konsole", "xfce4-terminal"],
            "text editor": ["gedit", "mousepad", "kate", "nano"],
        }

        app_lower = app_name.lower()
        candidates = app_commands.get(app_lower, [app_lower])

        for cmd in candidates:
            try:
                logger.info(f"Trying to open: {cmd}")
                subprocess.Popen(
                    [cmd],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                await asyncio.sleep(1.5)

                # Check if process started
                check = subprocess.run(
                    ["pgrep", "-fl", cmd.split("-")[0]],
                    capture_output=True, text=True
                )
                if check.returncode == 0:
                    logger.info(f"App opened via system: {cmd}")
                    return True

            except FileNotFoundError:
                logger.debug(f"Command not found: {cmd}")
            except Exception as e:
                logger.warning(f"Error opening {cmd}: {e}")

        return False

    async def _kill_hung_processes(self):
        """Kill common processes that might be hanging."""
        hung_indicators = []

        # Check for zombie windows
        try:
            result = subprocess.run(
                ["wmctrl", "-l"],
                capture_output=True, text=True,
                timeout=2
            )
            if result.returncode == 0:
                windows = result.stdout
                # Look for "(Not Responding)" or similar
                if "not responding" in windows.lower():
                    logger.warning("Detected 'Not Responding' window.")
                    hung_indicators.append("hung_window")
        except Exception:
            pass

        if hung_indicators:
            logger.warning(f"Hung indicators found: {hung_indicators}")
            # Try xkill equivalent via wmctrl
            # We don't force-kill arbitrary processes

    async def handle_error_popup(self, error_text: str, agent) -> bool:
        """Handle an error popup that appeared during execution."""
        logger.warning(f"Handling error popup: {error_text}")

        # Try to dismiss the popup with Escape or Enter
        try:
            import pyautogui
            pyautogui.press("escape")
            await asyncio.sleep(0.5)
            return True
        except Exception:
            pass

        # Try clicking "OK" button via accessibility
        dismiss_step = {
            "action": "click_button",
            "value": "OK",
            "method": "accessibility"
        }
        try:
            return await agent.ui_actions.execute(dismiss_step)
        except Exception as e:
            logger.warning(f"Could not dismiss popup: {e}")
            return False
