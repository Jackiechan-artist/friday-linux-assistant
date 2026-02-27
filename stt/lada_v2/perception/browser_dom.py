"""
LADA - Browser DOM Layer
Uses Playwright for stable browser control.
Always headful mode. No pixel clicking in browser.
"""

import asyncio
from typing import Optional, Any
from utils.logger import LADALogger

logger = LADALogger("BROWSER_DOM")


class BrowserDOMLayer:
    """
    Manages browser via Playwright.
    Handles navigation, element finding, clicking, typing.
    """

    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self._available = False
        self.default_timeout = 15000   # 15 seconds
        self.navigation_timeout = 30000  # 30 seconds

    async def initialize(self) -> bool:
        """Initialize Playwright browser."""
        try:
            from playwright.async_api import async_playwright
            self._playwright_context = async_playwright()
            self.playwright = await self._playwright_context.__aenter__()

            # Launch headful browser
            self.browser = await self.playwright.chromium.launch(
                headless=False,
                args=[
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-notifications",
                ]
            )

            # Create context with reasonable settings
            self.context = await self.browser.new_context(
                viewport={"width": 1280, "height": 800},
                accept_downloads=True,
            )

            # Default timeouts
            self.context.set_default_timeout(self.default_timeout)
            self.context.set_default_navigation_timeout(self.navigation_timeout)

            self.page = await self.context.new_page()
            self._available = True
            logger.info("Playwright browser initialized (headful).")
            return True

        except ImportError:
            logger.warning("Playwright not installed. Browser layer disabled.")
            self._available = False
            return False
        except Exception as e:
            logger.warning(f"Browser init failed: {e}")
            self._available = False
            return False

    def is_available(self) -> bool:
        return self._available and self.page is not None

    async def ensure_page(self):
        """Ensure browser and page are ready. Initialize if needed."""
        if not self._available:
            await self.initialize()
        if self.page is None or self.page.is_closed():
            self.page = await self.context.new_page()

    # ── NAVIGATION ────────────────────────────────────────────

    async def navigate(self, url: str) -> bool:
        """Navigate to a URL and wait for page load."""
        await self.ensure_page()
        try:
            await self.page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self.navigation_timeout
            )
            logger.info(f"Navigated to: {url}")
            return True
        except Exception as e:
            logger.warning(f"Navigation failed for {url}: {e}")
            return False

    async def get_current_url(self) -> str:
        """Get the current page URL."""
        if not self.is_available():
            return ""
        try:
            return self.page.url
        except Exception:
            return ""

    async def get_page_title(self) -> str:
        """Get the current page title."""
        if not self.is_available():
            return ""
        try:
            return await self.page.title()
        except Exception:
            return ""

    # ── ELEMENT INTERACTIONS ──────────────────────────────────

    async def find_and_click(
        self,
        selector: str,
        timeout: Optional[int] = None
    ) -> bool:
        """Find element by selector and click it."""
        await self.ensure_page()
        timeout = timeout or self.default_timeout
        try:
            # Try text selector first
            locator = self.page.get_by_text(selector, exact=False)
            await locator.first.wait_for(state="visible", timeout=timeout)
            await locator.first.click()
            logger.info(f"Clicked by text: {selector}")
            return True
        except Exception:
            pass

        # Try role-based selector
        try:
            locator = self.page.get_by_role("button", name=selector)
            await locator.first.wait_for(state="visible", timeout=timeout // 2)
            await locator.first.click()
            logger.info(f"Clicked by role+name: {selector}")
            return True
        except Exception:
            pass

        # Try CSS/XPath selector
        try:
            locator = self.page.locator(selector)
            await locator.first.wait_for(state="visible", timeout=timeout // 2)
            await locator.first.click()
            logger.info(f"Clicked by CSS selector: {selector}")
            return True
        except Exception as e:
            logger.warning(f"find_and_click failed for '{selector}': {e}")
            return False

    async def click_by_text(self, text: str) -> bool:
        """Click element containing specific text."""
        return await self.find_and_click(text)

    async def type_into(
        self,
        selector: str,
        text: str,
        clear_first: bool = True
    ) -> bool:
        """Type text into an input element."""
        await self.ensure_page()
        try:
            locator = self.page.locator(selector)
            await locator.first.wait_for(state="visible")

            if clear_first:
                await locator.first.clear()

            await locator.first.type(text, delay=50)
            logger.info(f"Typed into {selector}: {text[:30]}")
            return True
        except Exception as e:
            logger.warning(f"type_into failed for '{selector}': {e}")
            return False

    async def type_into_focused(self, text: str) -> bool:
        """Type text into whatever is currently focused."""
        await self.ensure_page()
        try:
            await self.page.keyboard.type(text, delay=50)
            return True
        except Exception as e:
            logger.warning(f"type_into_focused failed: {e}")
            return False

    async def press_key(self, key: str) -> bool:
        """Press a keyboard key."""
        await self.ensure_page()
        try:
            await self.page.keyboard.press(key)
            return True
        except Exception as e:
            logger.warning(f"press_key failed for {key}: {e}")
            return False

    # ── WAITING ───────────────────────────────────────────────

    async def wait_for_element(
        self,
        selector: str,
        state: str = "visible",
        timeout: Optional[int] = None
    ) -> bool:
        """Wait for an element to reach a specific state."""
        await self.ensure_page()
        timeout = timeout or self.default_timeout
        try:
            await self.page.wait_for_selector(
                selector,
                state=state,
                timeout=timeout
            )
            return True
        except Exception as e:
            logger.warning(f"wait_for_element timeout for '{selector}': {e}")
            return False

    async def wait_for_text(
        self,
        text: str,
        timeout: Optional[int] = None
    ) -> bool:
        """Wait for specific text to appear on page."""
        await self.ensure_page()
        timeout = timeout or self.default_timeout
        try:
            await self.page.wait_for_function(
                f"document.body.innerText.includes({repr(text)})",
                timeout=timeout
            )
            return True
        except Exception:
            return False

    async def wait_for_navigation(self) -> bool:
        """Wait for page navigation to complete."""
        await self.ensure_page()
        try:
            await self.page.wait_for_load_state("networkidle", timeout=15000)
            return True
        except Exception:
            try:
                await self.page.wait_for_load_state("domcontentloaded", timeout=10000)
                return True
            except Exception:
                return False

    # ── INFORMATION EXTRACTION ────────────────────────────────

    async def get_element_text(self, selector: str) -> str:
        """Get text content of an element."""
        await self.ensure_page()
        try:
            element = self.page.locator(selector).first
            return await element.inner_text()
        except Exception:
            return ""

    async def get_input_value(self, selector: str) -> str:
        """Get the value of an input element."""
        await self.ensure_page()
        try:
            return await self.page.input_value(selector)
        except Exception:
            return ""

    async def is_element_visible(self, selector: str) -> bool:
        """Check if an element is visible."""
        await self.ensure_page()
        try:
            return await self.page.is_visible(selector)
        except Exception:
            return False

    async def get_page_text(self) -> str:
        """Get all visible text from page."""
        await self.ensure_page()
        try:
            return await self.page.inner_text("body")
        except Exception:
            return ""

    # ── SCROLLING ─────────────────────────────────────────────

    async def scroll(self, direction: str = "down", amount: int = 300) -> bool:
        """Scroll the page."""
        await self.ensure_page()
        try:
            if direction == "down":
                await self.page.keyboard.press("PageDown")
            elif direction == "up":
                await self.page.keyboard.press("PageUp")
            elif direction == "top":
                await self.page.keyboard.press("Home")
            elif direction == "bottom":
                await self.page.keyboard.press("End")
            return True
        except Exception as e:
            logger.warning(f"Scroll failed: {e}")
            return False

    # ── POPUP HANDLING ────────────────────────────────────────

    async def handle_dialog(self, accept: bool = True) -> None:
        """Set up auto-handler for browser dialogs."""
        async def dialog_handler(dialog):
            if accept:
                await dialog.accept()
            else:
                await dialog.dismiss()

        self.page.on("dialog", dialog_handler)

    async def close_popups(self) -> bool:
        """Try to close common popup elements."""
        popup_selectors = [
            "button[aria-label='Close']",
            "[data-testid='cookie-accept']",
            ".popup-close",
            ".modal-close",
            "button:has-text('Accept')",
            "button:has-text('OK')",
            "button:has-text('Close')",
        ]

        for sel in popup_selectors:
            try:
                if await self.page.is_visible(sel, timeout=500):
                    await self.page.click(sel)
                    logger.debug(f"Closed popup: {sel}")
                    return True
            except Exception:
                continue

        return False

    # ── SCREENSHOT ────────────────────────────────────────────

    async def take_screenshot(self, path: str = "/tmp/lada_browser.png") -> bool:
        """Take a screenshot of the current page."""
        await self.ensure_page()
        try:
            await self.page.screenshot(path=path, full_page=False)
            logger.debug(f"Browser screenshot saved: {path}")
            return True
        except Exception as e:
            logger.warning(f"Screenshot failed: {e}")
            return False

    # ── CLEANUP ───────────────────────────────────────────────

    async def cleanup(self):
        """Close browser and Playwright."""
        try:
            if self.page and not self.page.is_closed():
                await self.page.close()
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self._playwright_context.__aexit__(None, None, None)
            self._available = False
            logger.info("Browser cleaned up.")
        except Exception as e:
            logger.warning(f"Browser cleanup error: {e}")
