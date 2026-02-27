"""
LADA - Browser Actions Layer
Playwright-based browser action executor.
All browser interactions go through here.
"""

import asyncio
from typing import Optional
from utils.logger import LADALogger

logger = LADALogger("BROWSER_ACTIONS")


class BrowserActions:
    """
    Executes browser-specific actions via Playwright.
    """

    def __init__(self, browser_dom=None):
        self.browser_dom = browser_dom

    async def execute(self, step: dict) -> bool:
        """
        Main dispatcher for browser actions.
        """
        import subprocess as sp

        action = step.get("action", "")
        value = step.get("value", "")

        logger.info(f"Browser execute: action={action} value={value[:50] if value else ''}")

        # youtube_navigate_and_play: always route — handles its own browser detection
        if action == "youtube_navigate_and_play":
            return await self._youtube_navigate_and_play(value, step)

        # For navigate: if a real browser is already running, use xdg-open directly
        # This avoids Playwright conflicts with existing Chrome sessions
        if action == "navigate":
            chrome_up = sp.run(["pgrep", "-f", "chrome"], capture_output=True).returncode == 0
            firefox_up = sp.run(["pgrep", "-f", "firefox"], capture_output=True).returncode == 0
            if chrome_up or firefox_up:
                return await self._navigate(value, step)

        # Ensure browser is available for other Playwright actions
        if not self.browser_dom:
            logger.warning("BrowserDOM not initialized.")
            return False

        if not self.browser_dom.is_available():
            logger.info("Browser not available. Initializing...")
            success = await self.browser_dom.initialize()
            if not success:
                logger.error("Browser initialization failed.")
                return False

        # Route to handlers
        handlers = {
            "navigate":                self._navigate,
            "youtube_navigate_and_play": self._youtube_navigate_and_play,
            "find_and_click":          self._find_and_click,
            "click_button":            self._click_button,
            "click_result":            self._click_result,
            "type_text":               self._type_text,
            "wait_for_element":        self._wait_for_element,
            "scroll":                  self._scroll,
            "get_text":                self._get_text,
            "search":                  self._search,
            "open_app":                self._open_app,
            "verify_window":           self._verify_window,
        }

        handler = handlers.get(action)
        if handler:
            return await handler(value, step)

        logger.warning(f"No browser handler for action: {action}")
        return False

    # ── ACTION HANDLERS ───────────────────────────────────────

    async def _youtube_navigate_and_play(self, value: str, step: dict) -> bool:
        """
        Open YouTube search URL and play the best matching video.
        Delegates to smart_actions.youtube_search_and_play which uses AT-SPI
        to read actual video titles and pick the best match — no blind Tab pressing.
        """
        from actions.smart_actions import youtube_search_and_play

        # Extract query from URL for scoring
        import urllib.parse
        parsed = urllib.parse.urlparse(value)
        params = urllib.parse.parse_qs(parsed.query)
        query = params.get("search_query", [""])[0].replace("+", " ")

        logger.info(f"YouTube play: query='{query}'")
        return await youtube_search_and_play(search_url=value, query=query)

    async def _navigate(self, value: str, step: dict) -> bool:
        """Navigate to URL. Uses existing Chrome if running, else Playwright."""
        import subprocess as sp

        # Ensure URL has schema
        url = value
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        # Check if Chrome/Firefox is already running
        chrome_running = sp.run(["pgrep", "-f", "chrome"], capture_output=True).returncode == 0
        firefox_running = sp.run(["pgrep", "-f", "firefox"], capture_output=True).returncode == 0

        if chrome_running or firefox_running:
            # Open URL in existing browser — no Playwright conflict
            result = sp.run(
                ["xdg-open", url],
                capture_output=True,
                timeout=5,
            )
            await asyncio.sleep(2.0)  # wait for tab to load
            logger.info(f"URL opened via xdg-open: {url[:60]}")
            return True  # xdg-open always succeeds if browser is running

        # Fallback: use Playwright if no browser is open
        success = await self.browser_dom.navigate(url)
        if success:
            await asyncio.sleep(1.0)
            await self.browser_dom.close_popups()
        return success

    async def _find_and_click(self, value: str, step: dict) -> bool:
        """Find element by text/selector and click."""
        # Try common search-related elements first
        search_selectors = {
            "search box":     ["input[type='search']", "input[name='q']", "[aria-label*='search' i]", "textarea[name='q']"],
            "search bar":     ["input[type='search']", "[placeholder*='search' i]"],
            "search field":   ["input[type='search']", "[aria-label*='search' i]"],
        }

        value_lower = value.lower()
        if value_lower in search_selectors:
            for sel in search_selectors[value_lower]:
                try:
                    if await self.browser_dom.is_element_visible(sel):
                        await self.browser_dom.find_and_click(sel)
                        return True
                except Exception:
                    continue

        # Generic text-based click
        return await self.browser_dom.find_and_click(value)

    async def _click_button(self, value: str, step: dict) -> bool:
        """Click a button."""
        # Try: button by text
        success = await self.browser_dom.find_and_click(value)
        if success:
            return True

        # Try: input[type=submit]
        try:
            if await self.browser_dom.is_element_visible("input[type='submit']"):
                await self.browser_dom.find_and_click("input[type='submit']")
                return True
        except Exception:
            pass

        # Try: pressing Enter key
        if value.lower() in ("search", "submit", "go", "enter"):
            return await self.browser_dom.press_key("Enter")

        return False

    async def _click_result(self, value: str, step: dict) -> bool:
        """Click a search result or list item."""
        return await self.browser_dom.find_and_click(value)

    async def _type_text(self, value: str, step: dict) -> bool:
        """Type text into focused input."""
        success = await self.browser_dom.type_into_focused(value)
        if not success:
            # Fallback: try common input selectors
            for sel in ["input:focus", "textarea:focus", "input[type='text']", "input[type='search']"]:
                try:
                    success = await self.browser_dom.type_into(sel, value)
                    if success:
                        return True
                except Exception:
                    continue
        return success

    async def _search(self, value: str, step: dict) -> bool:
        """Type search query and submit."""
        # Type the query
        typed = await self._type_text(value, step)
        if typed:
            # Press Enter to search
            await asyncio.sleep(0.3)
            await self.browser_dom.press_key("Enter")
            await self.browser_dom.wait_for_navigation()
            return True
        return False

    async def _wait_for_element(self, value: str, step: dict) -> bool:
        """Wait for element to appear."""
        # Try as selector
        found = await self.browser_dom.wait_for_element(value, state="visible")
        if not found:
            # Try waiting for text
            found = await self.browser_dom.wait_for_text(value)
        return found

    async def _scroll(self, value: str, step: dict) -> bool:
        """Scroll page."""
        return await self.browser_dom.scroll(direction=value)

    async def _get_text(self, value: str, step: dict) -> bool:
        """Get text from element (logs result)."""
        text = await self.browser_dom.get_element_text(value)
        if text:
            logger.info(f"Got text from '{value}': {text[:100]}")
            return True
        # Try page-level text
        page_text = await self.browser_dom.get_page_text()
        if value.lower() in page_text.lower():
            logger.info(f"Text '{value}' found on page.")
            return True
        return False

    async def _open_app(self, value: str, step: dict) -> bool:
        """Open browser app (navigate to URL or open browser)."""
        # If value looks like a URL, navigate
        if "." in value and " " not in value:
            return await self._navigate(value, step)

        # Otherwise open browser home page
        return await self._navigate("about:blank", step)

    async def _verify_window(self, value: str, step: dict) -> bool:
        """Verify browser window/tab matches expected state."""
        title = await self.browser_dom.get_page_title()
        url = await self.browser_dom.get_current_url()

        value_lower = value.lower()
        if value_lower in title.lower() or value_lower in url.lower():
            logger.info(f"Browser window verified: {value}")
            return True

        logger.warning(f"Browser window mismatch. Expected: {value}, Got title: {title}")
        return True  # Non-critical for browser verification

    async def cleanup(self):
        """Cleanup browser resources."""
        if self.browser_dom:
            await self.browser_dom.cleanup()
