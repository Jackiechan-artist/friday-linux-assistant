"""
LASA - ScreenReader
AT-SPI se poori screen padhta hai.

Kya karta hai:
  - Har open window ko detect karta hai
  - Har window ke andar ke elements (buttons, text fields, menus, icons) dhundta hai
  - Har element ka: naam, role, coordinates, state bataata hai
  - Screenshot leke usse combine karta hai

Use karo:
  sr = ScreenReader()
  await sr.initialize()
  
  state = await sr.get_screen_state()
  # state.windows  → list of open windows
  # state.elements → flat list of all clickable elements with coordinates
  # state.focused  → which window/element is focused right now
  
  el = sr.find("Save button")     → element by name
  el = sr.find_by_role("button")  → all buttons
"""

import asyncio
import subprocess
import json
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from utils.logger import get_logger

log = get_logger("SCREEN")


# ──────────────────────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────────────────────

@dataclass
class UIElement:
    """One element on screen — button, text field, menu item, icon, etc."""
    name:     str
    role:     str          # "button", "text", "menu item", "check box", etc.
    x:        int          # top-left x (desktop coordinates)
    y:        int          # top-left y
    w:        int          # width
    h:        int          # height
    cx:       int          # center x  ← use this to click
    cy:       int          # center y  ← use this to click
    window:   str = ""     # parent window title
    app:      str = ""     # parent app name
    enabled:  bool = True
    focused:  bool = False
    checked:  Optional[bool] = None   # for checkboxes/radio
    value:    str = ""     # current text value (for text fields)
    children: int = 0      # number of child elements
    _accessible: Any = field(default=None, repr=False)  # raw pyatspi object

    def is_clickable(self) -> bool:
        return self.role in {
            "button", "push button", "menu item", "check box", "radio button",
            "menu", "link", "tab", "icon", "toggle button", "combo box",
            "list item", "tree item", "cell"
        } and self.enabled and self.w > 0 and self.h > 0

    def to_dict(self) -> dict:
        return {
            "name": self.name, "role": self.role,
            "x": self.x, "y": self.y, "w": self.w, "h": self.h,
            "cx": self.cx, "cy": self.cy,
            "window": self.window, "app": self.app,
            "enabled": self.enabled, "focused": self.focused,
            "value": self.value,
        }

    def __str__(self) -> str:
        state = ""
        if not self.enabled: state += " [disabled]"
        if self.focused:     state += " [FOCUSED]"
        if self.checked is True:  state += " [✓]"
        if self.checked is False: state += " [ ]"
        val = f" = '{self.value}'" if self.value else ""
        return f"[{self.role}] '{self.name}'{val} @ ({self.cx},{self.cy}){state}"


@dataclass
class WindowInfo:
    title:    str
    app:      str
    x:        int
    y:        int
    w:        int
    h:        int
    focused:  bool = False
    elements: List[UIElement] = field(default_factory=list)

    def __str__(self) -> str:
        focused_str = " ◄ FOCUSED" if self.focused else ""
        return f"Window: '{self.title}' ({self.app}) [{self.w}x{self.h} @ {self.x},{self.y}]{focused_str} — {len(self.elements)} elements"


@dataclass
class ScreenState:
    windows:  List[WindowInfo]
    elements: List[UIElement]   # all elements across all windows, flat
    focused_window: Optional[str]
    focused_element: Optional[UIElement]
    screenshot_path: Optional[str] = None

    def summary(self) -> str:
        lines = [f"=== Screen State: {len(self.windows)} windows, {len(self.elements)} elements ==="]
        for w in self.windows:
            lines.append(f"  {w}")
        if self.focused_element:
            lines.append(f"  Focused element: {self.focused_element}")
        return "\n".join(lines)

    def find(self, name: str, role: str = "") -> Optional[UIElement]:
        """Find element by partial name match (case-insensitive)."""
        name_lower = name.lower()
        role_lower = role.lower()
        for el in self.elements:
            name_match = name_lower in el.name.lower()
            role_match = (not role_lower) or (role_lower in el.role.lower())
            if name_match and role_match:
                return el
        return None

    def find_all(self, name: str = "", role: str = "") -> List[UIElement]:
        """Find all elements matching name and/or role."""
        name_lower = name.lower()
        role_lower = role.lower()
        results = []
        for el in self.elements:
            name_match = (not name_lower) or (name_lower in el.name.lower())
            role_match = (not role_lower) or (role_lower in el.role.lower())
            if name_match and role_match:
                results.append(el)
        return results

    def clickable(self) -> List[UIElement]:
        """All elements that can be clicked."""
        return [el for el in self.elements if el.is_clickable()]

    def to_text(self, max_elements: int = 50) -> str:
        """Human-readable description of screen for AI."""
        lines = []
        if self.focused_window:
            lines.append(f"Active window: {self.focused_window}")
        lines.append(f"Open windows: {[w.title for w in self.windows]}")
        lines.append("")
        lines.append(f"Clickable elements ({len(self.clickable())} total):")
        shown = 0
        for el in self.elements:
            if el.is_clickable() and shown < max_elements:
                lines.append(f"  {el}")
                shown += 1
        if len(self.elements) > max_elements:
            lines.append(f"  ... and {len(self.elements)-max_elements} more")
        return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# Main ScreenReader class
