"""
LADA - InputController
Sab input operations ka single point of truth.

Priority:
  1. AT-SPI doAction (safest — accessibility API)
  2. xdotool (reliable — X11 level)
  3. evdev (raw kernel — fallback, needs /dev/input access)

Exports ONE clean API:
  ic = InputController()
  await ic.click(x, y)
  await ic.double_click(x, y)
  await ic.right_click(x, y)
  await ic.type(text)
  await ic.key(keyname)          # "Return", "ctrl+s", "ctrl+shift+s"
  await ic.scroll(x, y, "down", amount=3)
  await ic.drag(x1, y1, x2, y2)
  await ic.move(x, y)
"""

import asyncio
import subprocess
import shutil
from typing import Optional, Tuple
from utils.logger import LADALogger

logger = LADALogger("INPUT_CTRL")


class InputController:
    """
    Unified input controller.
    Tries xdotool first (always available on X11),
    falls back to evdev if xdotool fails.
    """

    def __init__(self):
        self._has_xdotool  = bool(shutil.which("xdotool"))
        self._has_ydotool  = bool(shutil.which("ydotool"))   # Wayland alternative
        self._evdev_mouse  = None   # lazy init
        self._evdev_kbd    = None   # lazy init
        self._screen_w     = 1366
        self._screen_h     = 768

        if not self._has_xdotool:
            logger.warning("xdotool not found — only evdev available")
        else:
            logger.info("InputController ready (xdotool + evdev fallback)")

    # ─────────────────────────────────────────────
    # MOUSE
    # ─────────────────────────────────────────────

    async def click(self, x: int, y: int, button: str = "left") -> bool:
        """Single click at coordinates."""
        btn = {"left": "1", "middle": "2", "right": "3"}.get(button, "1")
        return await self._xdo(["mousemove", str(x), str(y), "click", btn])

    async def double_click(self, x: int, y: int) -> bool:
        """Double click at coordinates."""
        return await self._xdo(
            ["mousemove", str(x), str(y), "click", "--repeat", "2", "--delay", "100", "1"]
        )

    async def right_click(self, x: int, y: int) -> bool:
        """Right click at coordinates."""
        return await self.click(x, y, button="right")

    async def move(self, x: int, y: int) -> bool:
        """Move mouse to coordinates."""
        return await self._xdo(["mousemove", str(x), str(y)])

    async def drag(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 400) -> bool:
        """Drag from (x1,y1) to (x2,y2)."""
        ok1 = await self._xdo(["mousemove", str(x1), str(y1)])
        if not ok1:
            return False
        await asyncio.sleep(0.1)
        ok2 = await self._xdo(["mousedown", "1"])
        if not ok2:
            return False
        await asyncio.sleep(duration_ms / 1000)
        ok3 = await self._xdo(["mousemove", str(x2), str(y2)])
        await asyncio.sleep(0.1)
        ok4 = await self._xdo(["mouseup", "1"])
        return ok3 and ok4

    async def scroll(
        self,
        x: int, y: int,
        direction: str = "down",
        amount: int = 3
    ) -> bool:
        """Scroll at coordinates. direction = up/down/left/right."""
        btn_map = {"down": "5", "up": "4", "right": "7", "left": "6"}
        btn = btn_map.get(direction, "5")
        await self.move(x, y)
        for _ in range(amount):
            await self._xdo(["click", btn])
            await asyncio.sleep(0.05)
        return True

    # ─────────────────────────────────────────────
    # KEYBOARD
    # ─────────────────────────────────────────────

    async def type(self, text: str, delay_ms: int = 30) -> bool:
        """Type a string of text."""
        return await self._xdo([
            "type", "--clearmodifiers", "--delay", str(delay_ms), "--", text
        ])

    async def key(self, keyname: str) -> bool:
        """
        Press a key or key combination.
        Examples: "Return", "Escape", "ctrl+s", "ctrl+shift+s", "super"
        """
        # Normalize common names
        keymap = {
            "enter":    "Return",
            "esc":      "Escape",
            "del":      "Delete",
            "backspace":"BackSpace",
            "tab":      "Tab",
            "space":    "space",
            "super":    "super",
            "win":      "super",
        }
        normalized = keymap.get(keyname.lower(), keyname)
        return await self._xdo(["key", "--clearmodifiers", normalized])

    async def hotkey(self, *keys: str) -> bool:
        """
        Press multiple keys simultaneously.
        hotkey("ctrl", "s") → Ctrl+S
        """
        combo = "+".join(keys)
        return await self.key(combo)

    # ─────────────────────────────────────────────
    # AT-SPI ELEMENT INTERACTION
    # ─────────────────────────────────────────────

    async def click_element(self, element) -> bool:
        """
        Click via AT-SPI doAction (preferred — no coordinates needed).
        Falls back to coordinate click using element's bounding box.
        """
        if element is None:
            return False
        try:
            import pyatspi
            # Try doAction first
            try:
                action = element.queryAction()
                for i in range(action.nActions):
                    name = action.getName(i).lower()
                    if name in ("click", "press", "activate"):
                        action.doAction(i)
                        await asyncio.sleep(0.2)
                        logger.info(f"AT-SPI click via doAction({i}): {element.name}")
                        return True
            except Exception:
                pass

            # Fallback: get bounding box and xdotool click
            try:
                comp = element.queryComponent()
                bbox = comp.getExtents(pyatspi.DESKTOP_COORDS)
                cx = bbox.x + bbox.width  // 2
                cy = bbox.y + bbox.height // 2
                if 0 < cx < 3840 and 0 < cy < 2160:
                    logger.info(f"AT-SPI click via coords ({cx},{cy}): {element.name}")
                    return await self.click(cx, cy)
            except Exception:
                pass

        except ImportError:
            pass

        return False

    async def focus_element(self, element) -> bool:
        """Focus an element via AT-SPI grabFocus."""
        if element is None:
            return False
        try:
            element.queryComponent().grabFocus()
            await asyncio.sleep(0.15)
            return True
        except Exception:
            return False

    async def type_into_element(self, element, text: str) -> bool:
        """
        Type text into an element.
        Tries AT-SPI insertText first, then focus+type.
        """
        if element is None:
            return False

        # 1. AT-SPI insertText (cleanest)
        try:
            editable = element.queryEditableText()
            editable.insertText(0, text, len(text))
            logger.info(f"AT-SPI insertText: {text[:30]}")
            return True
        except Exception:
            pass

        # 2. Focus element then xdotool type
        await self.focus_element(element)
        return await self.type(text)

    # ─────────────────────────────────────────────
    # EVDEV (raw kernel input — last resort)
    # ─────────────────────────────────────────────

    async def click_evdev(self, x: int, y: int) -> bool:
        """
        Click using evdev uinput (works even without X11).
        Requires: pip install evdev, and /dev/uinput write access.
        """
        try:
            import evdev
            from evdev import UInput, ecodes as e

            cap = {
                e.EV_KEY: [e.BTN_LEFT],
                e.EV_REL: [e.REL_X, e.REL_Y],
                e.EV_SYN: [e.SYN_REPORT],
            }
            with UInput(cap, name="lada-mouse") as ui:
                # Move to position
                ui.write(e.EV_REL, e.REL_X, x)
                ui.write(e.EV_REL, e.REL_Y, y)
                ui.syn()
                await asyncio.sleep(0.05)
                # Click
                ui.write(e.EV_KEY, e.BTN_LEFT, 1)
                ui.syn()
                await asyncio.sleep(0.05)
                ui.write(e.EV_KEY, e.BTN_LEFT, 0)
                ui.syn()
            logger.info(f"evdev click at ({x},{y})")
            return True

        except ImportError:
            logger.warning("evdev not installed: pip install evdev")
            return False
        except PermissionError:
            logger.warning("evdev: no permission for /dev/uinput. Try: sudo chmod 0660 /dev/uinput")
            return False
        except Exception as e:
            logger.warning(f"evdev click error: {e}")
            return False

    # ─────────────────────────────────────────────
    # INTERNAL
    # ─────────────────────────────────────────────

    async def _xdo(self, args: list) -> bool:
        """Run xdotool command."""
        if not self._has_xdotool:
            return False
        try:
            result = subprocess.run(
                ["xdotool"] + args,
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0