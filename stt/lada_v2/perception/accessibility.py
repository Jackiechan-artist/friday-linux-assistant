"""
LADA - Accessibility Layer (Primary Vision)
Uses AT-SPI (pyatspi) to read UI element tree.
No OCR needed for native apps.
"""

import asyncio
import subprocess
from typing import Optional, List, Dict, Any
from utils.logger import LADALogger

logger = LADALogger("ACCESSIBILITY")


class AccessibilityLayer:
    """
    Reads and interacts with the Linux accessibility tree via AT-SPI.
    Primary vision system — no pixel-based detection needed.
    """

    def __init__(self):
        self.registry = None
        self.desktop = None
        self._available = False

    async def initialize(self) -> bool:
        """Initialize AT-SPI connection."""
        try:
            import pyatspi
            self.pyatspi = pyatspi
            self.registry = pyatspi.Registry
            self.desktop = pyatspi.Registry.getDesktop(0)
            self._available = True
            logger.info("AT-SPI accessibility layer initialized.")
            return True
        except ImportError:
            logger.warning("pyatspi not available. Accessibility layer disabled.")
            self._available = False
            return False
        except Exception as e:
            logger.warning(f"AT-SPI init failed: {e}")
            self._available = False
            return False

    def is_available(self) -> bool:
        return self._available

    # ── ELEMENT FINDING ───────────────────────────────────────

    def find_element_by_name(
        self,
        name: str,
        role: Optional[str] = None,
        window_title: Optional[str] = None
    ) -> Optional[Any]:
        """
        Find an element by name and optionally role.
        Searches the entire accessibility tree.
        """
        if not self._available:
            return None

        try:
            return self._search_desktop(
                name=name,
                role=role,
                window_title=window_title
            )
        except Exception as e:
            logger.warning(f"find_element_by_name error: {e}")
            return None

    def _search_desktop(
        self,
        name: str,
        role: Optional[str] = None,
        window_title: Optional[str] = None
    ) -> Optional[Any]:
        """Recursively search the AT-SPI desktop tree."""
        if not self.desktop:
            return None

        name_lower = name.lower()

        for app in self.desktop:
            if app is None:
                continue

            # Filter by window title if specified
            if window_title:
                app_name = (app.name or "").lower()
                if window_title.lower() not in app_name:
                    # Check windows
                    found_window = False
                    try:
                        for window in app:
                            if window and window_title.lower() in (window.name or "").lower():
                                found_window = True
                                break
                    except Exception:
                        pass
                    if not found_window:
                        continue

            result = self._search_node(app, name_lower, role)
            if result:
                return result

        return None

    def _search_node(self, node, name: str, role: Optional[str] = None) -> Optional[Any]:
        """Recursively search a node in the AT-SPI tree."""
        try:
            node_name = (node.name or "").lower()
            node_role = node.getRoleName() if hasattr(node, "getRoleName") else ""

            # Match name
            if name in node_name:
                # Match role if specified
                if role is None or role.lower() in node_role.lower():
                    if self._is_visible(node):
                        return node

            # Search children
            for child in node:
                result = self._search_node(child, name, role)
                if result:
                    return result

        except Exception:
            pass

        return None

    def _is_visible(self, element) -> bool:
        """Check if an element is visible and enabled."""
        try:
            state_set = element.getState()
            pyatspi = self.pyatspi
            return (
                state_set.contains(pyatspi.STATE_VISIBLE)
                and state_set.contains(pyatspi.STATE_SENSITIVE)
            )
        except Exception:
            return True  # Assume visible if check fails

    # ── ELEMENT INTERACTIONS ──────────────────────────────────

    def click_element(self, element) -> bool:
        """Click an element via AT-SPI doAction.
        
        FIX v7.1: Pehle ui_actions.py se click_element(name=...) call hoti thi
        jo wrong tha. Ye function sirf element object leta hai.
        ui_actions.py mein fix kar diya — ab ye sahi tarah call hota hai.
        """
        if element is None:
            return False
        try:
            n_actions = element.queryAction().nActions
            action_names = [
                element.queryAction().getName(i)
                for i in range(n_actions)
            ]

            if "click" in [a.lower() for a in action_names]:
                idx = [a.lower() for a in action_names].index("click")
                element.queryAction().doAction(idx)
                return True

            if "press" in [a.lower() for a in action_names]:
                idx = [a.lower() for a in action_names].index("press")
                element.queryAction().doAction(idx)
                return True

            if n_actions > 0:
                element.queryAction().doAction(0)
                return True

        except Exception as e:
            logger.warning(f"AT-SPI click failed: {e}")

        return False

    async def click_element_async(self, element) -> bool:
        """Async wrapper for click_element — ui_actions ke liye."""
        return self.click_element(element)

    def type_into_element(self, element, text: str) -> bool:
        """Type text into a focused element."""
        if element is None:
            return False
        try:
            # Focus the element first
            element.queryComponent().grabFocus()
            import time
            time.sleep(0.2)

            # Try AT-SPI text editing
            try:
                text_iface = element.queryEditableText()
                text_iface.insertText(0, text, len(text))
                return True
            except Exception:
                pass

            # Fallback: use xdotool type
            result = subprocess.run(
                ["xdotool", "type", "--clearmodifiers", text],
                capture_output=True, text=True
            )
            return result.returncode == 0

        except Exception as e:
            logger.warning(f"Type into element failed: {e}")
            return False

    def get_element_text(self, element) -> str:
        """Get text content of an element."""
        if element is None:
            return ""
        try:
            try:
                text_iface = element.queryText()
                return text_iface.getText(0, -1)
            except Exception:
                return element.name or ""
        except Exception:
            return ""

    def get_element_value(self, element) -> Optional[float]:
        """Get numeric value of an element (e.g., slider)."""
        if element is None:
            return None
        try:
            val_iface = element.queryValue()
            return val_iface.currentValue
        except Exception:
            return None

    # ── WINDOW OPERATIONS ─────────────────────────────────────

    def get_all_windows(self) -> List[Dict]:
        """Get all top-level windows."""
        windows = []
        if not self._available or not self.desktop:
            return windows

        try:
            for app in self.desktop:
                if app is None:
                    continue
                try:
                    for window in app:
                        if window and window.getRoleName() == "frame":
                            windows.append({
                                "title": window.name or "",
                                "app": app.name or "",
                                "accessible": window
                            })
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"get_all_windows error: {e}")

        return windows

    def find_window_by_title(self, title: str) -> Optional[Any]:
        """Find a window by partial title match."""
        if not self._available:
            return None

        title_lower = title.lower()
        try:
            for app in self.desktop:
                if app is None:
                    continue
                try:
                    for window in app:
                        if window and title_lower in (window.name or "").lower():
                            return window
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"find_window_by_title error: {e}")

        return None

    def focus_window(self, title: str) -> bool:
        """Focus a window by title using wmctrl."""
        try:
            result = subprocess.run(
                ["wmctrl", "-a", title],
                capture_output=True, text=True
            )
            return result.returncode == 0
        except Exception as e:
            logger.warning(f"focus_window error: {e}")
            return False

    # ── UI TREE DUMP ──────────────────────────────────────────

    def dump_ui_tree(
        self,
        window_title: Optional[str] = None,
        max_depth: int = 5
    ) -> List[Dict]:
        """
        Dump the UI tree for debugging.
        Returns list of element dicts.
        """
        elements = []
        if not self._available:
            return elements

        try:
            for app in self.desktop:
                if app is None:
                    continue
                if window_title and window_title.lower() not in (app.name or "").lower():
                    skip = True
                    try:
                        for win in app:
                            if win and window_title.lower() in (win.name or "").lower():
                                skip = False
                                break
                    except Exception:
                        pass
                    if skip:
                        continue

                self._dump_node(app, elements, depth=0, max_depth=max_depth)
        except Exception as e:
            logger.warning(f"dump_ui_tree error: {e}")

        return elements

    def _dump_node(self, node, result: list, depth: int, max_depth: int):
        """Recursively dump node info."""
        if depth > max_depth:
            return
        try:
            result.append({
                "depth": depth,
                "name": node.name or "",
                "role": node.getRoleName() if hasattr(node, "getRoleName") else "",
                "visible": self._is_visible(node)
            })
            for child in node:
                self._dump_node(child, result, depth + 1, max_depth)
        except Exception:
            pass

    async def cleanup(self):
        """Cleanup AT-SPI resources."""
        self._available = False
        logger.info("Accessibility layer cleaned up.")
