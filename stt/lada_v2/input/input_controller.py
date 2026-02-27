"""
LASA - InputController
Sab input operations yahan se hote hain.

Priority (automatic fallback chain):
  1. AT-SPI doAction  → safest, accessibility API se
  2. xdotool          → reliable, X11 level
  3. evdev uinput     → raw kernel (needs /dev/uinput access)

API:
  ic = InputController()
  
  await ic.click(x, y)                    # left click at coordinates
  await ic.double_click(x, y)             # double click
  await ic.right_click(x, y)             # right click
  await ic.click_element(UIElement)       # click via AT-SPI (no coords needed)
  
  await ic.type("hello world")            # type text
  await ic.key("Return")                  # press Enter
  await ic.key("ctrl+s")                  # Ctrl+S
  await ic.key("ctrl+shift+s")            # Ctrl+Shift+S
  
  await ic.scroll(x, y, "down", 3)       # scroll 3 steps down
  await ic.drag(x1, y1, x2, y2)          # drag
"""

import asyncio
import subprocess
import shutil
from typing import Optional
from utils.logger import get_logger

log = get_logger("INPUT")


class InputController:

    def __init__(self):
        self._xdotool = bool(shutil.which("xdotool"))
        self._evdev_ok = False  # checked lazily
        if self._xdotool:
            log.info("InputController ready (xdotool primary, evdev fallback)")
        else:
            log.warning("xdotool not found! Install: sudo apt install xdotool")

    # ──────────────────────────────────────────────
    # MOUSE CLICK
    # ──────────────────────────────────────────────

    async def click(self, x: int, y: int, button: str = "left") -> bool:
        """Single mouse click at (x, y)."""
        btn = {"left": "1", "middle": "2", "right": "3"}.get(button, "1")
        ok = await self._xdo(["mousemove", "--sync", str(x), str(y)])
        if ok:
            await asyncio.sleep(0.05)
            ok = await self._xdo(["click", btn])
        if not ok:
            ok = await self._evdev_click(x, y)
        if ok:
            log.info(f"Clicked ({button}) @ ({x}, {y})")
        return ok

    async def double_click(self, x: int, y: int) -> bool:
        """Double click at (x, y)."""
        await self._xdo(["mousemove", "--sync", str(x), str(y)])
        await asyncio.sleep(0.05)
        ok = await self._xdo(["click", "--repeat", "2", "--delay", "120", "1"])
        if ok:
            log.info(f"Double-clicked @ ({x}, {y})")
        return ok

    async def right_click(self, x: int, y: int) -> bool:
        """Right click at (x, y)."""
        return await self.click(x, y, button="right")

    async def middle_click(self, x: int, y: int) -> bool:
        """Middle click (paste) at (x, y)."""
        return await self.click(x, y, button="middle")

    # ──────────────────────────────────────────────
    # AT-SPI ELEMENT CLICK (no coordinates needed)
    # ──────────────────────────────────────────────

    async def click_element(self, element) -> tuple:
        """
        Click a UIElement. Returns (success: bool, confidence: float).

        Confidence levels:
          0.9 = AT-SPI doAction, exact name match
          0.7 = AT-SPI doAction, partial name match
          0.5 = coordinate click, element found
          0.3 = coordinate click, element found by partial match
          0.0 = failed
        """
        if element is None:
            return False, 0.0

        exact_match = True  # will be False if name was partial match

        # 1. AT-SPI doAction (highest confidence — API level)
        if element._accessible is not None:
            try:
                node   = element._accessible
                action = node.queryAction()
                for i in range(action.nActions):
                    name = action.getName(i).lower()
                    if name in ("click", "press", "activate", "toggle"):
                        action.doAction(i)
                        await asyncio.sleep(0.15)
                        conf = 0.9 if exact_match else 0.7
                        log.info(
                            f"AT-SPI doAction({name}): '{element.name}' "
                            f"confidence={conf:.1f}"
                        )
                        return True, conf
            except Exception as e:
                log.debug(f"AT-SPI doAction failed: {e}")

        # 2. Coordinate click (medium confidence — visual position)
        if element.cx > 0 and element.cy > 0:
            ok = await self.click(element.cx, element.cy)
            conf = 0.5 if ok and exact_match else 0.3
            log.info(
                f"Coord click: '{element.name}' @ ({element.cx},{element.cy}) "
                f"confidence={conf:.1f}"
            )
            return ok, conf if ok else 0.0

        log.warning(f"Cannot click: '{element.name}' — no method available")
        return False, 0.0

    async def focus_element(self, element) -> bool:
        """Focus an element via AT-SPI grabFocus."""
        if element is None or element._accessible is None:
            return False
        try:
            element._accessible.queryComponent().grabFocus()
            await asyncio.sleep(0.1)
            return True
        except Exception:
            # Fallback: click it
            return await self.click_element(element)

    # ──────────────────────────────────────────────
    # TYPING
    # ──────────────────────────────────────────────

    async def type(self, text: str, delay_ms: int = 30) -> bool:
        """Type a string of text into currently focused element."""
        ok = await self._xdo([
            "type", "--clearmodifiers",
            "--delay", str(delay_ms),
            "--", text
        ])
        if ok:
            log.info(f"Typed: {text[:50]}{'...' if len(text)>50 else ''}")
        return ok

    async def type_into(self, element, text: str) -> bool:
        """
        Type text into a specific element.
        Tries AT-SPI insertText first, then focus+type.
        """
        if element is None:
            return False

        # 1. AT-SPI insertText (cleanest — doesn't need focus)
        if element._accessible is not None:
            try:
                editable = element._accessible.queryEditableText()
                # Clear existing text first, then insert
                editable.setTextContents(text)
                log.info(f"AT-SPI setText: '{text[:40]}'")
                return True
            except Exception:
                pass

            try:
                editable = element._accessible.queryEditableText()
                editable.insertText(0, text, len(text))
                log.info(f"AT-SPI insertText: '{text[:40]}'")
                return True
            except Exception:
                pass

        # 2. Focus + type
        await self.focus_element(element)
        await asyncio.sleep(0.1)
        return await self.type(text)

    # ──────────────────────────────────────────────
    # KEYBOARD
    # ──────────────────────────────────────────────

    async def key(self, keyname: str) -> bool:
        """
        Press a key or combination.
        
        Examples:
          "Return" or "enter"     → Enter key
          "Escape" or "esc"       → Escape
          "ctrl+s"                → Ctrl+S (save)
          "ctrl+shift+s"          → Ctrl+Shift+S (save as)
          "ctrl+c"                → Copy
          "ctrl+v"                → Paste
          "ctrl+z"                → Undo
          "alt+F4"                → Close window
          "super"                 → Open menu
          "F5"                    → Refresh
          "Delete"                → Delete key
          "BackSpace"             → Backspace
        """
        # Normalize common shorthands
        aliases = {
            "enter":      "Return",
            "esc":        "Escape",
            "del":        "Delete",
            "backspace":  "BackSpace",
            "tab":        "Tab",
            "space":      "space",
            "super":      "super",
            "win":        "super",
            "home":       "Home",
            "end":        "End",
            "pageup":     "Page_Up",
            "pagedown":   "Page_Down",
            "up":         "Up",
            "down":       "Down",
            "left":       "Left",
            "right":      "Right",
        }
        # Handle combos like "ctrl+s" — normalize each part
        parts = keyname.split("+")
        normalized = "+".join(aliases.get(p.lower(), p) for p in parts)

        ok = await self._xdo(["key", "--clearmodifiers", normalized])
        if ok:
            log.info(f"Key: {normalized}")
        return ok

    async def clear_and_type(self, text: str) -> bool:
        """Select all and replace with text (useful for text fields)."""
        await self.key("ctrl+a")
        await asyncio.sleep(0.05)
        return await self.type(text)

    # ──────────────────────────────────────────────
    # SCROLL
    # ──────────────────────────────────────────────

    async def scroll(
        self, x: int, y: int,
        direction: str = "down",
        amount: int = 3
    ) -> bool:
        """Scroll at (x,y). direction: up/down/left/right."""
        btn_map = {"down": "5", "up": "4", "right": "7", "left": "6"}
        btn = btn_map.get(direction.lower(), "5")
        await self._xdo(["mousemove", str(x), str(y)])
        for _ in range(amount):
            await self._xdo(["click", btn])
            await asyncio.sleep(0.04)
        log.info(f"Scrolled {direction} x{amount} @ ({x},{y})")
        return True

    # ──────────────────────────────────────────────
    # DRAG
    # ──────────────────────────────────────────────

    async def drag(
        self, x1: int, y1: int, x2: int, y2: int,
        duration_ms: int = 400
    ) -> bool:
        """Drag from (x1,y1) to (x2,y2)."""
        await self._xdo(["mousemove", str(x1), str(y1)])
        await asyncio.sleep(0.05)
        await self._xdo(["mousedown", "1"])
        await asyncio.sleep(duration_ms / 1000)
        # Move smoothly in steps
        steps = 10
        for i in range(1, steps + 1):
            ix = x1 + (x2 - x1) * i // steps
            iy = y1 + (y2 - y1) * i // steps
            await self._xdo(["mousemove", str(ix), str(iy)])
            await asyncio.sleep(0.02)
        await self._xdo(["mouseup", "1"])
        log.info(f"Dragged ({x1},{y1}) → ({x2},{y2})")
        return True

    # ──────────────────────────────────────────────
    # MOUSE POSITION
    # ──────────────────────────────────────────────

    async def move(self, x: int, y: int) -> bool:
        """Move mouse to (x, y) without clicking."""
        return await self._xdo(["mousemove", "--sync", str(x), str(y)])

    def get_position(self) -> tuple:
        """Get current mouse position (x, y)."""
        try:
            r = subprocess.run(
                ["xdotool", "getmouselocation", "--shell"],
                capture_output=True, text=True, timeout=2
            )
            pos = {}
            for line in r.stdout.strip().split("\n"):
                if "=" in line:
                    k, v = line.split("=", 1)
                    pos[k.strip()] = v.strip()
            return int(pos.get("X", 0)), int(pos.get("Y", 0))
        except Exception:
            return 0, 0

    # ──────────────────────────────────────────────
    # EVDEV FALLBACK
    # ──────────────────────────────────────────────

    async def _evdev_click(self, x: int, y: int) -> bool:
        """Raw evdev click — last resort when xdotool fails."""
        try:
            import evdev
            from evdev import UInput, ecodes as ec, AbsInfo

            cap = {
                ec.EV_KEY: [ec.BTN_LEFT, ec.BTN_RIGHT],
                ec.EV_ABS: [
                    (ec.ABS_X, AbsInfo(0, 0, 32767, 0, 0, 0)),
                    (ec.ABS_Y, AbsInfo(0, 0, 32767, 0, 0, 0)),
                ],
                ec.EV_SYN: [],
            }
            # Normalize coords to 0-32767
            nx = int(x * 32767 / 1366)
            ny = int(y * 32767 / 768)

            with UInput(cap, name="lasa-pointer") as ui:
                ui.write(ec.EV_ABS, ec.ABS_X, nx)
                ui.write(ec.EV_ABS, ec.ABS_Y, ny)
                ui.syn()
                await asyncio.sleep(0.05)
                ui.write(ec.EV_KEY, ec.BTN_LEFT, 1)
                ui.syn()
                await asyncio.sleep(0.05)
                ui.write(ec.EV_KEY, ec.BTN_LEFT, 0)
                ui.syn()

            self._evdev_ok = True
            log.info(f"evdev click @ ({x},{y})")
            return True

        except ImportError:
            log.debug("evdev not installed. Install: pip install evdev")
            return False
        except PermissionError:
            log.warning("evdev: need /dev/uinput access. Run: sudo chmod 0660 /dev/uinput")
            return False
        except Exception as e:
            log.debug(f"evdev error: {e}")
            return False

    # ──────────────────────────────────────────────
    # INTERNAL
    # ──────────────────────────────────────────────

    async def _xdo(self, args: list) -> bool:
        if not self._xdotool:
            return False
        try:
            r = subprocess.run(
                ["xdotool"] + args,
                capture_output=True, timeout=5
            )
            return r.returncode == 0
        except Exception:
            return False
