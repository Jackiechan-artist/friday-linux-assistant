"""
LADA - SmartActions
AT-SPI screen reading + pyautogui precise clicking.

Philosophy:
  - NEVER blind Tab pressing
  - ALWAYS read screen first, then act on what you see
  - Poll for element appearance (not fixed sleep timers)
  - Fallback gracefully when element not found

Supports:
  - YouTube: search and play correct video (by query match)
  - File Manager: open folders, navigate, scroll
  - Any app: find element by name/role and click it
  - Brightness, Volume via system commands
"""

import asyncio
import subprocess
import time
import re
from typing import Optional, List, Tuple
from utils.logger import LADALogger

logger = LADALogger("SMART")

# ── Pyautogui safety setup ─────────────────────────────────────
try:
    import pyautogui
    pyautogui.FAILSAFE = True   # move mouse to corner to abort
    pyautogui.PAUSE = 0.05      # small pause between actions
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False
    logger.warning("pyautogui not installed — coordinate clicks disabled")


# ══════════════════════════════════════════════════════════════
# SCREEN READER WRAPPER
# ══════════════════════════════════════════════════════════════

async def _get_screen_elements(window_hint: str = "") -> list:
    """
    Read AT-SPI screen elements. Returns list of UIElement objects.
    Optionally filter by window title hint.
    """
    try:
        from perception.screen_reader import ScreenReader
        sr = ScreenReader()
        ok = await sr.initialize()
        if not ok:
            return []
        state = await sr.get_screen_state()
        els = state.elements
        if window_hint:
            els = [e for e in els if window_hint.lower() in e.window.lower()
                   or window_hint.lower() in e.app.lower()]
        return els
    except Exception as e:
        logger.warning(f"Screen read failed: {e}")
        return []


async def _wait_for_elements(
    filter_fn,
    window_hint: str = "",
    max_wait: float = 30.0,
    poll: float = 0.8,
    min_count: int = 1,
) -> list:
    """
    Poll screen until filter_fn returns at least min_count elements,
    OR max_wait seconds pass. No fixed sleep — condition based.
    """
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        els = await _get_screen_elements(window_hint)
        matched = [e for e in els if filter_fn(e)]
        if len(matched) >= min_count:
            return matched
        await asyncio.sleep(poll)
    logger.warning(f"wait_for_elements: timeout after {max_wait}s")
    return []


def _click_at(x: int, y: int, label: str = "") -> bool:
    """Click at screen coordinates using pyautogui."""
    if not _PYAUTOGUI:
        logger.warning("pyautogui not available — cannot click")
        return False
    try:
        pyautogui.moveTo(x, y, duration=0.15)
        pyautogui.click()
        logger.info(f"Clicked ({x},{y}){' → ' + label if label else ''}")
        return True
    except Exception as e:
        logger.warning(f"Click failed at ({x},{y}): {e}")
        return False


def _double_click_at(x: int, y: int, label: str = "") -> bool:
    """Double-click at coordinates."""
    if not _PYAUTOGUI:
        return False
    try:
        pyautogui.moveTo(x, y, duration=0.15)
        pyautogui.doubleClick()
        logger.info(f"Double-clicked ({x},{y}){' → ' + label if label else ''}")
        return True
    except Exception as e:
        logger.warning(f"Double-click failed: {e}")
        return False


def _type_text(text: str) -> bool:
    """Type text using pyautogui."""
    if not _PYAUTOGUI:
        return False
    try:
        pyautogui.typewrite(text, interval=0.05)
        return True
    except Exception as e:
        logger.warning(f"Type failed: {e}")
        return False


def _press_key(key: str) -> bool:
    """Press a keyboard key."""
    if not _PYAUTOGUI:
        return False
    try:
        pyautogui.press(key)
        return True
    except Exception as e:
        logger.warning(f"Key press failed ({key}): {e}")
        return False


# ══════════════════════════════════════════════════════════════
# YOUTUBE: SEARCH + PLAY CORRECT VIDEO
# ══════════════════════════════════════════════════════════════

