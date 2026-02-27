"""
LADA - Capability Detector
System ko pata hona chahiye kya available hai.
Runtime pe detect karo — hardcode mat karo.
"""

import asyncio
import shutil
import subprocess
import os
from typing import Optional
from utils.logger import LADALogger

logger = LADALogger("CAPABILITY")


class Capabilities:
    """Snapshot of what this system can do."""

    def __init__(self):
        # ── Desktop ──
        self.desktop_env: str = "unknown"
        self.window_manager: str = "unknown"
        self.display_server: str = "unknown"    # x11 / wayland

        # ── Default apps ──
        self.default_browser: Optional[str] = None
        self.file_manager: Optional[str] = None
        self.terminal: Optional[str] = None
        self.text_editor: Optional[str] = None

        # ── Control tools ──
        self.has_wmctrl: bool = False
        self.has_xdotool: bool = False
        self.has_xdpyinfo: bool = False
        self.has_scrot: bool = False
        self.has_notify_send: bool = False

        # ── Audio ──
        self.audio_backend: Optional[str] = None   # pulse / pipewire / alsa

        # ── Vision / Automation ──
        self.has_pyatspi: bool = False
        self.has_playwright: bool = False
        self.has_opencv: bool = False
        self.has_pyautogui: bool = False
        self.has_pytesseract: bool = False

        # ── Screen ──
        self.resolution: str = "1920x1080"
        self.dpi: str = "96x96"

        # ── Python extras ──
        self.has_psutil: bool = False

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    def method_available(self, method: str) -> bool:
        """Check if a given action method is available."""
        if method == "accessibility":
            return self.has_pyatspi
        if method == "browser":
            return self.has_playwright
        if method == "cv":
            return self.has_opencv
        if method == "system":
            return True     # always possible via subprocess
        return True

    def best_method_for(self, action: str) -> str:
        """Return best available method for an action."""
        browser_actions = {"navigate", "find_and_click", "click_button",
                           "type_text", "search", "scroll", "wait_for_element"}
        if action in browser_actions and self.has_playwright:
            return "browser"
        if self.has_pyatspi:
            return "accessibility"
        return "system"