# ──────────────────────────────────────────────────────────────

class ScreenReader:
    """
    Reads the full screen state using AT-SPI + wmctrl fallback.
    """

    def __init__(self):
        self._pyatspi   = None
        self._desktop   = None
        self._available = False
        self._max_depth = 8       # how deep to traverse UI tree
        self._max_els   = 500     # max elements per window

    async def initialize(self) -> bool:
        """Initialize AT-SPI connection."""
        try:
            import pyatspi
            self._pyatspi = pyatspi
            self._desktop = pyatspi.Registry.getDesktop(0)
            self._available = True
            log.info("AT-SPI connected — screen reading ready")
            return True
        except ImportError:
            log.warning("pyatspi not installed. Install: pip install pyatspi")
            return False
        except Exception as e:
            log.warning(f"AT-SPI init failed: {e}")
            return False

    def is_available(self) -> bool:
        return self._available

    # ──────────────────────────────────────────────
    # MAIN PUBLIC METHOD
    # ──────────────────────────────────────────────

    async def get_screen_state(self, take_screenshot: bool = False) -> ScreenState:
        """
        Read complete screen state.
        Returns ScreenState with all windows and elements.
        """
        windows  = []
        all_els  = []
        focused_window  = None
        focused_element = None

        if self._available:
            try:
                focused_window, focused_element, windows, all_els = await self._read_atspi()
            except Exception as e:
                log.warning(f"AT-SPI read error: {e}")
                windows, all_els = self._read_wmctrl_fallback()
        else:
            windows, all_els = self._read_wmctrl_fallback()

        screenshot = None
        if take_screenshot:
            screenshot = self._take_screenshot()

        return ScreenState(
            windows=windows,
            elements=all_els,
            focused_window=focused_window,
            focused_element=focused_element,
            screenshot_path=screenshot,
        )

    async def find_element(self, name: str, role: str = "") -> Optional[UIElement]:
        """Quick find — reads screen and searches for element."""
        state = await self.get_screen_state()
        return state.find(name, role)

    async def get_focused_window(self) -> Optional[WindowInfo]:
        """Return the currently focused window."""
        state = await self.get_screen_state()
        for w in state.windows:
            if w.focused:
                return w
        return None

    async def get_windows(self) -> List[WindowInfo]:
        """Return list of all open windows."""
        state = await self.get_screen_state()
        return state.windows

    # ──────────────────────────────────────────────
    # AT-SPI IMPLEMENTATION
    # ──────────────────────────────────────────────

    async def _read_atspi(self):
        """Read all windows and elements via AT-SPI."""
        pa = self._pyatspi
        desktop = self._desktop

        windows      = []
        all_elements = []
        focused_window  = None
        focused_element = None

        for app in desktop:
            if app is None:
                continue
            app_name = app.name or "unknown"

            for win in app:
                if win is None:
                    continue
                if win.getRoleName() not in ("frame", "dialog", "window", "alert", "file chooser"):
                    continue

                win_title = win.name or app_name

                # Get window geometry
                try:
                    comp = win.queryComponent()
                    ext  = comp.getExtents(pa.DESKTOP_COORDS)
                    wx, wy, ww, wh = ext.x, ext.y, ext.width, ext.height
                except Exception:
                    wx, wy, ww, wh = 0, 0, 0, 0

                # Check if focused
                try:
                    state_set = win.getState()
                    win_focused = state_set.contains(pa.STATE_ACTIVE)
                    if win_focused:
                        focused_window = win_title
                except Exception:
                    win_focused = False

                # Collect elements
                elements = []
                self._collect_elements(
                    node=win,
                    elements=elements,
                    window=win_title,
                    app=app_name,
                    depth=0,
                )

                # Find focused element
                for el in elements:
                    if el.focused:
                        focused_element = el
                        break

                win_info = WindowInfo(
                    title=win_title, app=app_name,
                    x=wx, y=wy, w=ww, h=wh,
                    focused=win_focused,
                    elements=elements,
                )
                windows.append(win_info)
                all_elements.extend(elements)

                await asyncio.sleep(0)  # yield to event loop

        return focused_window, focused_element, windows, all_elements

    def _collect_elements(
        self,
        node,
        elements: list,
        window: str,
        app: str,
        depth: int,
    ):
        """
        Recursively collect UI elements from AT-SPI tree.

        Race condition guards:
          1. Stale element reference → wrapped in try/except per property
          2. Element disappears mid-traversal → skip silently
          3. Focus shift during read → capture at this moment, mark as snapshot
          4. Dynamic window title change → use app name as fallback
          5. Index shift (childCount vs actual children) → iterate defensively
        """
        if depth > self._max_depth:
            return
        if len(elements) >= self._max_els:
            return
        if node is None:
            return

        pa = self._pyatspi

        # ── Safe property reads (each isolated — one failure ≠ skip whole node) ──
        try:
            role = node.getRoleName() or "unknown"
        except Exception:
            return  # node became invalid (race condition #2)

        try:
            name = node.name or ""
        except Exception:
            name = ""

        skip_roles = {"filler", "separator", "unknown"}
        if role in skip_roles and not name:
            pass  # traverse children but don't add this node
        else:
            # ── Coordinates (race: element may move between reads) ──
            x = y = w = h = 0
            try:
                comp = node.queryComponent()
                ext  = comp.getExtents(pa.DESKTOP_COORDS)
                x, y, w, h = ext.x, ext.y, ext.width, ext.height
                # Guard: unreasonable coords = stale reference or offscreen
                if x < -500 or y < -500 or x > 9999 or y > 9999:
                    x = y = w = h = 0
                if w < 0 or h < 0:
                    w = h = 0
            except Exception:
                pass  # race condition #1: element went stale

            cx = x + w // 2
            cy = y + h // 2

            # ── State (race: state can flip during read) ──
            enabled = True
            focused = False
            checked = None
            try:
                states  = node.getState()
                enabled = states.contains(pa.STATE_ENABLED)
                focused = states.contains(pa.STATE_FOCUSED)
                if states.contains(pa.STATE_CHECKED):
                    checked = True
                elif role in ("check box", "radio button"):
                    checked = False
            except Exception:
                pass  # race condition #3: focus shifted

            # ── Text value (optional, never fatal) ──
            value = ""
            try:
                txt   = node.queryText()
                value = txt.getText(0, -1)[:100]
            except Exception:
                pass

            # ── Child count (race: children can appear/disappear) ──
            nchildren = 0
            try:
                nchildren = node.childCount
            except Exception:
                pass

            el = UIElement(
                name=name, role=role,
                x=x, y=y, w=w, h=h,
                cx=cx, cy=cy,
                window=window, app=app,
                enabled=enabled, focused=focused,
                checked=checked, value=value,
                children=nchildren,
                _accessible=node,
            )
            if name or el.is_clickable():
                elements.append(el)

        # ── Children traversal (race: childCount may not match actual children) ──
        # Use defensive iteration instead of range(childCount)
        try:
            child_index = 0
            while child_index < 200:  # hard cap prevents infinite loop
                try:
                    child = node.getChildAtIndex(child_index)
                    if child is None:
                        break
                    self._collect_elements(child, elements, window, app, depth + 1)
                    child_index += 1
                except IndexError:
                    break  # race condition #5: index shifted
                except Exception:
                    child_index += 1  # skip bad child, continue
                    if child_index > 500:
                        break
        except Exception:
            pass

    # ──────────────────────────────────────────────
    # FALLBACK: wmctrl (no AT-SPI)
    # ──────────────────────────────────────────────

    def _read_wmctrl_fallback(self):
        """Read window list via wmctrl when AT-SPI unavailable."""
        windows  = []
        elements = []
        try:
            result = subprocess.run(
                ["wmctrl", "-lG"], capture_output=True, text=True, timeout=3
            )
            for line in result.stdout.strip().split("\n"):
                parts = line.split(None, 7)
                if len(parts) >= 8:
                    try:
                        x, y, w, h = int(parts[2]), int(parts[3]), int(parts[4]), int(parts[5])
                        title = parts[7]
                        windows.append(WindowInfo(
                            title=title, app="",
                            x=x, y=y, w=w, h=h,
                        ))
                    except Exception:
                        pass
        except Exception:
            pass
        return windows, elements

    # ──────────────────────────────────────────────
    # SCREENSHOT
    # ──────────────────────────────────────────────

    def _take_screenshot(self) -> Optional[str]:
        """Take screenshot, save to /tmp, return path."""
        path = "/tmp/lasa_screen.png"
        try:
            for cmd in [["scrot", "-z", path], ["import", "-window", "root", path]]:
                r = subprocess.run(cmd, capture_output=True, timeout=5)
                if r.returncode == 0:
                    return path
        except Exception:
            pass
        return None

    # ──────────────────────────────────────────────
    # UTILITIES
    # ──────────────────────────────────────────────

    async def element_at(self, x: int, y: int) -> Optional[UIElement]:
        """Find which element is at screen coordinates (x, y)."""
        state = await self.get_screen_state()
        # Find smallest element containing (x,y)
        best = None
        best_area = float("inf")
        for el in state.elements:
            if el.x <= x <= el.x + el.w and el.y <= y <= el.y + el.h:
                area = el.w * el.h
                if area < best_area:
                    best = el
                    best_area = area
        return best

    async def dump_json(self, window_title: str = "") -> str:
        """Dump screen state as JSON (for debugging or AI input)."""
        state = await self.get_screen_state()
        els = state.elements
        if window_title:
            els = [e for e in els if window_title.lower() in e.window.lower()]
        return json.dumps(
            [e.to_dict() for e in els],
            ensure_ascii=False, indent=2
        )