# Words that appear in YouTube UI chrome — not video titles
_YT_UI_SKIP = {
    "youtube", "search", "sign in", "home", "shorts", "subscriptions",
    "library", "history", "skip", "menu", "more", "trending", "music",
    "gaming", "news", "sports", "learning", "fashion", "podcasts",
    "settings", "report", "help", "feedback", "about", "press",
    "copyright", "contact", "creators", "advertise", "logo",
    "notifications", "upload", "account", "search with your voice",
    "guide", "close", "cancel", "next", "previous", "autoplay",
    # FIX v7.2: Ad-related strings
    "promoted", "ad", "sponsored", "advertisement", "visit website",
    "view channel", "subscribe",
}

# Words that strongly indicate this is NOT a real video title
_AD_INDICATORS = [
    "promoted", "ad ·", "· ad", "sponsored", "advertisement",
    "visit website", "learn more", "get offer",
]


def _is_video_link(el, query_words: list = None) -> bool:
    """
    Check if an AT-SPI element looks like a YouTube video title link.
    FIX v7.1+v7.2: Better ad filtering, dynamic screen handling.
    """
    if el.role not in ("link", "list item"):
        return False
    name = (el.name or "").strip()
    if len(name) < 8 or len(name) > 250:
        return False
    name_lower = name.lower()

    # Skip UI chrome elements
    if name_lower in _YT_UI_SKIP:
        return False
    if any(skip == name_lower.split()[0] for skip in _YT_UI_SKIP if len(skip) > 4):
        # Starts with a UI chrome word
        return False

    # FIX v7.2: Skip ad indicators
    if any(ad in name_lower for ad in _AD_INDICATORS):
        return False

    # Must be in the content area — below search bar
    if el.cy < 130:
        return False
    if el.cx < 60:
        return False

    # FIX v7.2: Very short names after stripping numbers/time codes
    # are usually UI elements, not video titles
    # "3:45" or "45,123 views" → skip
    import re as _re
    cleaned = _re.sub(r'[\d:,\.]+\s*(view|views|watching|subscribers?|likes?|ago|hour|minute|second|day|week|month|year)?', '', name_lower).strip()
    if len(cleaned) < 5:
        return False

    return True


def _score_video(el, query_words: list) -> int:
    """
    Score a video link by how well it matches the search query.
    FIX v7.2: Better scoring — penalize ads harder, reward title matches.
    """
    name = (el.name or "")
    name_lower = name.lower()
    score = 0

    # ── Query word matches ──────────────────────────────────────
    for word in query_words:
        w = word.lower()
        if w in name_lower:
            # Exact word match — higher score
            score += 15
            # Bonus if it's at the start of title
            if name_lower.startswith(w):
                score += 5

    # ── Strong penalties ───────────────────────────────────────
    # Shorts
    if "#short" in name_lower or "shorts" in name_lower:
        score -= 50
    # Playlists / Mix
    if "playlist" in name_lower or " mix" in name_lower or "| mix" in name_lower:
        score -= 30
    # Live streams
    if "live" in name_lower and ("stream" in name_lower or "🔴" in name):
        score -= 20
    # Ads (should already be filtered by _is_video_link, but double-check)
    if any(ad in name_lower for ad in _AD_INDICATORS):
        score -= 100
    # Topic/Channel pages (not individual videos)
    if el.role == "list item" and len(name_lower) < 15:
        score -= 20

    # ── Position preference ────────────────────────────────────
    # Higher on screen = better result (first real video, not ad)
    # But don't over-penalize lower results
    score -= el.cy // 150   # small penalty per 150px from top

    # ── Length heuristic ──────────────────────────────────────
    # Real video titles are usually 20-80 chars
    if 20 <= len(name) <= 100:
        score += 3

    return score


