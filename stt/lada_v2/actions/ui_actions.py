"""
LADA - UI Actions
Native UI interactions via accessibility tree, with CV fallback.
Handles: app launching, menu opening, clicking, typing, window management.
"""

import asyncio
import subprocess
import time
from typing import Optional
from utils.logger import LADALogger

logger = LADALogger("UI_ACTIONS")


class UIActions:
    """
    UI executor with multi-mode fallback:
    Mode 1: AT-SPI accessibility tree
    Mode 2: xdotool/wmctrl
    Mode 3: PyAutoGUI (controlled, with easing)
    """

    def __init__(self, accessibility, cv_detector):
        self.accessibility = accessibility
        self.cv_detector   = cv_detector

    async def execute(self, step: dict) -> bool:
        """
        Execute a UI action.
        Returns True on success, False on failure.
        """
        action = step.get("action", "")
        value  = step.get("value", "")
        method = step.get("method", "accessibility")

        logger.info(f"UI execute: action={action} value={value} method={method}")

        # Route to handlers
        if action == "open_app":
            return await self._open_app(value, method)
        elif action == "open_menu":
            return await self._open_menu(value, method)
        elif action == "search":
            return await self._search(value, method)
        elif action == "click_result":
            return await self._click_result(value, method)
        elif action == "click_button":
            return await self._click_button(value, method)
        elif action == "type_text":
            return await self._type_text(value, method)
        elif action == "find_and_click":
            return await self._find_and_click(value, method)
        elif action == "verify_window":
            return await self._verify_window(value, method)
        elif action == "focus_window":
            return await self._focus_window(value, method)
        elif action == "close_window":
            return await self._close_window(value, method)
        elif action == "scroll":
            return await self._scroll(value, method)
        else:
            logger.warning(f"No UI handler for action: {action}")
            return False

    # ── Handlers ───────────────────────────────────────────

    async def _open_app(self, app_name: str, method: str) -> bool:
        """Launch application."""
        candidates = [
            app_name,
            f"{app_name}-browser",
            app_name.replace(" ", "-"),
        ]

        for candidate in candidates:
            try:
                subprocess.Popen(
                    [candidate],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                await asyncio.sleep(2.0)

                # Verify launch
                result = subprocess.run(
                    ["pgrep", "-f", candidate],
                    capture_output=True,
                    timeout=2,
                )
                if result.returncode == 0:
                    logger.info(f"App launched: {candidate}")
                    return True

            except FileNotFoundError:
                continue
            except Exception as e:
                logger.debug(f"Launch error ({candidate}): {e}")
                continue

        logger.error(f"All launch candidates failed for: {app_name}")
        return False

    async def _open_menu(self, menu_type: str, method: str) -> bool:
        """Open application menu via Super key."""
        try:
            subprocess.run(
                ["xdotool", "key", "Super_L"],
                capture_output=True,
                timeout=2,
            )
            await asyncio.sleep(0.8)
            logger.info("Menu opened")
            return True
        except Exception as e:
            logger.debug(f"open_menu error: {e}")
            return False

    async def _search(self, search_term: str, method: str) -> bool:
        """
        Type search term in application menu search box.
        Menu should already be open. Just type — Cinnamon menu auto-focuses search.
        """
        try:
            # Give menu more time to fully render and focus search box
            await asyncio.sleep(0.8)

            subprocess.run(
                ["xdotool", "type", "--clearmodifiers", "--delay", "50", search_term],
                capture_output=True,
                timeout=5,
            )
            await asyncio.sleep(0.7)  # wait for search results to appear
            logger.info(f"Typed in menu search: {search_term}")
            return True

        except Exception as e:
            logger.debug(f"search error: {e}")
            return False

    async def _click_result(self, result_name: str, method: str) -> bool:
        """
        Click on search result in menu.
        Cinnamon menu: first result is auto-selected — just press Enter.
        """
        try:
            await asyncio.sleep(0.3)  # brief pause before pressing Enter
            subprocess.run(
                ["xdotool", "key", "Return"],
                capture_output=True,
                timeout=2,
            )
            await asyncio.sleep(2.5)  # wait for app to start launching
            logger.info(f"Launched via menu Enter: {result_name}")
            return True

        except Exception as e:
            logger.debug(f"click_result error: {e}")
            return False

    async def _click_button(self, button_label: str, method: str) -> bool:
        """Click button by label."""
        # FIX v7.1: Pehle code click_element(name=...) call karta tha
        # lekin click_element() sirf element object leta hai, name nahi.
        # Ab pehle find_element_by_name() se element dhundho, phir click karo.
        if method == "accessibility" and self.accessibility.is_available():
            element = self.accessibility.find_element_by_name(
                name=button_label, role="push button"
            )
            if element is None:
                # Role ke bina bhi try karo
                element = self.accessibility.find_element_by_name(name=button_label)
            if element:
                clicked = self.accessibility.click_element(element)
                if clicked:
                    await asyncio.sleep(0.3)
                    return True

        # Fallback: CV
        if self.cv_detector:
            try:
                found = await self.cv_detector.find_and_click(button_label)
                if found:
                    await asyncio.sleep(0.5)
                    return True
            except Exception:
                pass

        # Fallback: xdotool
        try:
            result = subprocess.run(
                ["xdotool", "search", "--name", button_label, "mouseclick", "1"],
                capture_output=True, timeout=3
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass

        return False

    async def _type_text(self, text: str, method: str) -> bool:
        """Type text."""
        try:
            if method == "accessibility":
                # AT-SPI: type into focused element
                typed = await self.accessibility.type_text(text)
                if typed:
                    await asyncio.sleep(0.2)
                    return True

            # Fallback: xdotool
            subprocess.run(
                ["xdotool", "type", "--clearmodifiers", text],
                capture_output=True,
                timeout=5,
            )
            await asyncio.sleep(0.2)
            return True

        except Exception as e:
            logger.debug(f"type_text error: {e}")
            return False

    async def _find_and_click(self, element_name: str, method: str) -> bool:
        """Find and click an element."""
        # FIX v7.1: Same bug as _click_button — find first, then click
        if method == "accessibility" and self.accessibility.is_available():
            element = self.accessibility.find_element_by_name(name=element_name)
            if element:
                clicked = self.accessibility.click_element(element)
                if clicked:
                    await asyncio.sleep(0.5)
                    return True

        if self.cv_detector:
            try:
                found = await self.cv_detector.find_and_click(element_name)
                if found:
                    await asyncio.sleep(0.5)
                    return True
            except Exception:
                pass

        logger.warning(f"find_and_click: could not find '{element_name}' with method={method}")
        return False

    async def _verify_window(self, window_title: str, method: str) -> bool:
        """Verify window exists."""
        try:
            result = subprocess.run(
                ["wmctrl", "-l"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if result.returncode == 0:
                title_lower = window_title.lower()
                for line in result.stdout.split("\n"):
                    if title_lower in line.lower():
                        return True
            return False
        except Exception:
            return False

    async def _focus_window(self, window_title: str, method: str) -> bool:
        """Focus window."""
        try:
            result = subprocess.run(
                ["wmctrl", "-a", window_title],
                capture_output=True,
                timeout=3,
            )
            return result.returncode == 0
        except Exception:
            return False

    async def _close_window(self, window_title: str, method: str) -> bool:
        """Close window."""
        try:
            result = subprocess.run(
                ["wmctrl", "-c", window_title],
                capture_output=True,
                timeout=3,
            )
            return result.returncode == 0
        except Exception:
            return False

    async def _scroll(self, direction: str, method: str) -> bool:
        """Scroll in direction."""
        try:
            if direction.lower() == "up":
                subprocess.run(["xdotool", "click", "4"], timeout=1)
            elif direction.lower() == "down":
                subprocess.run(["xdotool", "click", "5"], timeout=1)
            await asyncio.sleep(0.2)
            return True
        except Exception:
            return False
