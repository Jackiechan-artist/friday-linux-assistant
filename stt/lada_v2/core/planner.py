"""
LADA - Planner Engine v7.0
Linux Mint Cinnamon specific upgrades:
  - Lockscreen command fix
  - Logout/shutdown/restart
  - Better Hindi/Hinglish understanding
  - AI model update
"""

import json
import re
import urllib.request, urllib.error
import asyncio
import urllib.parse
import os
from typing import Optional
from utils.logger import LADALogger
from utils.schema_validator import SchemaValidator
from memory.plan_cache import PlanCache

logger = LADALogger("PLANNER")

# ── API Config ──────────────────────────────────────────────────────────────

def _load_env():
    env_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
        ".env"
    )
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

_load_env()

# [P13] All config from .env — no hardcoding needed
GROQ_API_KEY  = os.environ.get("NVIDIA_KEY_BRAIN2", os.environ.get("NVIDIA_KEY_BRAIN1", ""))
_brain2_base  = os.environ.get("BRAIN2_BASE_URL", "https://integrate.api.nvidia.com/v1")
GROQ_BASE_URL = _brain2_base.rstrip("/") + "/chat/completions"
_brain2_model = os.environ.get("BRAIN2_MODEL", os.environ.get("BRAIN1_MODEL", "openai/gpt-oss-120b"))
GROQ_MODELS   = [_brain2_model]