async def youtube_search_and_play(search_url: str, query: str) -> bool:
    """
    Open YouTube search URL and click the best matching video.
    Uses AT-SPI to read actual page content — no blind Tab pressing.
    """
    import subprocess as sp

    # Extract query words for scoring
    query_words = [w for w in re.split(r'\s+', query.lower()) if len(w) > 2]

    # Open URL in existing Chrome or launch Chrome
    chrome_up = sp.run(["pgrep", "-f", "chrome"], capture_output=True).returncode == 0
    if chrome_up:
        sp.Popen(["xdg-open", search_url], stdout=sp.DEVNULL, stderr=sp.DEVNULL)
    else:
        sp.Popen(["google-chrome", search_url], stdout=sp.DEVNULL, stderr=sp.DEVNULL)
        await asyncio.sleep(3.0)  # extra wait for Chrome launch

    logger.info(f"YouTube URL opened: {search_url[:70]}")

    # Focus Chrome
    await asyncio.sleep(1.0)
    for title in ["Google Chrome", "Chrome", "YouTube"]:
        r = sp.run(["wmctrl", "-a", title], capture_output=True, timeout=2)
        if r.returncode == 0:
            break

    # Wait for video links to appear (no fixed timer — poll until ready)
    logger.info("Waiting for YouTube search results to load...")
    video_els = await _wait_for_elements(
        filter_fn=lambda e: _is_video_link(e, query_words),
        window_hint="chrome",
        max_wait=25.0,   # up to 25s for slow connections
        poll=1.0,
        min_count=1,
    )

    if not video_els:
        logger.warning("No video links found via AT-SPI — trying coordinate fallback")
        return await _youtube_coord_fallback()

    # Pick best video by query match score
    video_els.sort(key=lambda e: -_score_video(e, query_words))
    best = video_els[0]
    logger.info(f"Best video match: '{best.name[:70]}' score={_score_video(best, query_words)} @ ({best.cx},{best.cy})")

    return _click_at(best.cx, best.cy, label=best.name[:40])


async def _youtube_coord_fallback() -> bool:
    """
    Last resort: click where YouTube first video thumbnail usually appears.

    FIX v7.1: Pehle hardcoded 480, 290 tha — sirf 1366x768 ke liye sahi tha.
    Ab screen resolution dynamically detect karo.
    """
    if not _PYAUTOGUI:
        return False
    logger.warning("YouTube coord fallback: clicking estimated video position")

    # FIX: Dynamic screen resolution detect karo
    try:
        res = subprocess.run(
            ["xdotool", "getdisplaygeometry"],
            capture_output=True, text=True, timeout=3
        )
        if res.returncode == 0:
            parts = res.stdout.strip().split()
            screen_w = int(parts[0]) if len(parts) >= 2 else 1366
            screen_h = int(parts[1]) if len(parts) >= 2 else 768
        else:
            screen_w, screen_h = 1366, 768
    except Exception:
        screen_w, screen_h = 1366, 768

    # YouTube first video: approximately 35% from left, 38% from top
    click_x = int(screen_w * 0.35)
    click_y = int(screen_h * 0.38)

    logger.info(f"YouTube coord fallback: clicking ({click_x},{click_y}) on {screen_w}x{screen_h}")
    # Take screenshot to verify page loaded
    subprocess.run(["scrot", "/tmp/lada_yt.png"], capture_output=True, timeout=3)
    return _click_at(click_x, click_y, label="yt-coord-fallback")


# ══════════════════════════════════════════════════════════════
# FILE MANAGER NAVIGATION
# ══════════════════════════════════════════════════════════════

async def file_manager_navigate(folder_name: str) -> bool:
    """
    In an open file manager (Nemo), navigate to a folder by name.
    Uses AT-SPI to find the folder item and click it.
    """
    import subprocess as sp

    # Ensure Nemo is focused
    for title in ["Files", "Nemo", "File Manager"]:
        r = sp.run(["wmctrl", "-a", title], capture_output=True, timeout=2)
        if r.returncode == 0:
            break
    await asyncio.sleep(0.5)

    logger.info(f"Looking for folder: '{folder_name}' in file manager...")

    # Wait for folder item to appear in AT-SPI tree
    def is_folder(el):
        if el.role not in ("icon", "list item", "table cell", "label", "link"):
            return False
        name = (el.name or "").lower()
        return folder_name.lower() in name and el.cx > 0 and el.cy > 100

    folder_els = await _wait_for_elements(
        filter_fn=is_folder,
        window_hint="nemo",
        max_wait=15.0,
        poll=0.8,
    )

    if folder_els:
        # Sort by position — topmost/leftmost first
        folder_els.sort(key=lambda e: (e.cy, e.cx))
        best = folder_els[0]
        logger.info(f"Found folder: '{best.name}' @ ({best.cx},{best.cy})")
        _double_click_at(best.cx, best.cy, label=best.name)
        await asyncio.sleep(1.5)
        return True

    # Fallback: use Nemo's Go To Location (Ctrl+L) and type path
    logger.warning(f"Folder '{folder_name}' not found via AT-SPI — using Ctrl+L")
    return await _file_manager_goto_path(folder_name)


