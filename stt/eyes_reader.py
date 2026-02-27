"""
FRIDAY Eyes v2.0 — Smart Screen Reader
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Problem 2 Fix: Screen ko samajhna — blindly kaam karna band

Kya karta hai:
  - Active window kaunsa hai — title, app name
  - Browser mein kaunsa URL/page khuli hai  
  - Open windows ki list (wmctrl se)
  - Running apps
  - Focused element (AT-SPI se)
"""

import subprocess, os, re, time

_last_screen_ctx  = ""
_last_screen_time = 0.0
_SCREEN_REFRESH   = float(os.environ.get("SCREEN_REFRESH", "2"))
_SCREEN_MAX_CHARS = int(os.environ.get("SCREEN_MAX_CHARS", "800"))


def _run(cmd, timeout=2) -> str:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except:
        return ""


def _active_window() -> dict:
    info = {"title": "", "app": ""}
    wid = _run("xdotool getactivewindow 2>/dev/null")
    if wid:
        title = _run(f"xdotool getwindowname {wid.strip()} 2>/dev/null")
        info["title"] = title[:100] if title else ""
        wm = _run(f"xprop -id {wid.strip()} WM_CLASS 2>/dev/null")
        if wm:
            parts = re.findall(r'"([^"]+)"', wm)
            if parts:
                info["app"] = parts[-1].lower()
    return info


def _browser_page() -> str:
    out = _run("wmctrl -l 2>/dev/null")
    for line in out.split("\n"):
        low = line.lower()
        if "google chrome" in low or "chromium" in low:
            parts = line.split(None, 3)
            if len(parts) >= 4:
                t = re.sub(r'\s*[-—]\s*(Google Chrome|Chromium).*', '', parts[3], flags=re.I)
                if t and len(t) > 3:
                    return f"Chrome page: {t[:80]}"
        if "firefox" in low:
            parts = line.split(None, 3)
            if len(parts) >= 4:
                t = re.sub(r'\s*[-—]\s*Mozilla Firefox.*', '', parts[3], flags=re.I)
                if t and len(t) > 3:
                    return f"Firefox page: {t[:80]}"
    return ""


def _running_apps() -> list:
    APPS = {
        "google-chrome": "Chrome", "chromium": "Chrome", "firefox": "Firefox",
        "code": "VSCode", "gedit": "TextEditor", "xed": "TextEditor",
        "nemo": "FileManager", "vlc": "VLC", "spotify": "Spotify",
        "gnome-terminal": "Terminal", "xterm": "Terminal",
        "gimp": "GIMP", "discord": "Discord", "telegram-desktop": "Telegram",
    }
    running = []
    for proc, name in APPS.items():
        r = subprocess.run(["pgrep", "-x", proc], capture_output=True)
        if r.returncode == 0 and name not in running:
            running.append(name)
    return running[:6]


def _at_spi_focused() -> str:
    try:
        import pyatspi
        reg = pyatspi.Registry
        for i in range(min(reg.getAppCount(), 15)):
            try:
                app = reg.getApp(i)
                if not app or not app.name or app.name.lower() in ('', 'nemo-desktop', 'cinnamon'):
                    continue
                for j in range(min(app.childCount, 4)):
                    try:
                        child = app[j]
                        if child and child.name:
                            ss = child.getState()
                            if ss.contains(pyatspi.STATE_FOCUSED):
                                return f"{app.name}>{child.getRoleName()}:{child.name[:40]}"
                    except:
                        continue
            except:
                continue
    except:
        pass
    return ""


def get_screen_context() -> str:
    global _last_screen_ctx, _last_screen_time
    now = time.monotonic()
    if now - _last_screen_time < _SCREEN_REFRESH and _last_screen_ctx:
        return _last_screen_ctx

    parts = []
    
    active = _active_window()
    if active["title"]:
        app = f" ({active['app']})" if active["app"] else ""
        parts.append(f"Active:{active['title'][:70]}{app}")
    
    browser = _browser_page()
    if browser:
        parts.append(browser)
    
    apps = _running_apps()
    if apps:
        parts.append("Apps:" + ",".join(apps))
    
    focused = _at_spi_focused()
    if focused:
        parts.append(f"Focus:{focused[:70]}")

    ctx = " | ".join(parts)
    if len(ctx) > _SCREEN_MAX_CHARS:
        ctx = ctx[:_SCREEN_MAX_CHARS]

    _last_screen_ctx  = ctx
    _last_screen_time = now
    return ctx or "Desktop"


def scan_ui() -> str:
    """Backward compat."""
    return get_screen_context()


if __name__ == "__main__":
    print(get_screen_context())
