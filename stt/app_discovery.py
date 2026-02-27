"""
FRIDAY App Discovery v1.0
[Problem 9 Fix]

Linux mein lakho applications hain. Har ek ka ek .desktop file hota hai.
Yeh module un sab ko scan karke ek searchable database banata hai.

Pehle se define karne ki zarurat nahi — dynamically detect hoga.

Usage:
    from app_discovery import find_app, get_installed_apps
    
    cmd = find_app("gedit")           # "gedit"
    cmd = find_app("text editor")     # "xed" (installed editor)
    cmd = find_app("Firefox")         # "firefox"
    
    apps = get_installed_apps()  # {name: exec_cmd, ...}
"""

import glob
import os
import re
import shutil
import subprocess
import time
import threading

# Cache — har baar scan nahi karna
_apps_cache: dict = {}
_cache_time: float = 0.0
_cache_lock = threading.Lock()
_CACHE_TTL = 300.0  # 5 min tak cache valid

def _clean_exec(exec_str: str) -> str:
    """
    .desktop Exec field ko clean karo.
    '%U', '%f', etc. remove karo — sirf executable chahiye.
    """
    # Remove field codes
    cleaned = re.sub(r'%[a-zA-Z]', '', exec_str).strip()
    # Remove wrapper scripts that just pass env vars
    cleaned = re.sub(r'^env\s+', '', cleaned)
    # Take only first token (executable)
    parts = cleaned.split()
    if parts:
        return parts[0]
    return exec_str

def _scan_desktop_files() -> dict:
    """
    Sare .desktop files scan karo aur app database banao.
    Returns: {normalized_name: {'name': str, 'exec': str, 'exec_full': str}, ...}
    """
    apps = {}
    search_paths = [
        '/usr/share/applications/*.desktop',
        '/usr/local/share/applications/*.desktop',
        os.path.expanduser('~/.local/share/applications/*.desktop'),
        '/var/lib/snapd/desktop/applications/*.desktop',
        '/var/lib/flatpak/exports/share/applications/*.desktop',
    ]
    
    for pattern in search_paths:
        for path in glob.glob(pattern):
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                
                # Quick parse without configparser (faster)
                section = ''
                entry = {}
                for line in content.splitlines():
                    line = line.strip()
                    if line.startswith('['):
                        section = line[1:line.rfind(']')]
                        continue
                    if section == 'Desktop Entry' and '=' in line:
                        k, _, v = line.partition('=')
                        entry[k.strip()] = v.strip()
                
                # Skip if not a proper application
                if entry.get('Type') != 'Application':
                    continue
                if entry.get('NoDisplay', 'false').lower() == 'true':
                    continue
                
                name     = entry.get('Name', '').strip()
                exec_raw = entry.get('Exec', '').strip()
                
                if not name or not exec_raw:
                    continue
                
                exec_clean = _clean_exec(exec_raw)
                
                # Only store if executable exists
                # (or if it looks like a path-based command)
                if not shutil.which(exec_clean) and not exec_clean.startswith('/'):
                    # Try without any path prefix
                    base = os.path.basename(exec_clean)
                    if not shutil.which(base):
                        continue  # App not actually installed
                    exec_clean = base
                
                apps[name.lower()] = {
                    'name':      name,
                    'exec':      exec_clean,
                    'exec_full': exec_raw,
                }
                
                # Also store GenericName (e.g., "Web Browser", "Text Editor")
                generic = entry.get('GenericName', '').strip()
                if generic and generic.lower() not in apps:
                    apps[generic.lower()] = apps[name.lower()].copy()
                    
            except Exception:
                pass
    
    return apps

def get_installed_apps(force_refresh: bool = False) -> dict:
    """
    Installed apps ki cached dictionary return karo.
    force_refresh=True karoge toh naya scan hoga.
    """
    global _apps_cache, _cache_time
    with _cache_lock:
        now = time.monotonic()
        if not force_refresh and _apps_cache and (now - _cache_time < _CACHE_TTL):
            return _apps_cache
        _apps_cache = _scan_desktop_files()
        _cache_time = now
        print(f"[AppDiscovery] {len(_apps_cache)} apps scanned")
        return _apps_cache

def find_app(query: str) -> str | None:
    """
    Query se matching app ka executable dhundo.
    
    Examples:
        find_app("firefox")       → "firefox"
        find_app("text editor")   → "xed" (ya installed editor)
        find_app("gedit")         → "gedit"
        find_app("web browser")   → "firefox" ya "google-chrome"
    
    Returns: executable string ya None agar nahi mila
    """
    q = query.lower().strip()
    apps = get_installed_apps()
    
    # 1. Exact match
    if q in apps:
        return apps[q]['exec']
    
    # 2. Starts-with match
    for name, info in apps.items():
        if name.startswith(q) or q.startswith(name):
            return info['exec']
    
    # 3. Word overlap match
    q_words = set(q.split())
    best_match = None
    best_score = 0
    for name, info in apps.items():
        name_words = set(name.split())
        overlap = len(q_words & name_words)
        if overlap > best_score:
            best_score = overlap
            best_match = info['exec']
    
    if best_score > 0:
        return best_match
    
    # 4. Executable name match (e.g., user said "gedit" but name is "Text Editor")
    for name, info in apps.items():
        exec_base = os.path.basename(info['exec']).lower()
        if q == exec_base or q in exec_base:
            return info['exec']
    
    # 5. shutil.which — agar user ne exact executable naam bola
    if shutil.which(q):
        return q
    
    return None

def get_open_windows() -> list:
    """
    Abhi kaun si windows open hain — wmctrl se.
    Returns: [{'id': str, 'desktop': str, 'title': str}, ...]
    [P5/P10 Fix] Screen awareness ke liye.
    """
    try:
        result = subprocess.run(
            ['wmctrl', '-l'],
            capture_output=True, text=True, timeout=3
        )
        windows = []
        for line in result.stdout.splitlines():
            parts = line.split(None, 3)
            if len(parts) >= 4:
                windows.append({
                    'id': parts[0],
                    'desktop': parts[1],
                    'title': parts[3].strip()
                })
        return windows
    except:
        return []

def get_running_processes() -> list:
    """Abi kaun se processes chal rahe hain."""
    try:
        result = subprocess.run(
            ['ps', '-eo', 'comm', '--no-headers'],
            capture_output=True, text=True, timeout=3
        )
        procs = list(set(line.strip() for line in result.stdout.splitlines() if line.strip()))
        return sorted(procs)
    except:
        return []

# Startup pe background mein scan karo
def _background_scan():
    time.sleep(3)  # Startup ke baad thoda wait
    get_installed_apps()

_scan_thread = threading.Thread(target=_background_scan, daemon=True)
_scan_thread.start()
