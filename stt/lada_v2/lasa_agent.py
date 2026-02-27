"""
lada_v2 - LASAAgent
LASA screen reading + input control, now merged into lada_v2.
Uses perception/screen_reader.py + input/input_controller.py directly.
"""

import asyncio
from typing import Optional, List
from perception.screen_reader import ScreenReader, UIElement, WindowInfo, ScreenState
from input.input_controller import InputController
from utils.logger import get_logger

log = get_logger("LASA")


class LASAAgent:
    """Screen reading + accurate UI input — merged into lada_v2."""

    def __init__(self):
        self.screen = ScreenReader()
        self.input  = InputController()

    async def start(self) -> bool:
        ok = await self.screen.initialize()
        log.info("LASAAgent ready" if ok else "LASAAgent running without AT-SPI")
        return ok

    # ── Screen ─────────────────────────────────────────────
    async def see(self, screenshot: bool = False) -> ScreenState:
        return await self.screen.get_screen_state(take_screenshot=screenshot)

    async def describe(self) -> str:
        state = await self.see()
        return state.to_text()

    async def find(self, name: str, role: str = "") -> Optional[UIElement]:
        state = await self.see()
        el = state.find(name, role)
        if el:
            log.info(f"Found: {el}")
        else:
            log.warning(f"Not found: '{name}' role='{role}'")
        return el

    async def find_all(self, name: str = "", role: str = "") -> List[UIElement]:
        state = await self.see()
        return state.find_all(name, role)

    async def windows(self) -> List[WindowInfo]:
        state = await self.see()
        return state.windows

    async def focused_window(self) -> Optional[str]:
        state = await self.see()
        return state.focused_window

    async def what_is_at(self, x: int, y: int) -> Optional[UIElement]:
        return await self.screen.element_at(x, y)

    # ── Click ──────────────────────────────────────────────
    async def click_on(self, name: str, role: str = "", wait_after: float = 0.3) -> tuple:
        """
        Find element by name and click it.
        Returns (success: bool, confidence: float).
        """
        el = await self.find(name, role)
        if el is None:
            log.error(f"click_on: '{name}' not found")
            return False, 0.0
        ok, conf = await self.input.click_element(el)
        if ok:
            await asyncio.sleep(wait_after)
        return ok, conf

    async def click_at(self, x: int, y: int, wait_after: float = 0.2) -> bool:
        ok = await self.input.click(x, y)
        if ok:
            await asyncio.sleep(wait_after)
        return ok

    async def double_click_on(self, name: str, role: str = "") -> bool:
        el = await self.find(name, role)
        if el is None:
            return False
        return await self.input.double_click(el.cx, el.cy)

    async def right_click_on(self, name: str, role: str = "") -> bool:
        el = await self.find(name, role)
        if el is None:
            return False
        return await self.input.right_click(el.cx, el.cy)

    # ── Keyboard ───────────────────────────────────────────
    async def type(self, text: str) -> bool:
        return await self.input.type(text)

    async def press(self, key: str) -> bool:
        return await self.input.key(key)

    async def type_into(self, element_name: str, text: str, clear_first: bool = True) -> bool:
        el = await self.find(element_name)
        if el is None:
            log.error(f"type_into: '{element_name}' not found")
            return False
        await self.input.focus_element(el)
        if clear_first:
            await self.input.key("ctrl+a")
            await asyncio.sleep(0.05)
        return await self.input.type_into(el, text)

    # ── Scroll ─────────────────────────────────────────────
    async def scroll(self, direction: str = "down", amount: int = 3,
                     x: Optional[int] = None, y: Optional[int] = None) -> bool:
        return await self.input.scroll(x or 683, y or 400, direction, amount)

    # ── Wait ───────────────────────────────────────────────
    async def wait_for(self, name: str, role: str = "",
                       timeout: float = 10.0, interval: float = 0.5) -> Optional[UIElement]:
        elapsed = 0.0
        while elapsed < timeout:
            el = await self.find(name, role)
            if el:
                log.info(f"wait_for: '{name}' appeared after {elapsed:.1f}s")
                return el
            await asyncio.sleep(interval)
            elapsed += interval
        log.warning(f"wait_for: '{name}' timed out after {timeout}s")
        return None

    async def element_exists(self, name: str, role: str = "") -> bool:
        return (await self.find(name, role)) is not None

    # ── Drag ───────────────────────────────────────────────
    async def drag(self, x1: int, y1: int, x2: int, y2: int) -> bool:
        return await self.input.drag(x1, y1, x2, y2)