class CapabilityDetector:
    """
    Detects and caches what this machine can actually do.
    Called once at boot. Results stored in ContextStore.
    """

    async def detect(self) -> Capabilities:
        cap = Capabilities()

        self._detect_display(cap)
        self._detect_desktop(cap)
        self._detect_tools(cap)
        self._detect_audio(cap)
        self._detect_default_apps(cap)
        self._detect_screen(cap)
        await self._detect_python_libs(cap)

        self._log_summary(cap)
        return cap

    # ── Detectors ──────────────────────────────────────────

    def _detect_display(self, cap: Capabilities):
        """X11 or Wayland?"""
        wayland_display = os.environ.get("WAYLAND_DISPLAY", "")
        x_display = os.environ.get("DISPLAY", "")
        if wayland_display:
            cap.display_server = "wayland"
        elif x_display:
            cap.display_server = "x11"
        else:
            cap.display_server = "headless"

    def _detect_desktop(self, cap: Capabilities):
        """Desktop environment and window manager."""
        cap.desktop_env = (
            os.environ.get("XDG_CURRENT_DESKTOP", "")
            or os.environ.get("DESKTOP_SESSION", "")
            or "unknown"
        )
        # WM via wmctrl
        if shutil.which("wmctrl"):
            result = subprocess.run(
                ["wmctrl", "-m"],
                capture_output=True, text=True, timeout=3
            )
            for line in result.stdout.split("\n"):
                if "Name:" in line:
                    cap.window_manager = line.split(":", 1)[1].strip()
                    break

    def _detect_tools(self, cap: Capabilities):
        """Check which CLI tools are installed."""
        cap.has_wmctrl      = bool(shutil.which("wmctrl"))
        cap.has_xdotool     = bool(shutil.which("xdotool"))
        cap.has_xdpyinfo    = bool(shutil.which("xdpyinfo"))
        cap.has_scrot       = bool(shutil.which("scrot"))
        cap.has_notify_send = bool(shutil.which("notify-send"))

    def _detect_audio(self, cap: Capabilities):
        """Detect audio backend."""
        if shutil.which("wpctl"):
            cap.audio_backend = "pipewire"
        elif shutil.which("pactl"):
            cap.audio_backend = "pulse"
        elif shutil.which("amixer"):
            cap.audio_backend = "alsa"
        else:
            cap.audio_backend = None

    def _detect_default_apps(self, cap: Capabilities):
        """Find default browser, file manager, terminal, text editor."""
        browsers = [
            "chromium-browser", "chromium", "google-chrome",
            "google-chrome-stable", "firefox", "brave-browser"
        ]
        for b in browsers:
            if shutil.which(b):
                cap.default_browser = b
                break

        file_managers = ["nemo", "nautilus", "thunar", "pcmanfm", "dolphin"]
        for fm in file_managers:
            if shutil.which(fm):
                cap.file_manager = fm
                break

        terminals = [
            "gnome-terminal", "xterm", "xfce4-terminal",
            "konsole", "tilix", "mate-terminal"
        ]
        for t in terminals:
            if shutil.which(t):
                cap.terminal = t
                break

        editors = ["gedit", "mousepad", "xed", "kate", "leafpad", "nano"]
        for e in editors:
            if shutil.which(e):
                cap.text_editor = e
                break

    def _detect_screen(self, cap: Capabilities):
        """Screen resolution and DPI via xdpyinfo."""
        if not shutil.which("xdpyinfo"):
            return
        try:
            result = subprocess.run(
                ["xdpyinfo"],
                capture_output=True, text=True, timeout=3
            )
            for line in result.stdout.split("\n"):
                if "dimensions:" in line:
                    for part in line.split():
                        if "x" in part and part[0].isdigit():
                            cap.resolution = part
                            break
                if "resolution:" in line:
                    for part in line.split():
                        if "x" in part and part[0].isdigit():
                            cap.dpi = part
                            break
        except Exception:
            pass

    async def _detect_python_libs(self, cap: Capabilities):
        """Check which Python automation libs are installed."""
        # pyatspi
        try:
            import pyatspi
            cap.has_pyatspi = True
        except ImportError:
            cap.has_pyatspi = False

        # playwright
        try:
            from playwright.async_api import async_playwright
            cap.has_playwright = True
        except ImportError:
            cap.has_playwright = False

        # opencv
        try:
            import cv2
            cap.has_opencv = True
        except ImportError:
            cap.has_opencv = False

        # pyautogui
        try:
            import pyautogui
            cap.has_pyautogui = True
        except ImportError:
            cap.has_pyautogui = False

        # pytesseract
        try:
            import pytesseract
            cap.has_pytesseract = True
        except ImportError:
            cap.has_pytesseract = False

        # psutil
        try:
            import psutil
            cap.has_psutil = True
        except ImportError:
            cap.has_psutil = False

    def _log_summary(self, cap: Capabilities):
        logger.info(
            f"System: {cap.desktop_env} | {cap.display_server} | "
            f"WM={cap.window_manager}"
        )
        logger.info(
            f"Apps: browser={cap.default_browser} | "
            f"files={cap.file_manager} | term={cap.terminal}"
        )
        logger.info(
            f"Tools: wmctrl={cap.has_wmctrl} xdotool={cap.has_xdotool} "
            f"scrot={cap.has_scrot}"
        )
        logger.info(
            f"Automation: pyatspi={cap.has_pyatspi} playwright={cap.has_playwright} "
            f"cv2={cap.has_opencv} pyautogui={cap.has_pyautogui}"
        )
        logger.info(
            f"Audio: {cap.audio_backend} | Screen: {cap.resolution}"
        )
