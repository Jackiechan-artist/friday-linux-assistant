"""
LADA - System Actions
System-level control via subprocess commands.
Handles: volume, brightness, window management, app launching, shell commands.
"""

import asyncio
import subprocess
import shutil
from typing import Optional
from utils.logger import LADALogger

logger = LADALogger("SYSTEM_ACTIONS")

# Dangerous commands that should never be executed
BLOCKED_COMMANDS = [
    "rm -rf /",
    "mkfs",
    "dd if=/dev/zero",
    ":(){ :|:& };:",  # fork bomb
    "chmod -R 777 /",
    "> /dev/sda",
]


class SystemActions:
    """
    System-level executor using subprocess.
    Never uses pixel coordinates.
    """

    def __init__(self):
        self._audio_backend = self._detect_audio()

    def _detect_audio(self) -> str:
        """Detect which audio system is available."""
        if shutil.which("wpctl"):
            return "pipewire"
        elif shutil.which("pactl"):
            return "pulse"
        elif shutil.which("amixer"):
            return "alsa"
        return "none"

    async def execute(self, step: dict) -> bool:
        """
        Execute a system-level action.
        Returns True on success, False on failure.
        """
        action = step.get("action", "")
        value  = step.get("value", "")

        # FIX v7.2: "open X using command: Y &" format — _discover_app se aata hai
        if action == "run_command" and not value and "using command:" in step.get("value", ""):
            pass  # handled below
        if action == "run_command" and "using command:" in value:
            # Extract: "open text editor using command: xed &"
            import re as _re
            m = _re.search(r'using command:\s*(.+)$', value)
            if m:
                cmd = m.group(1).strip()
                return await self._run_command(cmd)
            return await self._run_command(value)

        logger.info(f"System execute: action={action} value={value}")

        # Route to handlers
        if action == "open_menu":
            return await self._open_menu(value)
        elif action == "open_app":
            return await self._open_app(value)
        elif action == "open_terminal":
            return await self._open_terminal()
        elif action == "navigate_folder":
            return await self._navigate_folder(value)
        elif action == "set_volume":
            return await self._set_volume(value)
        elif action == "set_brightness":
            return await self._set_brightness(value)
        elif action == "focus_window":
            return await self._focus_window(value)
        elif action == "close_window":
            return await self._close_window(value)
        elif action == "verify_window":
            return await self._verify_window(value)
        elif action == "run_command":
            return await self._run_command(value)
        elif action == "youtube_play":
            return await self._youtube_play(value)
        elif action == "youtube_skip_ad":
            return await self._youtube_skip_ad()
        else:
            logger.warning(f"No system handler for action: {action}")
            return False

    # ── Handlers ───────────────────────────────────────────

    async def _open_menu(self, menu_type: str = "start") -> bool:
        """
        Open application launcher menu.
        Linux Mint Cinnamon: Super key opens menu.
        """
        try:
            # Method 1: xdotool to press Super key
            result = subprocess.run(
                ["xdotool", "key", "Super_L"],
                capture_output=True,
                timeout=2,
            )
            if result.returncode == 0:
                await asyncio.sleep(1.0)  # wait for menu animation
                logger.debug("Menu opened via Super key")
                return True

            # Method 2: xdg-open or keyboard shortcut
            result = subprocess.run(
                ["xdotool", "key", "ctrl+F2"],
                capture_output=True,
                timeout=2,
            )
            if result.returncode == 0:
                await asyncio.sleep(0.5)
                return True

            return False

        except Exception as e:
            logger.debug(f"open_menu error: {e}")
            return False

    async def _open_app(self, app_name: str) -> bool:
        """Launch an application by executable name.
        
        FIX v7.1: Better known-app mapping + smarter process verification.
        """
        # FIX: Known app name mappings — natural language → executable
        APP_MAP = {
            "google chrome browser": ["google-chrome", "chromium-browser", "chromium"],
            "google chrome":         ["google-chrome", "chromium-browser", "chromium"],
            "chrome":                ["google-chrome", "chromium-browser", "chromium"],
            "browser":               ["google-chrome", "firefox", "chromium"],
            "terminal":              ["gnome-terminal", "xterm", "xfce4-terminal"],
            "file manager":          ["nemo", "nautilus", "thunar"],
            "file manager nemo":     ["nemo"],
            "nemo":                  ["nemo"],
            "vs code":               ["code"],
            "vs code editor":        ["code"],
            "code":                  ["code"],
            "vlc":                   ["vlc"],
            "gimp":                  ["gimp"],
            "text editor":           ["xed", "gedit", "mousepad", "kate", "pluma"],
            "editor":                ["xed", "gedit", "mousepad", "kate"],
            "gedit":                 ["gedit"],
            "xed":                   ["xed"],
            "mousepad":              ["mousepad"],
            "calculator":            ["gnome-calculator", "kcalc", "galculator", "xcalc"],
            "gnome-calculator":      ["gnome-calculator"],
            "libreoffice":           ["libreoffice"],
        }

        app_lower = app_name.lower().strip()
        candidates = APP_MAP.get(app_lower, [app_name])

        for candidate in candidates:
            try:
                subprocess.Popen(
                    [candidate],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                await asyncio.sleep(2.5)

                # Verify process started
                base_name = candidate.split("/")[-1].split("-")[0]
                check = subprocess.run(
                    ["pgrep", "-f", base_name],
                    capture_output=True, timeout=3,
                )
                if check.returncode == 0:
                    logger.info(f"App launched and verified: {candidate}")
                    return True

                # Second check via wmctrl
                wm = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True, timeout=3)
                if wm.returncode == 0 and base_name.lower() in wm.stdout.lower():
                    logger.info(f"App window found: {candidate}")
                    return True

                # App launched but process check inconclusive — assume success
                logger.warning(f"App launched but not verified: {candidate} — assuming OK")
                return True

            except FileNotFoundError:
                logger.debug(f"App not found: {candidate}")
                continue
            except Exception as e:
                logger.debug(f"open_app error ({candidate}): {e}")
                continue

        logger.error(f"All candidates failed for: {app_name}")
        return False

    async def _navigate_folder(self, folder_name: str) -> bool:
        """Navigate to a folder in open file manager using smart_actions AT-SPI."""
        try:
            from actions.smart_actions import file_manager_navigate
            # Give Nemo time to fully open first
            await asyncio.sleep(2.5)
            return await file_manager_navigate(folder_name)
        except Exception as e:
            logger.debug(f"navigate_folder error: {e}")
            return False

    async def _open_terminal(self) -> bool:
        """Open terminal emulator."""
        terminals = [
            "gnome-terminal",
            "xfce4-terminal",
            "konsole",
            "xterm",
            "mate-terminal",
        ]
        for term in terminals:
            if shutil.which(term):
                try:
                    subprocess.Popen(
                        [term],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    await asyncio.sleep(1.0)
                    return True
                except Exception:
                    continue
        return False

    async def _set_volume(self, volume: str) -> bool:
        """Set system volume using smart_actions (tries pactl → wpctl → amixer)."""
        try:
            from actions.smart_actions import set_volume
            return await set_volume(int(volume))
        except Exception as e:
            logger.debug(f"set_volume error: {e}")
            return False

    async def _set_brightness(self, brightness: str) -> bool:
        """Set screen brightness using smart_actions (tries brightnessctl → xrandr → light)."""
        try:
            from actions.smart_actions import set_brightness
            return await set_brightness(int(brightness))
        except Exception as e:
            logger.debug(f"set_brightness error: {e}")
            return False

    async def _focus_window(self, window_title: str) -> bool:
        """Focus window by title."""
        try:
            result = subprocess.run(
                ["wmctrl", "-a", window_title],
                capture_output=True,
                timeout=3,
            )
            return result.returncode == 0
        except Exception as e:
            logger.debug(f"focus_window error: {e}")
            return False

    async def _close_window(self, window_title: str) -> bool:
        """Close window by title."""
        try:
            result = subprocess.run(
                ["wmctrl", "-c", window_title],
                capture_output=True,
                timeout=3,
            )
            return result.returncode == 0
        except Exception as e:
            logger.debug(f"close_window error: {e}")
            return False

    async def _verify_window(self, window_title: str) -> bool:
        """Check if a window with given title exists."""
        try:
            result = subprocess.run(
                ["wmctrl", "-l"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if result.returncode == 0:
                # Case-insensitive partial match
                title_lower = window_title.lower()
                for line in result.stdout.split("\n"):
                    if title_lower in line.lower():
                        logger.debug(f"Window found: {window_title}")
                        return True
            return False
        except Exception as e:
            logger.debug(f"verify_window error: {e}")
            return False

    async def _run_command(self, command: str) -> bool:
        """
        Run shell command. Smart routing:
        - App launch commands (nemo &, gnome-terminal &, google-chrome &) → non-blocking
        - Info commands → block and print output
        - File ops (cp, mv, rm, echo) → block, show result

        FIX v7.1: Background command detection improved — app launches reliably async.
        """
        for blocked in BLOCKED_COMMANDS:
            if blocked in command:
                logger.error(f"BLOCKED dangerous command: {command}")
                return False

        try:
            cmd_stripped = command.strip()

            # FIX v7.1: Known app launchers — hamesha background mein
            APP_LAUNCHER_KEYWORDS = [
                "google-chrome", "chromium", "firefox", "nemo", "nautilus",
                "gnome-terminal", "xterm", "xfce4-terminal", "code", "gimp",
                "vlc", "libreoffice", "gedit", "mousepad", "xed", "kate",
                "pluma", "gnome-calculator", "kcalc", "galculator", "xcalc",
                "rhythmbox", "evince", "okular"
            ]
            is_app_launch = any(kw in cmd_stripped for kw in APP_LAUNCHER_KEYWORDS)

            is_background = (
                cmd_stripped.endswith(" &") or
                cmd_stripped.startswith("xdg-open") or
                cmd_stripped.startswith("xdg-email") or
                is_app_launch  # FIX: app launches always background
            )

            if is_background:
                clean_cmd = cmd_stripped.rstrip("&").strip()
                # "nemo & sleep 1 && echo ..." → sleep + echo bhi chalane chahiye
                # Sirf pure app name ke liye Popen karo
                # Agar '&&' hai toh shell=True ke saath chala
                if "&&" in clean_cmd or ";" in clean_cmd:
                    subprocess.Popen(
                        clean_cmd, shell=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    subprocess.Popen(
                        clean_cmd, shell=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                await asyncio.sleep(1.5)
                logger.info(f"Background command launched: {clean_cmd[:60]}")
                return True

            # Blocking command — run and capture output
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
            )

            out = result.stdout.strip()
            err = result.stderr.strip()

            if out:
                print(f"\n[LADA] {out}")
            if err and result.returncode != 0:
                print(f"\n[LADA] Error: {err[:200]}")

            return result.returncode == 0 or bool(out)

        except subprocess.TimeoutExpired:
            logger.warning(f"Command timeout: {command}")
            return False
        except Exception as e:
            logger.debug(f"run_command error: {e}")
            return False

    async def _youtube_play(self, query: str) -> bool:
        """Search YouTube and play first video."""
        from actions.youtube_actions import YouTubeActions
        yt = YouTubeActions()
        return await yt.search_and_play(query)

    async def _youtube_skip_ad(self) -> bool:
        """Skip current YouTube ad."""
        from actions.youtube_actions import YouTubeActions
        yt = YouTubeActions()
        return await yt.skip_ad()