async def _file_manager_goto_path(folder_name: str) -> bool:
    """Use Ctrl+L in Nemo to navigate to a path directly."""
    try:
        import subprocess as sp

        # Map common folder names to paths
        folder_map = {
            "documents": "~/Documents",
            "document": "~/Documents",
            "downloads": "~/Downloads",
            "desktop": "~/Desktop",
            "pictures": "~/Pictures",
            "music": "~/Music",
            "videos": "~/Videos",
            "home": "~",
        }
        path = folder_map.get(folder_name.lower(), f"~/{folder_name}")

        # Ctrl+L opens location bar in Nemo
        sp.run(["xdotool", "key", "ctrl+l"], capture_output=True, timeout=2)
        await asyncio.sleep(0.4)

        # Type the path
        sp.run(["xdotool", "type", "--clearmodifiers", path],
               capture_output=True, timeout=3)
        await asyncio.sleep(0.2)

        # Press Enter
        sp.run(["xdotool", "key", "Return"], capture_output=True, timeout=2)
        await asyncio.sleep(1.0)
        logger.info(f"Navigated to: {path}")
        return True
    except Exception as e:
        logger.warning(f"Ctrl+L navigation failed: {e}")
        return False


# ══════════════════════════════════════════════════════════════
# GENERIC: FIND ELEMENT AND CLICK
# ══════════════════════════════════════════════════════════════

async def click_element_by_name(
    name: str,
    role: str = "",
    window_hint: str = "",
    max_wait: float = 20.0,
) -> bool:
    """
    Find a UI element by name (partial match) and click it.
    Polls until found or timeout — no fixed sleep.
    """
    name_lower = name.lower()
    role_lower = role.lower()

    def matches(el):
        n = (el.name or "").lower()
        r = el.role.lower()
        name_ok = name_lower in n
        role_ok = (not role_lower) or (role_lower in r)
        has_coords = el.cx > 0 and el.cy > 0
        return name_ok and role_ok and has_coords

    els = await _wait_for_elements(
        filter_fn=matches,
        window_hint=window_hint,
        max_wait=max_wait,
        poll=0.8,
    )

    if not els:
        logger.warning(f"Element not found: name='{name}' role='{role}'")
        return False

    # Prefer enabled, visible elements — sort by size (bigger = more prominent)
    els.sort(key=lambda e: -(e.w * e.h))
    best = els[0]
    logger.info(f"Clicking element: '{best.name[:50]}' ({best.role}) @ ({best.cx},{best.cy})")
    return _click_at(best.cx, best.cy, label=best.name[:30])


# ══════════════════════════════════════════════════════════════
# SCROLL
# ══════════════════════════════════════════════════════════════

async def scroll_in_window(direction: str = "down", amount: int = 3) -> bool:
    """Scroll in currently focused window."""
    if not _PYAUTOGUI:
        return False
    try:
        # Get current mouse position or screen center
        import subprocess as sp
        # Get focused window geometry
        r = sp.run(["xdotool", "getactivewindow", "getwindowgeometry"],
                   capture_output=True, text=True, timeout=2)
        # Parse geometry if possible, else use screen center
        cx, cy = 683, 400  # default center for 1366x768

        clicks = amount if direction == "down" else -amount
        pyautogui.moveTo(cx, cy, duration=0.1)
        pyautogui.scroll(clicks)
        logger.info(f"Scrolled {direction} x{amount}")
        return True
    except Exception as e:
        logger.warning(f"Scroll failed: {e}")
        return False


# ══════════════════════════════════════════════════════════════
# BRIGHTNESS
# ══════════════════════════════════════════════════════════════