# OpenRouter fallback — also from .env
OPENROUTER_API_KEY  = os.environ.get("OPENROUTER_KEY", "")
OPENROUTER_MODELS   = [os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")] if os.environ.get("OPENROUTER_KEY") else []
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

# Groq fast fallback from .env
_GROQ_KEY   = os.environ.get("GROQ_KEY", "")
_GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-70b-versatile")

# ── System Prompt ───────────────────────────────────────────────────────────



def _urllib_post(url: str, headers: dict, payload: dict, timeout: float = 30.0):
    """urllib-based POST — replaces httpx (no extra dep needed)."""
    import urllib.request, urllib.error, json as _json
    data = _json.dumps(payload).encode()
    req  = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode()
            result = _json.loads(body)
            result["status_code"] = resp.status
            return result
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            result = _json.loads(body)
            result["status_code"] = e.code
            return result
        except:
            return {"status_code": e.code, "error": str(e)}
    except Exception as e:
        return None

SYSTEM_PROMPT = """
You are the planning brain of LADA (Linux Autonomous Desktop Agent) on Linux Mint Cinnamon.
You understand Hindi, Hinglish, and English equally — including typos and informal spelling.

════════════════════════════════════════════
OUTPUT FORMAT — ABSOLUTE REQUIREMENT
════════════════════════════════════════════

You MUST return ONLY valid JSON. Nothing else.
- No explanation text before or after
- No markdown, no backticks, no prose
- Exactly this structure:

{
  "task":   "short_task_name",
  "intent": "what the user actually wants",
  "steps":  [
    {"action": "ACTION", "value": "VALUE", "method": "METHOD"}
  ]
}

If you cannot produce valid JSON for any reason → return:
{"task":"error","intent":"unknown","steps":[],"error":"reason"}

════════════════════════════════════════════
ANTI-HALLUCINATION RULES
════════════════════════════════════════════

1. NEVER guess application names. If an app is not in the known list below → return:
   {"task":"unknown_app","intent":"open unknown app","steps":[],"error":"unknown_application"}

2. NEVER fabricate file paths. If path is not given → list the directory first.

3. NEVER invent commands. Use ONLY the exact commands listed in this prompt.

4. NEVER substitute key names in keyboard shortcuts. Relay exactly what was given.
   "alt+x" → "alt+x" — NEVER change to "alt+f4" or anything else.

5. If the task is destructive (delete, format, wipe) → add confirmation step.

════════════════════════════════════════════
LINUX MINT CINNAMON — EXACT COMMANDS
════════════════════════════════════════════

KNOWN APPLICATIONS (these plus auto-detected apps):
  google-chrome, chromium, firefox, nemo, gedit, xed, code,
  gnome-terminal, xterm, libreoffice, vlc, gimp, nautilus,
  gnome-calculator, mousepad, kate, evince, rhythmbox

Lock screen:    loginctl lock-session
Logout:         cinnamon-session-quit --logout --no-prompt
Shutdown:       systemctl poweroff
Restart:        systemctl reboot
Sleep/Suspend:  systemctl suspend
Show desktop:   xdotool key super+d

Screenshot:
  Full:      scrot ~/Pictures/screenshot_$(date +%Y%m%d_%H%M%S).png && echo 'Screenshot saved'
  Selection: scrot -s ~/Pictures/screenshot_$(date +%Y%m%d_%H%M%S).png && echo 'Screenshot saved'

Volume:
  Up:    pactl set-sink-volume @DEFAULT_SINK@ +10% && echo 'Volume up'
  Down:  pactl set-sink-volume @DEFAULT_SINK@ -10% && echo 'Volume down'
  Mute:  pactl set-sink-mute @DEFAULT_SINK@ toggle && echo 'Mute toggled'
  Set N%: pactl set-sink-volume @DEFAULT_SINK@ N% && echo 'Volume set'

Brightness:
  brightnessctl set N% && echo 'Brightness set'
  Fallback: xrandr --output $(xrandr | grep ' connected' | head -1 | awk '{print $1}') --brightness 0.N

WiFi:
  Off: nmcli radio wifi off && echo 'WiFi off'
  On:  nmcli radio wifi on && echo 'WiFi on'

Bluetooth:
  Off: rfkill block bluetooth && echo 'Bluetooth off'
  On:  rfkill unblock bluetooth && echo 'Bluetooth on'

System info:
  RAM:     free -h
  Disk:    df -h /
  Battery: upower -i $(upower -e | grep -i bat | head -1) | grep -E 'percentage|state'
  CPU:     top -bn1 | grep "Cpu(s)" | awk '{print "CPU: " $2 "%"}'

File manager: nemo &
Open specific folder: nemo ~/Documents & OR nemo ~/Downloads & OR nemo ~/Desktop &
Terminal: gnome-terminal & OR xterm &
Browser: google-chrome &

════════════════════════════════════════════
PATHS
════════════════════════════════════════════

Desktop=~/Desktop/  Documents=~/Documents/  Downloads=~/Downloads/
Music=~/Music/  Pictures=~/Pictures/  Videos=~/Videos/  Home=~/

════════════════════════════════════════════
STEP ACTIONS REFERENCE
════════════════════════════════════════════

METHOD: system      → run_command, open_app, open_terminal, navigate_folder
                       set_volume, set_brightness, focus_window, close_window
METHOD: browser     → navigate (URL)
METHOD: accessibility → find_and_click (ONLY if no system alternative exists)

════════════════════════════════════════════
RULES
════════════════════════════════════════════

- Info commands (RAM/disk/battery): always capture output with echo or awk
- App launch: ALWAYS use run_command with executable & — eg: "google-chrome &"
- Folder open: ALWAYS use run_command with "nemo ~/FolderName &" — NOT navigate_folder
- "file manager open karo Documents mein le jao" → ONE run_command: "nemo ~/Documents &"
- "terminal open karo" → run_command: "gnome-terminal &"
- Browser search: use navigate method with full URL, not accessibility
- AT-SPI / accessibility: ONLY for clicking buttons inside open apps (AVOID for app/folder launch)
- Multi-step task: each step must be independently executable

Generate the plan for:
"""


class Planner:
    def __init__(self, context_store=None):
        self.context_store    = context_store
        self.schema_validator = SchemaValidator()
        self.conversation_history = []
        self.cache = PlanCache()
        # FIX v7.1: Startup pe broken open_menu plans saaf karo
        purged = self.cache.purge_broken_plans()
        purged += self.cache.purge_generic_plans()
        if purged:
            logger.info(f"Startup: purged {purged} stale/broken cached plans")
        logger.info(f"Plan cache: {self.cache.stats()['cached_plans']} plans")

    def _get_system_context_snippet(self) -> str:
        if not self.context_store:
            return ""
        ctx = self.context_store.get_context("system")
        if not ctx:
            return ""
        return (
            f"\nSYSTEM CONTEXT:\n"
            f"- OS: {ctx.get('os_name', 'Linux Mint')}\n"
            f"- Desktop: {ctx.get('desktop_env', 'Cinnamon')}\n"
            f"- Default browser: {ctx.get('default_browser', 'chromium')}\n"
            f"- File manager: {ctx.get('file_manager', 'nemo')}\n"
            f"- Resolution: {ctx.get('resolution', '1920x1080')}\n"
        )

    async def plan(self, user_command: str) -> Optional[dict]:
        import time as _pt
        _pt0 = _pt.monotonic()
        print(f"[PLANNER] Planning: '{user_command}'")

        cached = self.cache.get(user_command)
        if cached:
            _ms = int((_pt.monotonic()-_pt0)*1000)
            print(f"[PLANNER] ✓ CACHE HIT ({_ms}ms) | task='{cached.get('task')}' | "
                  f"steps={len(cached.get('steps',[]))}")
            return cached

        fallback = self._fallback_plan(user_command)
        if fallback:
            _ms = int((_pt.monotonic()-_pt0)*1000)
            print(f"[PLANNER] ✓ RULE-BASED ({_ms}ms) | task='{fallback.get('task')}'")
            for i, s in enumerate(fallback.get('steps',[]), 1):
                print(f"  Step {i}: action={s.get('action')} | "
                      f"method={s.get('method')} | value='{str(s.get('value',''))[:60]}'")
            return fallback

        if not GROQ_API_KEY.strip():
            print("[PLANNER] ✗ No API key — cannot plan")
            return None

        print(f"[PLANNER] → BRAIN2 (LLM planning)...")
        context_snippet   = self._get_system_context_snippet()
        full_user_message = f"{context_snippet}\nUSER COMMAND: {user_command}"
        messages          = [{"role": "user", "content": full_user_message}]

        for attempt in range(3):
            try:
                _tb = _pt.monotonic()
                response_text = await self._call_openrouter(messages)
                _b2ms = int((_pt.monotonic()-_tb)*1000)
                if not response_text:
                    print(f"[PLANNER] Brain2 attempt {attempt+1}: empty response")
                    continue
                print(f"[←BRAIN2] {_b2ms}ms | raw: {response_text[:120]!r}")

                plan = self._parse_json_response(response_text)
                if not plan:
                    messages.append({"role": "assistant", "content": response_text})
                    messages.append({"role": "user", "content": "Output only valid JSON."})
                    continue

                if self.schema_validator.validate_plan(plan):
                    plan = self._resolve_placeholders(plan, user_command)
                    _ms = int((_pt.monotonic()-_pt0)*1000)
                    print(f"[PLANNER] ✓ AI PLAN ({_ms}ms) | task='{plan.get('task')}'")
                    for i, s in enumerate(plan.get('steps',[]), 1):
                        print(f"  Step {i}: action={s.get('action')} | "
                              f"method={s.get('method')} | value='{str(s.get('value',''))[:60]}'")
                    self.cache.save(user_command, plan)
                    return plan
                else:
                    print(f"[PLANNER] Schema invalid: {plan}")
                    messages.append({"role": "assistant", "content": response_text})
                    messages.append({"role": "user", "content": "Ensure 'task', 'intent', 'steps' fields."})

            except Exception as e:
                print(f"[PLANNER] Attempt {attempt+1} error: {e}")
                await asyncio.sleep(1)

        print("[PLANNER] ✗ All attempts failed")
        return None

    async def _call_openrouter(self, messages):
        if GROQ_API_KEY.strip():
            result = await self._try_groq(messages)
            if result:
                return result
        return await self._try_openrouter(messages)

    async def _try_groq(self, messages):
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        }
        for model in GROQ_MODELS:
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    *messages
                ],
                "temperature": 0.1,
                "max_tokens": 2048,
            }
            try:
                loop = asyncio.get_event_loop()
                data = await loop.run_in_executor(None, lambda: _urllib_post(
                    GROQ_BASE_URL, headers, payload, timeout=30.0
                ))
                if data is None:
                    continue
                if data.get("status_code") == 429:
                    await asyncio.sleep(1)
                    continue
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                if content:
                    return content.strip()
            except Exception as e:
                logger.warning(f"Groq {model}: {e}")
        return None

    async def _try_openrouter(self, messages):
        if not OPENROUTER_API_KEY or not OPENROUTER_MODELS:
            return None
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        }
        for model in OPENROUTER_MODELS:
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    *messages
                ],
                "temperature": 0.1,
                "max_tokens": 2048,
            }
            try:
                loop = asyncio.get_event_loop()
                data = await loop.run_in_executor(None, lambda: _urllib_post(
                    OPENROUTER_BASE_URL, headers, payload, timeout=30.0
                ))
                if data is None:
                    continue
                sc = data.get("status_code", 200)
                if sc in (402, 429):
                    continue
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                if content:
                    return content.strip()
            except Exception as e:
                logger.warning(f"OpenRouter {model}: {e}")
        return None

    def _parse_json_response(self, text):
        if not text:
            return None
        text = re.sub(r"```(?:json)?\s*", "", text)
        text = re.sub(r"```\s*$", "", text)
        text = text.strip()
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start == -1 or end == 0:
            return None
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            return None

    def _resolve_placeholders(self, plan, user_command):
        plan_str = json.dumps(plan)
        search_m = re.search(r"search (?:for |about )?['\"]?(.+?)['\"]?$", user_command, re.IGNORECASE)
        if search_m:
            plan_str = plan_str.replace("SEARCH_TERM", search_m.group(1).strip())
        open_m = re.search(r"open\s+['\"]?(.+?)['\"]?$", user_command, re.IGNORECASE)
        if open_m:
            plan_str = plan_str.replace("APP_NAME", open_m.group(1).strip())
        return json.loads(plan_str)

    def _fallback_plan(self, user_command: str) -> Optional[dict]:
        logger.info("Using fallback (rule-based)...")
        cmd = user_command.lower().strip()

        # ── open_app: FORMAT (from friday_brain.py _rule_route) ─────────────
        # Format: "open_app: exec1||exec2||exec3" — tries each in order
        if cmd.startswith("open_app:"):
            import shutil as _sh
            raw = user_command[len("open_app:"):].strip()
            candidates = [c.strip() for c in raw.split("||")]
            chosen = None
            for c in candidates:
                if _sh.which(c):
                    chosen = c
                    break
            if not chosen:
                chosen = candidates[0]  # Try first anyway
            print(f"[PLANNER] open_app: candidates={candidates} → chosen='{chosen}'")
            return {
                "task": "open_app",
                "intent": f"Open app: {chosen}",
                "steps": [{"action": "run_command",
                           "value": f"{chosen} &",
                           "method": "system"}]
            }

        # ── BLUETOOTH (Brain 1 "bluetooth: on/off" format) ──────────────────
        if cmd.startswith("bluetooth:"):
            action = cmd[len("bluetooth:"):].strip().lower()
            is_off = action in ("off", "band", "bandh", "hatao", "disable")
            cmd_str = "rfkill block bluetooth && echo 'Bluetooth off'" if is_off else "rfkill unblock bluetooth && echo 'Bluetooth on'"
            return {"task": f"bluetooth_{'off' if is_off else 'on'}", "intent": f"Bluetooth {'off' if is_off else 'on'}",
                    "steps": [{"action": "run_command", "value": cmd_str, "method": "system"}]}

        # ── WIFI (Brain 1 "wifi: on/off" format) ────────────────────────────
        if cmd.startswith("wifi:"):
            action = cmd[len("wifi:"):].strip().lower()
            is_off = action in ("off", "band", "bandh", "hatao")
            cmd_str = "nmcli radio wifi off && echo 'WiFi off'" if is_off else "nmcli radio wifi on && echo 'WiFi on'"
            return {"task": f"wifi_{'off' if is_off else 'on'}", "intent": f"WiFi {'off' if is_off else 'on'}",
                    "steps": [{"action": "run_command", "value": cmd_str, "method": "system"}]}

        # ── BRIGHTNESS (Brain 1 "brightness: N" format) ──────────────────────
        if cmd.startswith("brightness:"):
            level = cmd[len("brightness:"):].strip()
            try:
                level = int(re.sub(r'[^0-9]', '', level))
                level = max(0, min(100, level))
            except:
                level = 70
            return {"task": "set_brightness", "intent": f"Set brightness to {level}%",
                    "steps": [{"action": "set_brightness", "value": str(level), "method": "system"}]}

        # ── VOLUME (Brain 1 "volume: up/down/mute/N" format) ─────────────────
        if cmd.startswith("volume:"):
            action = cmd[len("volume:"):].strip().lower()
            if action == "up":
                cmd_str = "pactl set-sink-volume @DEFAULT_SINK@ +10% && echo 'Volume increased'"
                task = "volume_up"
            elif action == "down":
                cmd_str = "pactl set-sink-volume @DEFAULT_SINK@ -10% && echo 'Volume decreased'"
                task = "volume_down"
            elif action == "mute":
                cmd_str = "pactl set-sink-mute @DEFAULT_SINK@ toggle && echo 'Mute toggled'"
                task = "mute_volume"
            else:
                try:
                    pct = int(re.sub(r'[^0-9]', '', action))
                    cmd_str = f"pactl set-sink-volume @DEFAULT_SINK@ {pct}% && echo 'Volume set to {pct}%'"
                    task = f"set_volume_{pct}"
                except:
                    cmd_str = "pactl set-sink-volume @DEFAULT_SINK@ +10% && echo 'Volume increased'"
                    task = "volume_up"
            return {"task": task, "intent": f"Volume: {action}",
                    "steps": [{"action": "run_command", "value": cmd_str, "method": "system"}]}

        # ── KEYBOARD SHORTCUTS (Brain 1 "key: combo" or "key: combo xN") ──────
        if cmd.startswith("key:"):
            key_part = cmd[len("key:"):].strip()
            # Check for repeat count: "alt+x x3" → key=alt+x, times=3
            times_match = re.search(r'\s+x(\d+)$', key_part)
            if times_match:
                times = int(times_match.group(1))
                key_combo = key_part[:times_match.start()].strip()
            else:
                times = 1
                key_combo = key_part
            # Normalize key names for xdotool
            key_combo = key_combo.replace('window+', 'super+').replace('win+', 'super+')
            key_combo = key_combo.replace('control+', 'ctrl+').replace(' ', '')
            # Build xdotool command
            if times == 1:
                xdo_cmd = f"xdotool key {key_combo} && echo 'Pressed {key_combo}'"
            else:
                parts = " && ".join([f"xdotool key {key_combo}"] * times)
                xdo_cmd = f"{parts} && echo 'Pressed {key_combo} {times} times'"
            return {"task": "key_press", "intent": f"Press {key_combo} x{times}",
                    "steps": [{"action": "run_command", "value": xdo_cmd, "method": "system"}]}

        # ── KEYBOARD SHORTCUTS (regex fallback for Brain 1 free-form) ────────
        shortcut_match = re.search(
            r'((?:alt|ctrl|control|shift|super|win(?:dow)?)\s*[\+\-]\s*\w+(?:\s*[\+\-]\s*\w+)*)',
            cmd, re.IGNORECASE
        )
        if shortcut_match:
            key_combo = shortcut_match.group(1)
            key_combo = re.sub(r'\b(?:window|win)\b', 'super', key_combo, flags=re.IGNORECASE)
            key_combo = re.sub(r'\bcontrol\b', 'ctrl', key_combo, flags=re.IGNORECASE)
            key_combo = re.sub(r'\s+', '', key_combo).lower()
            times_match = re.search(r'(\d+)\s*(?:times?|baar|bar)', cmd)
            times = int(times_match.group(1)) if times_match else 1
            if times <= 1:
                xdo_cmd = f"xdotool key {key_combo} && echo 'Pressed {key_combo}'"
            else:
                keys = " && ".join([f"xdotool key {key_combo}"] * times)
                xdo_cmd = f"{keys} && echo 'Pressed {key_combo} {times} times'"
            return {"task": "key_press", "intent": f"Press {key_combo} x{times}",
                    "steps": [{"action": "run_command", "value": xdo_cmd, "method": "system"}]}

        # ── LOCKSCREEN ───────────────────────────────────────────────────────
        if any(k in cmd for k in ["lock", "lockscreen", "lock screen", "screen lock",
                                   "lock kar", "screen band", "lock karo",
                                   "lockscreen par", "lock par le", "lock pe le"]):
            return {
                "task": "lock_screen",
                "intent": "Lock the screen",
                "steps": [{"action": "run_command",
                           "value": "loginctl lock-session && echo 'Screen locked'",
                           "method": "system"}]
            }

        # ── LOGOUT ──────────────────────────────────────────────────────────
        if any(k in cmd for k in ["logout", "log out", "sign out", "signout"]):
            return {
                "task": "logout",
                "intent": "Logout from session",
                "steps": [{"action": "run_command",
                           "value": "cinnamon-session-quit --logout --no-prompt",
                           "method": "system"}]
            }

        # ── SHUTDOWN ─────────────────────────────────────────────────────────
        if any(k in cmd for k in ["shutdown", "shut down", "band karo system", "computer band",
                                   "power off", "poweroff"]):
            return {
                "task": "shutdown",
                "intent": "Shutdown the system",
                "steps": [{"action": "run_command",
                           "value": "systemctl poweroff",
                           "method": "system"}]
            }

        # ── RESTART ──────────────────────────────────────────────────────────
        if any(k in cmd for k in ["restart", "reboot", "dobara chalao"]):
            return {
                "task": "reboot",
                "intent": "Restart the system",
                "steps": [{"action": "run_command",
                           "value": "systemctl reboot",
                           "method": "system"}]
            }

        # ── SLEEP/SUSPEND ────────────────────────────────────────────────────
        if any(k in cmd for k in ["sleep", "suspend", "hibernate", "so jao"]):
            return {
                "task": "sleep",
                "intent": "Suspend system",
                "steps": [{"action": "run_command",
                           "value": "systemctl suspend",
                           "method": "system"}]
            }

        # ── SYSTEM INFO ──────────────────────────────────────────────────────
        if any(k in cmd for k in ["battery", "batter", "charge"]):
            return {"task": "check_battery", "intent": "Check battery",
                    "steps": [{"action": "run_command",
                               "value": "upower -i $(upower -e | grep -i bat | head -1) | grep -E 'percentage|state|time to'",
                               "method": "system"}]}

        if any(k in cmd for k in ["ram", "memory", "mem"]):
            return {"task": "check_ram", "intent": "Check RAM",
                    "steps": [{"action": "run_command",
                               "value": "free -h | awk 'NR==2{print \"Total: \"$2\" | Used: \"$3\" | Free: \"$4}'",
                               "method": "system"}]}

        if any(k in cmd for k in ["disk", "storage", "space"]):
            return {"task": "check_disk", "intent": "Check disk space",
                    "steps": [{"action": "run_command",
                               "value": "df -h / | awk 'NR==2{print \"Disk - Total: \"$2\" Used: \"$3\" Free: \"$4\" (\"$5\" used)\"}'",
                               "method": "system"}]}

        if any(k in cmd for k in ["cpu", "processor"]):
            return {"task": "check_cpu", "intent": "Check CPU usage",
                    "steps": [{"action": "run_command",
                               "value": "top -bn1 | grep 'Cpu(s)' | awk '{print \"CPU: \" $2 \"% user, \" $4 \"% system\"}'",
                               "method": "system"}]}

        if "screenshot" in cmd:
            return {"task": "screenshot", "intent": "Take screenshot",
                    "steps": [{"action": "run_command",
                               "value": "scrot ~/screenshot_$(date +%Y%m%d_%H%M%S).png && echo 'Screenshot saved to home folder'",
                               "method": "system"}]}

        # ── VOLUME ───────────────────────────────────────────────────────────
        vol = re.search(r"(?:volume|awaaz|sound)\s*(?:ko\s*|to\s*)?(\d+)\s*%?", cmd)
        if vol:
            pct = vol.group(1)
            return {"task": "set_volume", "intent": f"Set volume to {pct}%",
                    "steps": [{"action": "run_command",
                               "value": f"pactl set-sink-volume @DEFAULT_SINK@ {pct}% && echo 'Volume set to {pct}%'",
                               "method": "system"}]}

        if any(k in cmd for k in ["volume up", "awaaz badhao", "louder"]):
            return {"task": "volume_up", "intent": "Increase volume",
                    "steps": [{"action": "run_command",
                               "value": "pactl set-sink-volume @DEFAULT_SINK@ +10% && echo 'Volume increased'",
                               "method": "system"}]}

        if any(k in cmd for k in ["volume down", "awaaz kam", "quieter"]):
            return {"task": "volume_down", "intent": "Decrease volume",
                    "steps": [{"action": "run_command",
                               "value": "pactl set-sink-volume @DEFAULT_SINK@ -10% && echo 'Volume decreased'",
                               "method": "system"}]}

        if any(k in cmd for k in ["mute", "awaaz band", "volume band"]):
            return {"task": "mute_volume", "intent": "Mute/unmute",
                    "steps": [{"action": "run_command",
                               "value": "pactl set-sink-mute @DEFAULT_SINK@ toggle && echo 'Mute toggled'",
                               "method": "system"}]}

        # ── BRIGHTNESS ───────────────────────────────────────────────────────
        bri = re.search(r"(?:brightness|ujala|roshan|brightnees)\s*(?:ko\s*)?(\d+)\s*%?", cmd)
        if bri:
            return {"task": "set_brightness", "intent": f"Set brightness to {bri.group(1)}%",
                    "steps": [{"action": "set_brightness", "value": bri.group(1), "method": "system"}]}

        # ── BLUETOOTH ────────────────────────────────────────────────────────
        if "bluetooth" in cmd:
            is_off = any(k in cmd for k in ["band", "off", "hatao", "bandh"])
            cmd_str = "rfkill block bluetooth && echo 'Bluetooth off'" if is_off else "rfkill unblock bluetooth && echo 'Bluetooth on'"
            return {"task": f"bluetooth_{'off' if is_off else 'on'}", "intent": f"Bluetooth {'off' if is_off else 'on'}",
                    "steps": [{"action": "run_command", "value": cmd_str, "method": "system"}]}

        # ── WIFI ─────────────────────────────────────────────────────────────
        if any(k in cmd for k in ["wifi", "wi-fi", "internet", "network"]):
            is_off = any(k in cmd for k in ["band", "off", "hatao", "bandh"])
            cmd_str = "nmcli radio wifi off && echo 'WiFi off'" if is_off else "nmcli radio wifi on && echo 'WiFi on'"
            return {"task": f"wifi_{'off' if is_off else 'on'}", "intent": f"WiFi {'off' if is_off else 'on'}",
                    "steps": [{"action": "run_command", "value": cmd_str, "method": "system"}]}

        # ── YOUTUBE ──────────────────────────────────────────────────────────
        # Brain 1 "youtube: <query>" format — clean aur direct
        if cmd.startswith("youtube:"):
            query = cmd[len("youtube:"):].strip()
            if not query:
                query = "best hindi songs 2024"
            url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(query)}"
            return {"task": "youtube_play", "intent": f"YouTube: {query}",
                    "steps": [{"action": "youtube_navigate_and_play", "value": url, "method": "browser"}]}

        is_yt = any(k in cmd for k in ["youtube", "ytube", "song play", "video play",
                                        "song chala", "video chala", "music play"])
        is_play = any(k in cmd for k in ["play", "chala", "sunao", "bajao"])

        if is_yt or (is_play and any(k in cmd for k in ["song", "music", "video", "gana"])):
            # FIX v7.2: Better noise removal — preserve artist names
            noise_phrases = [
                "youtube pe", "youtube par", "youtube mein", "youtube search karo",
                "chrome par", "chrome mein", "chrome pe", "browser pe",
                "open karo aur", "search karo", "search kar",
                "play karo", "play kar", "chala do", "chalao",
                "ke gaane", "ke gane", "ki songs", "ka gana", "ki song", "ke songs",
            ]
            noise_words = [
                "youtube", "song", "music", "gana", "video",
                "chala", "sunao", "bajao", "lao",
                "chrome par", "chrome mein",
                "ka", "ki", "ke", "wala", "wale",
            ]
            query = cmd
            for n in sorted(noise_phrases, key=len, reverse=True):
                query = query.replace(n, " ")
            for n in sorted(noise_words, key=len, reverse=True):
                query = re.sub(r'\b' + re.escape(n) + r'\b', ' ', query)
            query = re.sub(r"\s+", " ", query).strip().strip('.,!?-:')
            if not query or len(query) < 2:
                query = "best hindi songs 2024"
            url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(query)}"
            return {"task": "youtube_play", "intent": f"YouTube: {query}",
                    "steps": [{"action": "youtube_navigate_and_play", "value": url, "method": "browser"}]}

        # ── BROWSER SEARCH ───────────────────────────────────────────────────
        is_search = any(k in cmd for k in ["search", "dhundo", "google karo"])
        if is_search and any(k in cmd for k in ["chrome", "browser", "firefox"]):
            query = cmd
            for n in ["chrome par", "chrome mein", "chrome", "browser", "firefox",
                      "search karo", "search kar", "google karo"]:
                query = query.replace(n, " ")
            query = re.sub(r"\s+", " ", query).strip()
            if query:
                return {"task": "browser_search", "intent": f"Search: {query}",
                        "steps": [{"action": "navigate",
                                  "value": f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}",
                                  "method": "browser"}]}

        # ── CLOSE COMMANDS ───────────────────────────────────────────────────
        is_close = any(k in cmd for k in ["close", "band karo", "band kar", "bnd karo",
                                           "kill karo", "quit karo", "bandh"])
        if is_close:
            for app, kill_cmd in [
                ("terminal", "wmctrl -c Terminal || pkill gnome-terminal || pkill xterm"),
                ("chrome",   "wmctrl -c Chrome || pkill google-chrome"),
                ("firefox",  "wmctrl -c Firefox || pkill firefox"),
                ("nemo",     "wmctrl -c Nemo || pkill nemo"),
                ("vlc",      "pkill vlc"),
            ]:
                if app in cmd:
                    return {"task": f"close_{app}", "intent": f"Close {app}",
                            "steps": [{"action": "run_command", "value": kill_cmd, "method": "system"}]}

        # ── FOLDER NAVIGATION — Direct path open (most reliable) ────────────
        # FIX v7.1: AT-SPI folder click unreliable — direct nemo <path> 100% reliable
        # "document folder kholo", "Documents mein jao", "file manager open karo documents mein le jao"
        FOLDER_MAP = {
            "document":  "~/Documents",
            "documents": "~/Documents",
            "download":  "~/Downloads",
            "downloads": "~/Downloads",
            "desktop":   "~/Desktop",
            "picture":   "~/Pictures",
            "pictures":  "~/Pictures",
            "music":     "~/Music",
            "video":     "~/Videos",
            "videos":    "~/Videos",
            "home":      "~",
        }
        is_folder_nav = any(k in cmd for k in list(FOLDER_MAP.keys()) + [
            "folder kholo", "folder open", "folder mein jao", "folder mein le",
            "mein le chalo", "le jao", "folder jana"
        ])
        if is_folder_nav:
            target_path = None
            for name, path in FOLDER_MAP.items():
                if name in cmd:
                    target_path = path
                    break
            if target_path:
                nemo_cmd = f"nemo {target_path} & sleep 1 && echo 'Opened {target_path}'"
                return {
                    "task": "open_folder",
                    "intent": f"Open {target_path} in file manager",
                    "steps": [{"action": "run_command", "value": nemo_cmd, "method": "system"}]
                }

        # ── APP OPEN — Direct launch (reliable) ─────────────────────────────
        # FIX v7.1: Pehle 3-step menu approach (Super→search→Enter) thi — unreliable.
        # Ab direct run_command se launch karo — 100% reliable, no AT-SPI needed.
        if any(k in cmd for k in ["file manager", "files", "nemo", "file managr"]):
            return {
                "task": "open_file_manager",
                "intent": "Open file manager",
                "steps": [{"action": "run_command",
                           "value": "nemo & sleep 1 && echo 'File manager opened'",
                           "method": "system"}]
            }

        if any(k in cmd for k in ["terminal", "bash", "command line", "cmd"]):
            return {
                "task": "open_terminal",
                "intent": "Open terminal",
                "steps": [{"action": "run_command",
                           "value": "gnome-terminal & sleep 1 && echo 'Terminal opened'",
                           "method": "system"}]
            }

        # FIX v7.2: Text editor
        if any(k in cmd for k in ["text editor", "editor", "gedit", "xed", "mousepad",
                                   "notepad", "likhna", "text file"]):
            import shutil as _shutil
            # Find installed text editor
            for editor in ["xed", "gedit", "mousepad", "kate", "pluma", "leafpad"]:
                if _shutil.which(editor):
                    return {
                        "task": "open_text_editor",
                        "intent": "Open text editor",
                        "steps": [{"action": "run_command",
                                   "value": f"{editor} & sleep 1 && echo 'Text editor opened'",
                                   "method": "system"}]
                    }
            # Fallback: try gedit anyway
            return {
                "task": "open_text_editor",
                "intent": "Open text editor",
                "steps": [{"action": "run_command",
                           "value": "gedit & sleep 1 && echo 'Text editor opened'",
                           "method": "system"}]
            }

        # FIX v7.2: Calculator
        if any(k in cmd for k in ["calculator", "calc", "ganana", "calculation"]):
            import shutil as _shutil
            for calc in ["gnome-calculator", "kcalc", "galculator", "xcalc"]:
                if _shutil.which(calc):
                    return {
                        "task": "open_calculator",
                        "intent": "Open calculator",
                        "steps": [{"action": "run_command",
                                   "value": f"{calc} & sleep 1 && echo 'Calculator opened'",
                                   "method": "system"}]
                    }

        is_just_open = any(k in cmd for k in [
            "chrome kholo", "chrome open", "chrome chalao", "browser open",
            "browser kholo", "chrome chalo", "open chrome", "open google chrome",
            "open browser", "google chrome open", "browser chalao"
        ])
        if is_just_open or (any(k in cmd for k in ["chrome", "browser", "google chrome"])
                            and not is_search and not is_yt):
            return {
                "task": "open_browser",
                "intent": "Open Chrome",
                "steps": [{"action": "run_command",
                           "value": "google-chrome & sleep 1 && echo 'Chrome launched'",
                           "method": "system"}]
            }

        # ── FILE OPS ─────────────────────────────────────────────────────────
        has_write  = any(k in cmd for k in ["likho", "write", "mein likh"])
        has_save   = any(k in cmd for k in ["save karo", "documents", "folder main"])
        has_delete = any(k in cmd for k in ["delete karo", "hatao", "mita do"])

        if has_write and has_save and has_delete:
            return {
                "task": "edit_save_delete",
                "intent": "Write, save copy, delete original",
                "steps": [{"action": "run_command",
                           "value": "echo 'welcome' >> ~/Desktop/hello.txt && cp ~/Desktop/hello.txt ~/Documents/welcome.txt && rm ~/Desktop/hello.txt && echo 'Done'",
                           "method": "system"}]
            }

        # ── NEW TAB ──────────────────────────────────────────────────────────
        if any(k in cmd for k in ["new tab", "naya tab", "tab kholo"]):
            return {"task": "new_tab", "intent": "Open new browser tab",
                    "steps": [{"action": "run_command",
                               "value": "xdotool search --onlyvisible --class google-chrome windowactivate key ctrl+t",
                               "method": "system"}]}

        logger.warning(f"No fallback rule for: '{user_command}'")

        # FIX v7.2: "open X using command: Y &" — from _discover_app()
        if "using command:" in user_command:
            import re as _re
            m = _re.search(r'using command:\s*(.+)$', user_command)
            if m:
                cmd = m.group(1).strip()
                return {
                    "task": "open_app_discovered",
                    "intent": f"Open discovered app: {cmd}",
                    "steps": [{"action": "run_command", "value": cmd, "method": "system"}]
                }

        return None
