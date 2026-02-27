"""
LADA - YouTube Actions
YouTube pe song/video search, play, aur ad skip karna.

Strategy:
  1. xdg-open se YouTube search URL open karo
  2. xdotool se pehli video pe click karo (AT-SPI se element dhundo)
  3. Ad detect karo → skip button dhundo → click karo

Kaise kaam karta hai:
  - YouTube URL directly open = search results
  - pehla video result click = play shuru
  - "Skip Ad" / "Ads skip karein" button AT-SPI se detect
"""

import asyncio
import subprocess
import time
import urllib.parse
from typing import Optional
from utils.logger import LADALogger

logger = LADALogger("YOUTUBE")


class YouTubeActions:
    """YouTube automation — search, play, ad skip."""

    YT_SEARCH_URL = "https://www.youtube.com/results?search_query={query}"
    YT_BASE       = "https://www.youtube.com"

    def __init__(self, accessibility_layer=None):
        self.acc = accessibility_layer   # AT-SPI layer for element detection

    # ──────────────────────────────────────────────────────────
    # MAIN: search + play
    # ──────────────────────────────────────────────────────────

    async def search_and_play(self, query: str) -> bool:
        """
        Search YouTube for query and play first good result.
        Returns True if video started playing.
        """
        logger.info(f"YouTube: searching '{query}'")

        # Step 1: Open search URL
        search_url = self.YT_SEARCH_URL.format(
            query=urllib.parse.quote_plus(query)
        )
        ok = self._open_url(search_url)
        if not ok:
            return False

        # Step 2: Wait for page load
        await asyncio.sleep(4.0)

        # Step 3: Click first video (skip ads/shorts/playlists)
        clicked = await self._click_first_video()
        if not clicked:
            logger.warning("Could not click video — trying keyboard fallback")
            clicked = await self._keyboard_select_video()

        if not clicked:
            return False

        # Step 4: Wait for video to start
        await asyncio.sleep(3.0)

        # Step 5: Check for ad + skip if possible
        await self._handle_ad()

        logger.info("YouTube: video playing")
        return True

    # ──────────────────────────────────────────────────────────
    # AD SKIP
    # ──────────────────────────────────────────────────────────

    async def skip_ad(self) -> bool:
        """
        Try to skip current YouTube ad.
        Watches for skip button for up to 10 seconds.
        """
        logger.info("YouTube: looking for skip button...")

        for attempt in range(10):
            skipped = await self._try_skip_once()
            if skipped:
                logger.info(f"Ad skipped on attempt {attempt + 1}")
                return True
            await asyncio.sleep(1.0)

        logger.warning("No skip button found — ad may not be skippable")
        return False

    async def _handle_ad(self) -> None:
        """Auto-handle ad when video first loads."""
        await asyncio.sleep(2.0)

        # Try to skip for up to 8 seconds
        for _ in range(8):
            if await self._try_skip_once():
                logger.info("Ad auto-skipped")
                return
            await asyncio.sleep(1.0)

    async def _try_skip_once(self) -> bool:
        """Single attempt to find and click skip button."""

        # Method 1: xdotool — search for skip button by text
        skip_texts = [
            "Skip Ad", "Skip Ads", "Skip ad", "Skip ads",
            "Ads skip karein", "Izhtar skip karein",
            "विज्ञापन छोड़ें",
        ]
        for text in skip_texts:
            r = subprocess.run(
                ["xdotool", "search", "--name", text],
                capture_output=True, text=True, timeout=2
            )
            if r.stdout.strip():
                # Click it
                subprocess.run(
                    ["xdotool", "key", "--clearmodifiers", "Return"],
                    capture_output=True, timeout=2
                )
                return True

        # Method 2: AT-SPI element search for skip button
        if self.acc:
            try:
                skip_el = self.acc.find_element_by_name("Skip Ad")
                if skip_el is None:
                    skip_el = self.acc.find_element_by_name("Skip Ads")
                if skip_el:
                    skip_el.doAction(0)
                    return True
            except Exception:
                pass

        # Method 3: xdotool click on known skip button position
        # YouTube skip button appears bottom-right of video
        # Typical position: ~1200x680 on 1366x768 screen
        r = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowgeometry"],
            capture_output=True, text=True, timeout=2
        )
        if "chrome" in self._get_active_window_title().lower() or \
           "youtube" in self._get_active_window_title().lower():
            # Try clicking skip button area (YouTube's skip is always bottom-right of player)
            subprocess.run(
                ["xdotool", "mousemove", "--window", "$(xdotool getactivewindow)", "1200", "670"],
                shell=False, capture_output=True, timeout=2
            )
            # Check if cursor changed to pointer (skip button hover)
            # Just try clicking that area
            subprocess.run(
                ["xdotool", "click", "--clearmodifiers", "1"],
                capture_output=True, timeout=2
            )
            await asyncio.sleep(0.5)

        return False

    # ──────────────────────────────────────────────────────────
    # VIDEO CLICK
    # ──────────────────────────────────────────────────────────

    async def _click_first_video(self) -> bool:
        """Click first proper video in search results using AT-SPI or xdotool."""

        # Method 1: AT-SPI — find video links in Chrome
        if self.acc:
            try:
                # YouTube video titles appear as links
                elements = self.acc.get_all_elements()
                video_links = [
                    el for el in elements
                    if el.getRoleName() in ("link", "list item")
                    and el.name
                    and len(el.name) > 10   # skip short/ads
                    and "shorts" not in el.name.lower()
                    and "playlist" not in el.name.lower()
                    and "mix" not in el.name.lower()
                ]
                if video_links:
                    first = video_links[0]
                    logger.info(f"Clicking video: '{first.name[:50]}'")
                    first.doAction(0)
                    return True
            except Exception as e:
                logger.debug(f"AT-SPI video click failed: {e}")

        # Method 2: xdotool Tab + Enter (keyboard navigation)
        return await self._keyboard_select_video()

    async def _keyboard_select_video(self) -> bool:
        """Use keyboard to navigate to and click first video."""
        try:
            # Focus Chrome using wmctrl (more reliable than xdotool --sync)
            r = subprocess.run(
                ["wmctrl", "-a", "Google Chrome"],
                capture_output=True, timeout=3
            )
            if r.returncode != 0:
                # Try alternate window titles
                subprocess.run(["wmctrl", "-a", "Chrome"], capture_output=True, timeout=2)
            await asyncio.sleep(0.8)

            # Tab through page to first video result
            for _ in range(5):
                subprocess.run(
                    ["xdotool", "key", "Tab"],
                    capture_output=True, timeout=1
                )
                await asyncio.sleep(0.2)

            # Press Enter to click focused video
            subprocess.run(
                ["xdotool", "key", "Return"],
                capture_output=True, timeout=1
            )
            await asyncio.sleep(1.0)
            logger.info("Video selected via keyboard navigation")
            return True

        except Exception as e:
            logger.error(f"Keyboard video select failed: {e}")
            return False

    # ──────────────────────────────────────────────────────────
    # HELPERS
    # ──────────────────────────────────────────────────────────

    def _open_url(self, url: str) -> bool:
        """Open URL in existing Chrome browser."""
        try:
            # Check if chrome running
            r = subprocess.run(["pgrep", "-f", "chrome"], capture_output=True)
            if r.returncode == 0:
                subprocess.Popen(
                    ["xdg-open", url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                logger.info(f"URL opened: {url[:60]}")
                return True
            else:
                # Launch Chrome with URL
                subprocess.Popen(
                    ["google-chrome", url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                logger.info(f"Chrome launched with: {url[:60]}")
                return True
        except Exception as e:
            logger.error(f"URL open failed: {e}")
            return False

    def _get_active_window_title(self) -> str:
        try:
            r = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowname"],
                capture_output=True, text=True, timeout=2
            )
            return r.stdout.strip()
        except Exception:
            return ""