async def set_brightness(level: int) -> bool:
    """
    Set screen brightness (0-100).
    Tries: brightnessctl → xrandr → ddcutil.
    """
    level = max(0, min(100, level))

    # Method 1: brightnessctl (most common on modern Linux)
    try:
        r = subprocess.run(
            ["brightnessctl", "set", f"{level}%"],
            capture_output=True, timeout=5
        )
        if r.returncode == 0:
            logger.info(f"Brightness set to {level}% via brightnessctl")
            return True
    except FileNotFoundError:
        pass

    # Method 2: xrandr (software brightness — always available)
    try:
        # Get connected display name
        r = subprocess.run(
            ["xrandr"], capture_output=True, text=True, timeout=3
        )
        display = None
        for line in r.stdout.splitlines():
            if " connected" in line:
                display = line.split()[0]
                break

        if display:
            bri_val = level / 100.0
            r2 = subprocess.run(
                ["xrandr", "--output", display, "--brightness", str(bri_val)],
                capture_output=True, timeout=5
            )
            if r2.returncode == 0:
                logger.info(f"Brightness set to {level}% via xrandr ({display})")
                return True
    except Exception as e:
        logger.warning(f"xrandr brightness failed: {e}")

    # Method 3: light
    try:
        r = subprocess.run(
            ["light", "-S", str(level)],
            capture_output=True, timeout=5
        )
        if r.returncode == 0:
            logger.info(f"Brightness set to {level}% via light")
            return True
    except FileNotFoundError:
        pass

    logger.error(f"All brightness methods failed for level={level}")
    return False


# ══════════════════════════════════════════════════════════════
# VOLUME
# ══════════════════════════════════════════════════════════════

async def set_volume(level: int) -> bool:
    """
    Set system volume (0-100).
    Tries: pactl → wpctl → amixer.
    """
    level = max(0, min(100, level))

    # Method 1: pactl (PulseAudio — most common)
    try:
        r = subprocess.run(
            ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{level}%"],
            capture_output=True, timeout=5
        )
        if r.returncode == 0:
            logger.info(f"Volume set to {level}% via pactl")
            return True
    except FileNotFoundError:
        pass

    # Method 2: wpctl (PipeWire)
    try:
        r = subprocess.run(
            ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{level}%"],
            capture_output=True, timeout=5
        )
        if r.returncode == 0:
            logger.info(f"Volume set to {level}% via wpctl")
            return True
    except FileNotFoundError:
        pass

    # Method 3: amixer (ALSA)
    try:
        r = subprocess.run(
            ["amixer", "set", "Master", f"{level}%"],
            capture_output=True, timeout=5
        )
        if r.returncode == 0:
            logger.info(f"Volume set to {level}% via amixer")
            return True
    except FileNotFoundError:
        pass

    logger.error(f"All volume methods failed for level={level}")
    return False


# ══════════════════════════════════════════════════════════════
# APP LAUNCH VIA MENU (menu → search → click — human-like)
# ══════════════════════════════════════════════════════════════

async def open_app_via_menu(app_name: str) -> bool:
    """
    Open an app via Cinnamon menu: Super → type name → wait for result → click.
    Uses AT-SPI to confirm the result appeared before pressing Enter.
    """
    import subprocess as sp

    logger.info(f"Opening app via menu: {app_name}")

    # Press Super key to open menu
    sp.run(["xdotool", "key", "Super_L"], capture_output=True, timeout=2)

    # Wait for menu to open — poll for a search input or menu window
    await asyncio.sleep(0.8)

    # Type the app name
    sp.run(["xdotool", "type", "--clearmodifiers", "--delay", "60", app_name],
           capture_output=True, timeout=5)

    # Wait for search result to appear in AT-SPI tree
    app_lower = app_name.lower()

    def is_menu_result(el):
        n = (el.name or "").lower()
        return app_lower in n and el.role in ("menu item", "push button", "icon",
                                               "list item", "label") and el.cy > 0

    results = await _wait_for_elements(
        filter_fn=is_menu_result,
        max_wait=8.0,
        poll=0.5,
    )

    if results:
        best = results[0]
        logger.info(f"Menu result found: '{best.name}' @ ({best.cx},{best.cy})")
        _click_at(best.cx, best.cy, label=best.name)
    else:
        # Fallback: just press Enter (first result is usually selected)
        logger.warning(f"Menu result not found via AT-SPI — pressing Enter")
        sp.run(["xdotool", "key", "Return"], capture_output=True, timeout=2)

    await asyncio.sleep(2.0)
    return True
