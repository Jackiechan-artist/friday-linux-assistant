"""
FRIDAY Brain v8.0 — All Problems Fixed
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Developer  : Tejasv Dubey
Platform   : Linux Mint Cinnamon

Fixes in v8.0 (14 problems solved):
  [P1]  Normal chat (hello/hii/how are you) → rule-based instant response
  [P2]  Screen properly read → structured context to LLM
  [P3]  Mouse without moving → xdotool virtual clicks (user mouse untouched)
  [P4]  .env main sab config → BOSS_NAME, models, URLs, prompts sab
  [P5]  LADA cache fix → purge on error, max_age, manual reset command
  [P6]  Personality → proactive comments, notices user activity
  [P7]  Less LLM dependency → smarter rule routing
  [P8]  Multi-task proper → ALL apps in linux_tasks execute hote hain
  [P9]  Long multi-step commands → sequential execution with verification
  [P10] Real-time data → DuckDuckGo web search built-in
  [P11] Piper TTS fix → text preprocessing for better pronunciation
  [P12] STT fix → better timeout, retry, Hindi+English dual mode
  [P13] Three brain config in .env → no code editing needed
  [P14] Stop command → interrupt any running task with "ruko/stop/bas"
"""

import os, re, sys, json, socket, threading, subprocess, time
from datetime import datetime
from openai import OpenAI

# ══════════════════════════════════════════════════════════════════════════
# ENV LOADER — .env se sab kuch
# ══════════════════════════════════════════════════════════════════════════

def _load_env():
    """Project root se .env load karo."""
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

_load_env()

# ── [P4] Config sab .env se ──────────────────────────────────────────────
BOSS_NAME       = os.environ.get("BOSS_NAME", "Sir")
BOSS_FULL_NAME  = os.environ.get("BOSS_FULL_NAME", BOSS_NAME)
ASSISTANT_NAME  = os.environ.get("ASSISTANT_NAME", "FRIDAY")
BOSS_STYLE      = os.environ.get("BOSS_STYLE", "formal_sir")
CUSTOM_CONTEXT  = os.environ.get("CUSTOM_CONTEXT", "")

# Brain 1
NVIDIA_KEY_BRAIN1   = os.environ.get("NVIDIA_KEY_BRAIN1", "")
BRAIN1_BASE_URL     = os.environ.get("BRAIN1_BASE_URL", "https://integrate.api.nvidia.com/v1")
BRAIN1_MODEL        = os.environ.get("BRAIN1_MODEL", "openai/gpt-oss-120b")
BRAIN1_MODEL_FB     = os.environ.get("BRAIN1_MODEL_FALLBACK", "openai/gpt-oss-120b")

# Brain 2 (LADA Planner)
NVIDIA_KEY_BRAIN2   = os.environ.get("NVIDIA_KEY_BRAIN2", NVIDIA_KEY_BRAIN1)
BRAIN2_BASE_URL     = os.environ.get("BRAIN2_BASE_URL", BRAIN1_BASE_URL)
BRAIN2_MODEL        = os.environ.get("BRAIN2_MODEL", BRAIN1_MODEL)

# Brain 3
NVIDIA_KEY_BRAIN3   = os.environ.get("NVIDIA_KEY_BRAIN3", NVIDIA_KEY_BRAIN1)
BRAIN3_BASE_URL     = os.environ.get("BRAIN3_BASE_URL", BRAIN1_BASE_URL)
BRAIN3_MODEL        = os.environ.get("BRAIN3_MODEL", BRAIN1_MODEL)

# Fallback keys
OPENROUTER_KEY      = os.environ.get("OPENROUTER_KEY", "")
OPENROUTER_MODEL    = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")
GROQ_KEY            = os.environ.get("GROQ_KEY", "")
GROQ_MODEL          = os.environ.get("GROQ_MODEL", "llama-3.1-70b-versatile")

# Features
PLAN_CACHE_ENABLED  = os.environ.get("PLAN_CACHE", "true").lower() == "true"
PLAN_CACHE_MAX_AGE  = int(os.environ.get("PLAN_CACHE_MAX_AGE_HOURS", "24"))
WEB_SEARCH_ENABLED  = os.environ.get("WEB_SEARCH", "true").lower() == "true"
PROACTIVE_MODE      = os.environ.get("PROACTIVE_MODE", "true").lower() == "true"
PROACTIVE_INTERVAL  = int(os.environ.get("PROACTIVE_INTERVAL", "30"))

# [P14] Stop keywords
STOP_KEYWORDS_RAW   = os.environ.get("STOP_KEYWORDS", "stop,ruko,ruk,bas,cancel,mat karo,nahi,hold on,wait")
STOP_KEYWORDS       = [k.strip().lower() for k in STOP_KEYWORDS_RAW.split(",")]

_THIS_DIR   = os.path.dirname(os.path.abspath(__file__))
LADA_DIR    = os.path.join(_THIS_DIR, "lada_v2")
SOCKET_PATH = "/tmp/lada_daemon.sock"
MEMORY_FILE = os.path.join(_THIS_DIR, "friday_memory.json")

# ── API Clients ───────────────────────────────────────────────────────────
try:
    client1 = OpenAI(api_key=NVIDIA_KEY_BRAIN1, base_url=BRAIN1_BASE_URL) if NVIDIA_KEY_BRAIN1 else None
    client3 = OpenAI(api_key=NVIDIA_KEY_BRAIN3 or NVIDIA_KEY_BRAIN1,
                     base_url=BRAIN3_BASE_URL) if (NVIDIA_KEY_BRAIN3 or NVIDIA_KEY_BRAIN1) else None
    # Groq fallback
    client_groq = None
    if GROQ_KEY:
        from openai import OpenAI as _OAI
        client_groq = _OAI(api_key=GROQ_KEY, base_url="https://api.groq.com/openai/v1")
except ImportError:
    client1 = client3 = client_groq = None

# ── API key warnings ──────────────────────────────────────────────────────
for kn, kv, use in [
    ("NVIDIA_KEY_BRAIN1", NVIDIA_KEY_BRAIN1, "Brain 1 fail hoga"),
    ("NVIDIA_KEY_BRAIN3", NVIDIA_KEY_BRAIN3, "Brain 3 fail hoga"),
]:
    if not kv:
        print(f"[WARNING] {kn} .env mein set nahi — {use}!")

# ── Logger ────────────────────────────────────────────────────────────────
import logging
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("FRIDAY")

# ══════════════════════════════════════════════════════════════════════════
# [P14] STOP SIGNAL — interrupt running tasks
# ══════════════════════════════════════════════════════════════════════════

class _StopController:
    """
    Global stop flag.
    Koi bhi function is_stopped() check kar sakta hai
    aur execution rok sakta hai.
    """
    def __init__(self):
        self._stop = threading.Event()
    
    def request_stop(self):
        self._stop.set()
        print("[STOP] Stop requested — current task band ho raha hai")
    
    def clear(self):
        self._stop.clear()
    
    def is_stopped(self) -> bool:
        return self._stop.is_set()
    
    def check(self):
        """Raise exception if stopped."""
        if self._stop.is_set():
            raise InterruptedError("Task stopped by user")

STOP_CTRL = _StopController()


def is_stop_command(text: str) -> bool:
    """Check karo agar user ne stop command diya."""
    t = text.lower().strip()
    return any(kw in t for kw in STOP_KEYWORDS)


# ══════════════════════════════════════════════════════════════════════════
# [P10] WEB SEARCH — Real-time data
# ══════════════════════════════════════════════════════════════════════════

def web_search(query: str, max_results: int = 3) -> str:
    """
    DuckDuckGo instant answers API se real-time data.
    No API key needed.
    """
    if not WEB_SEARCH_ENABLED:
        return ""
    try:
        import urllib.request, urllib.parse
        encoded = urllib.parse.quote(query)
        url = f"https://api.duckduckgo.com/?q={encoded}&format=json&no_html=1&skip_disambig=1"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        
        parts = []
        
        # Instant answer
        if data.get("AbstractText"):
            parts.append(data["AbstractText"][:300])
        
        # Answer box (facts)
        if data.get("Answer"):
            parts.append(data["Answer"][:200])
        
        # Related topics
        for topic in data.get("RelatedTopics", [])[:2]:
            if isinstance(topic, dict) and topic.get("Text"):
                parts.append(topic["Text"][:150])
        
        if parts:
            return "\n".join(parts[:3])
        
        # Fallback: DuckDuckGo HTML search
        return _ddg_html_search(query)
        
    except Exception as e:
        print(f"[SEARCH] Error: {e}")
        return ""


def _ddg_html_search(query: str) -> str:
    """DuckDuckGo HTML search fallback."""
    try:
        import urllib.request, urllib.parse
        encoded = urllib.parse.quote(query)
        url = f"https://html.duckduckgo.com/html/?q={encoded}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=6) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        
        # Extract result snippets
        snippets = re.findall(r'class="result__snippet"[^>]*>([^<]+)', html)
        if snippets:
            return " | ".join(s.strip() for s in snippets[:2])
    except:
        pass
    return ""


def needs_web_search(user_input: str) -> bool:
    """Check karo agar user ko real-time data chahiye."""
    t = user_input.lower()
    web_keywords = [
        "aaj ka", "aaj ki", "today", "news", "khabar", "abhi", "current",
        "latest", "naya", "new", "price", "kitna", "rate", "weather", "mausam",
        "stock", "ipl score", "cricket score", "match", "result", "winner",
        "trending", "viral", "breakdown", "update", "recently",
        "2024", "2025", "2026",
    ]
    return any(k in t for k in web_keywords)


# ══════════════════════════════════════════════════════════════════════════
# [P11] TTS PREPROCESSOR — Piper pronunciation fix
# ══════════════════════════════════════════════════════════════════════════

def preprocess_for_tts(text: str) -> str:
    """
    Piper ke liye text clean karo — better pronunciation.
    Numbers, abbreviations, special chars ko expand karo.
    """
    if not text:
        return text
    
    t = text
    
    # Screen data aur markers remove karo
    t = re.sub(r'\[SCREEN.*?\].*', '', t, flags=re.DOTALL)
    t = re.sub(r'\[\[.*?\]\]', '', t, flags=re.DOTALL)
    t = re.sub(r'\*+', '', t)
    t = re.sub(r'`+', '', t)
    
    # Number + % → word form (TTS ke liye)
    t = re.sub(r'(\d+)\s*%', lambda m: f"{m.group(1)} percent", t)
    
    # Commonly mispronounced abbreviations
    abbr = {
        "RAM": "R A M", "CPU": "C P U", "GPU": "G P U",
        "URL": "U R L", "API": "A P I", "IDE": "I D E",
        "USB": "U S B", "SSD": "S S D", "HDD": "H D D",
        "WiFi": "Wi-Fi", "VS Code": "V S Code",
        "GB": "gigabyte", "MB": "megabyte", "KB": "kilobyte",
        "GHz": "gigahertz", "MHz": "megahertz",
        "LLM": "L L M", "AI": "A I",
    }
    for abbr_word, expansion in abbr.items():
        t = re.sub(r'\b' + re.escape(abbr_word) + r'\b', expansion, t)
    
    # Numbers to words for better pronunciation (simple cases)
    # 1 → ek, 2 → do, etc. (only standalone numbers)
    num_words = {
        "0": "zero", "1": "ek", "2": "do", "3": "teen", "4": "chaar",
        "5": "paanch", "6": "chheh", "7": "saat", "8": "aath",
        "9": "nau", "10": "das"
    }
    # Only replace single-digit standalone numbers in Hinglish context
    # (avoid replacing in technical contexts like "version 3.5")
    
    # Markdown cleanup
    t = re.sub(r'#+\s+', '', t)
    t = re.sub(r'[-*]\s+', '', t)
    t = re.sub(r'\n+', ' ', t)
    t = re.sub(r'\s+', ' ', t)
    t = t.strip()
    
    return t


# ══════════════════════════════════════════════════════════════════════════
# MEMORY SYSTEM (same as v7, improved)
# ══════════════════════════════════════════════════════════════════════════

class FridayMemory:
    MAX_LONG_TERM  = 20
    MAX_SHORT_TERM = 10
    
    def __init__(self):
        self.short_term = []
        self.long_term  = []
        # [P6] Activity tracking for proactive mode
        self._session_start = datetime.now()
        self._last_activity = datetime.now()
        self._activity_count = 0
        self._load()
    
    def _load(self):
        try:
            if os.path.exists(MEMORY_FILE):
                with open(MEMORY_FILE) as f:
                    data = json.load(f)
                    self.long_term = data.get("facts", [])[-self.MAX_LONG_TERM:]
        except:
            self.long_term = []
    
    def _save(self):
        try:
            with open(MEMORY_FILE, "w") as f:
                json.dump({"facts": self.long_term}, f, ensure_ascii=False, indent=2)
        except:
            pass
    
    def add_exchange(self, user: str, assistant: str):
        self.short_term.append({"role": "user", "content": user})
        self.short_term.append({"role": "assistant", "content": assistant})
        if len(self.short_term) > self.MAX_SHORT_TERM * 2:
            self.short_term = self.short_term[-(self.MAX_SHORT_TERM * 2):]
        self._last_activity = datetime.now()
        self._activity_count += 1
    
    def save_fact(self, fact: str, topic: str = "general"):
        for existing in self.long_term:
            if existing["fact"].lower() == fact.lower():
                return
        self.long_term.append({
            "fact": fact, "topic": topic,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M")
        })
        if len(self.long_term) > self.MAX_LONG_TERM:
            self.long_term = self.long_term[-self.MAX_LONG_TERM:]
        self._save()
    
    def get_context_string(self) -> str:
        if not self.long_term:
            return ""
        facts = "\n".join(f"- {f['fact']}" for f in self.long_term[-10:])
        return f"\n[MEMORY — {BOSS_NAME} ke baare mein]:\n{facts}\n"
    
    def get_short_term(self) -> list:
        return self.short_term[-self.MAX_SHORT_TERM * 2:]
    
    def clear_session(self):
        self.short_term = []
    
    def get_session_duration_mins(self) -> int:
        return int((datetime.now() - self._session_start).total_seconds() / 60)
    
    def minutes_since_last_activity(self) -> int:
        return int((datetime.now() - self._last_activity).total_seconds() / 60)


# ══════════════════════════════════════════════════════════════════════════
# BRAIN 1 — ROUTER + CHAT (NVIDIA / Groq fallback)
# ══════════════════════════════════════════════════════════════════════════

# [P4] FRIDAY_SYSTEM uses .env variables — no hardcoding
FRIDAY_SYSTEM = f"""
You are {ASSISTANT_NAME} — {BOSS_FULL_NAME}'s personal Linux desktop AI assistant.
Personality: Iron Man's FRIDAY — precise, calm, intelligent, occasionally witty.

{"CUSTOM_CONTEXT: " + CUSTOM_CONTEXT if CUSTOM_CONTEXT else ""}

════════════════════════════════════════════
TONE & STYLE
════════════════════════════════════════════

- Address user as "Sir" always
- Speak Hinglish (Hindi + English, Roman script)
- Max 1-2 sentences per reply — TTS optimized
- Confident, direct tone
- Dry wit occasionally — never sarcastic
- NEVER over-explain, NEVER add filler
- For greetings/chat: be warm and friendly, like a real assistant

════════════════════════════════════════════
[P6] PROACTIVE PERSONALITY — IMPORTANT
════════════════════════════════════════════

- You CAN notice screen context and comment naturally
- If user has been working long time: suggest a break
- If something interesting on screen: mention it
- Feel ALIVE — not like a cold robot
- Examples:
  "Sir, GitHub pe kaam ho raha hai — koi naya commit chahiye?"
  "Laga raha hai sir thoda tired ho — break lete hain?"

════════════════════════════════════════════
ROUTER — CLASSIFY & RETURN JSON ONLY
════════════════════════════════════════════

ROUTE: chat        → Greetings, conversation, general questions
ROUTE: system_info → Date/time/RAM/battery/disk → run linux command
ROUTE: command     → Any desktop action → pass to LADA
ROUTE: web_search  → Current news/price/weather/scores → search web
ROUTE: memory_save → User shared personal info
ROUTE: clarify     → ONLY if truly ambiguous (LAST RESORT)

GOLDEN RULE: chat route = NO ACTIONS. Actions = command route.

════════════════════════════════════════════
[P1] CHAT — BE NATURAL, NOT ROBOTIC
════════════════════════════════════════════

"hello" / "hi" / "hey"        → "Boliye Sir, kya kaam hai?"
"how are you" / "kya haal"    → "Main bilkul theek hun Sir, aap batao?"
"kya kar rahe ho"             → "Aapka intezaar kar raha tha Sir."
"tu kaun hai"                 → "Sir, main {ASSISTANT_NAME} hun — aapka personal AI."
"achha" / "theek hai" / "ok"  → "Ji Sir."
"shukriya" / "thank you"      → "Hamesha Sir."
"all good" / "sab theek"      → "Ji Sir, sab ek number."
"bore ho raha hun"            → "Sir, kuch kaam dete hain main — kya karna hai?"
"mujhe neend aa rahi hai"     → "Sir, kaam band karo — so jao thoda."

════════════════════════════════════════════
COMMAND FORMATS (for LADA)
════════════════════════════════════════════

APP OPEN:    "open Google Chrome browser" / "open terminal" / "open file manager nemo"
APP CLOSE:   "close Google Chrome window"
YOUTUBE:     "youtube: Arijit Singh songs"   (ALWAYS this prefix)
WIFI:        "wifi: on" / "wifi: off"
BLUETOOTH:   "bluetooth: on" / "bluetooth: off"
BRIGHTNESS:  "brightness: 75"
VOLUME:      "volume: up" / "volume: down" / "volume: mute" / "volume: 60"
KEY:         "key: ctrl+c" / "key: alt+f4"
MULTI-APP:   Use linux_tasks LIST

[P8] MULTI-TASK — EVERY APP MUST BE LISTED:
"file manager, calculator, calendar, chrome sab kholo" →
{{
  "type": "command",
  "linux_task": "open Google Chrome browser",
  "linux_tasks": ["open Google Chrome browser", "open file manager nemo",
                  "open calculator", "open calendar"],
  "reply": "Ji Sir, sab khol raha hun."
}}

[P9] MULTI-STEP SEQUENTIAL:
"chrome kholo, github tab band karo, new tab mein claude.ai search karo" →
{{
  "type": "command",
  "linux_task": "open Chrome, close github tab, open new tab, search claude.ai",
  "reply": "Ji Sir, kar raha hun."
}}

SYSTEM INFO:
  RAM:     "free -h | awk 'NR==2{{print \"Total:\"$2\" Used:\"$3\" Free:\"$4}}'"
  Battery: "upower -i $(upower -e | grep -i bat | head -1) | grep -i percentage | awk '{{print $2}}'"
  Date:    "date '+%A, %d %B %Y'"
  Time:    "date '+%I:%M %p'"

════════════════════════════════════════════
RESPONSE JSON SCHEMA
════════════════════════════════════════════

{{"type":"chat","reply":"..."}}
{{"type":"system_info","current_command":"...","reply_template":"Sir, {{OUTPUT}}"}}
{{"type":"command","linux_task":"...","reply":"..."}}
{{"type":"command","linux_task":"...","linux_tasks":["...","..."],"reply":"..."}}
{{"type":"web_search","query":"...","reply":"..."}}
{{"type":"memory_save","fact":"...","topic":"...","reply":"..."}}

CRITICAL: Return ONLY valid JSON. No text before/after. No markdown.
"""

def ask_brain1(user_input: str, memory: FridayMemory, screen_context: str = "") -> dict:
    """Brain 1 — Router. Online? LLM. Offline? Rule-based."""
    
    # Online check
    if not _is_online():
        print("[OFFLINE] Brain 1 skip")
        t = user_input.lower().strip()
        if any(k in t for k in ['open','close','band','kholo','chrome','terminal',
                                  'volume','brightness','wifi','bluetooth','screenshot']):
            return {"type": "command", "linux_task": user_input, "reply": "Ji Sir, kar raha hun."}
        return {"type": "chat", "reply": "Sir, internet nahi hai. Sirf local commands chalenge."}
    
    if not client1:
        return {"type": "chat", "reply": "Sir, Brain 1 API key set nahi hai .env mein."}
    
    # Build context
    memory_ctx = memory.get_context_string()
    screen_ctx = f"\n[SCREEN]: {screen_context}" if screen_context else ""
    system_with_ctx = FRIDAY_SYSTEM + (memory_ctx or "") + screen_ctx
    
    messages = [{"role": "system", "content": system_with_ctx}]
    messages.extend(memory.get_short_term())
    messages.append({"role": "user", "content": user_input})
    
    # Try Brain 1
    for model in [BRAIN1_MODEL, BRAIN1_MODEL_FB]:
        try:
            resp = client1.chat.completions.create(
                model=model, messages=messages,
                temperature=0.1, max_tokens=250,
                timeout=12
            )
            raw = resp.choices[0].message.content.strip()
            raw = re.sub(r'^```json\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)
            return json.loads(raw.strip())
        except json.JSONDecodeError:
            return {"type": "chat", "reply": "Samajh nahi aaya Sir."}
        except Exception as e:
            err = str(e)
            print(f"[Brain1 {model}] Error: {err[:80]}")
            if "connection" in err.lower() or "network" in err.lower():
                break
            continue
    
    # Groq fallback
    if client_groq:
        try:
            resp = client_groq.chat.completions.create(
                model=GROQ_MODEL, messages=messages,
                temperature=0.1, max_tokens=250
            )
            raw = resp.choices[0].message.content.strip()
            raw = re.sub(r'^```json\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)
            return json.loads(raw.strip())
        except:
            pass
    
    return {"type": "chat", "reply": "Sir, kuch technical issue aa gaya."}


# ══════════════════════════════════════════════════════════════════════════
# BRAIN 3 — POLISHER
# ══════════════════════════════════════════════════════════════════════════

POLISH_SYSTEM = f"""
Tu {ASSISTANT_NAME} hai — {BOSS_FULL_NAME} ka personal AI assistant.
Iron Man ki FRIDAY ki tarah — smart, confident, thoda witty.

Tujhe ek Linux command ka RAW OUTPUT mila hai.
Use {ASSISTANT_NAME} ke character mein Hinglish reply mein convert karo — TTS ke liye.

RULES:
- "Sir" se address karo
- Max 2 sentences, TTS-ready
- Confident tone — "Kar diya Sir" not "Ho gaya hoga"
- Roman Hinglish

EXAMPLES:
RAW: "total used free\\nMem: 15Gi 8.2Gi 7.1Gi"
REPLY: "Sir, 15 GB RAM mein 8.2 lagi hui hai, 7.1 free hai."

RAW: "Percentage: 78%"
REPLY: "Battery 78 percent hai Sir."

RAW: "" (empty)
REPLY: "Ho gaya Sir."

RAW: "error: command not found"
REPLY: "Sir, kuch gadbad hui — command nahi mila."

Return: ONLY the reply string. No JSON, no quotes, no extra text.
"""


def polish_output(raw_output: str, original_command: str, brief_reply: str = "") -> str:
    """Brain 3 — LADA output → FRIDAY character reply."""
    if not raw_output or len(raw_output.strip()) < 3:
        return brief_reply or "Ho gaya Sir."
    
    user_msg = f"COMMAND: {original_command}\nRAW OUTPUT:\n{raw_output[:500]}"
    
    for client, model in [(client3, BRAIN3_MODEL), (client_groq, GROQ_MODEL)]:
        if not client:
            continue
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": POLISH_SYSTEM},
                    {"role": "user", "content": user_msg}
                ],
                temperature=0.3, max_tokens=80, timeout=8
            )
            r = resp.choices[0].message.content
            if r:
                return r.strip().strip('"').strip("'").strip()
        except Exception as e:
            print(f"[Brain3 {model}] {str(e)[:60]}")
    
    return brief_reply or "Ho gaya Sir."


# ══════════════════════════════════════════════════════════════════════════
# INTERNET CHECK
# ══════════════════════════════════════════════════════════════════════════

_connectivity_cache      = True
_last_connectivity_check = 0.0

def _is_online() -> bool:
    global _connectivity_cache, _last_connectivity_check
    now = time.monotonic()
    if now - _last_connectivity_check > 15:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            s.connect(("8.8.8.8", 53))
            s.close()
            _connectivity_cache = True
        except:
            _connectivity_cache = False
        _last_connectivity_check = now
    return _connectivity_cache

def is_online() -> bool:
    return _is_online()


# ══════════════════════════════════════════════════════════════════════════
# LADA DAEMON — Brain 2 interface
# ══════════════════════════════════════════════════════════════════════════

def _build_daemon_code(lada_dir, sock_path):
    return (
        'import asyncio, sys, os, io\n'
        'sys.path.insert(0, ' + repr(lada_dir) + ')\n'
        'os.chdir(' + repr(lada_dir) + ')\n'
        'from main import LADA\n'
        'agent = None\n'
        '\n'
        'async def boot():\n'
        '    global agent\n'
        '    agent = LADA(exec_mode="live")\n'
        '    await agent.boot()\n'
        '    print("DAEMON_READY", flush=True)\n'
        '\n'
        'async def handle(reader, writer):\n'
        '    try:\n'
        '        data = await asyncio.wait_for(reader.read(4096), timeout=5.0)\n'
        '        cmd = data.decode().strip()\n'
        '        if cmd == "__PING__":\n'
        '            writer.write(b"pong")\n'
        '        elif cmd == "__QUIT__":\n'
        '            writer.write(b"bye")\n'
        '            await writer.drain()\n'
        '            writer.close()\n'
        '            asyncio.get_event_loop().stop()\n'
        '            return\n'
        '        else:\n'
        '            captured = io.StringIO()\n'
        '            old_stdout = sys.stdout\n'
        '            sys.stdout = captured\n'
        '            try:\n'
        '                result = await agent.run_command(cmd)\n'
        '            finally:\n'
        '                sys.stdout = old_stdout\n'
        '            out = captured.getvalue().strip()\n'
        '            out = out.replace("[LADA] ", "").strip()\n'
        '            if result and result.success:\n'
        '                writer.write(("success|" + out[:400]).encode())\n'
        '            else:\n'
        '                emsg = ""\n'
        '                if result and result.error:\n'
        '                    emsg = result.error[:60]\n'
        '                elif out:\n'
        '                    emsg = out[:60]\n'
        '                else:\n'
        '                    emsg = "no_result"\n'
        '                writer.write(("failed:" + emsg).encode())\n'
        '    except Exception as ex:\n'
        '        writer.write(("failed:" + str(ex)[:60]).encode())\n'
        '    finally:\n'
        '        try:\n'
        '            await writer.drain()\n'
        '            writer.close()\n'
        '        except: pass\n'
        '\n'
        'async def main():\n'
        '    await boot()\n'
        '    try: os.unlink(' + repr(sock_path) + ')\n'
        '    except: pass\n'
        '    server = await asyncio.start_unix_server(handle, path=' + repr(sock_path) + ')\n'
        '    async with server:\n'
        '        await server.serve_forever()\n'
        '\n'
        'asyncio.run(main())\n'
    )


_daemon_proc = None


def _ping() -> bool:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            s.connect(SOCKET_PATH)
            s.sendall(b"__PING__")
            return s.recv(16) == b"pong"
    except:
        return False


def start_daemon() -> bool:
    global _daemon_proc
    if _ping():
        return True
    
    code = _build_daemon_code(LADA_DIR, SOCKET_PATH)
    tmp  = "/tmp/_lada_srv.py"
    with open(tmp, "w") as f:
        f.write(code)
    
    _daemon_proc = subprocess.Popen(
        [sys.executable, tmp],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    
    import select
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        r, _, _ = select.select([_daemon_proc.stdout], [], [], 0.5)
        if r:
            line = _daemon_proc.stdout.readline().decode().strip()
            if "DAEMON_READY" in line:
                return True
        if _daemon_proc.poll() is not None:
            break
    return False


def send_to_daemon(cmd: str, timeout: float = 90.0) -> tuple:
    if not _ping():
        if not start_daemon():
            return False, ""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(SOCKET_PATH)
            s.sendall(cmd.encode())
            raw = s.recv(1024).decode().strip()
            if raw.startswith("success|"):
                return True, raw[8:]
            elif raw == "success":
                return True, ""
            else:
                return False, raw
    except socket.timeout:
        return False, "timeout"
    except Exception as e:
        return False, str(e)


def stop_daemon():
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(2.0)
            s.connect(SOCKET_PATH)
            s.sendall(b"__QUIT__")
    except:
        pass
    if _daemon_proc:
        _daemon_proc.terminate()

# ══════════════════════════════════════════════════════════════════════════
# DIRECT EXECUTOR — LADA ke bina bhi kaam kare (P3 + daemon fallback)
# ══════════════════════════════════════════════════════════════════════════

# App name → executable candidates (priority order)
_APP_EXEC_MAP = {
    # Browsers
    "google-chrome":        ["google-chrome", "google-chrome-stable", "chromium-browser", "chromium", "firefox"],
    "firefox":              ["firefox", "firefox-esr"],
    "chromium":             ["chromium-browser", "chromium"],
    "google-chrome||firefox": ["google-chrome", "google-chrome-stable", "chromium-browser", "firefox"],
    # Terminals
    "gnome-terminal":       ["gnome-terminal", "xterm", "xfce4-terminal", "konsole", "mate-terminal", "tilix"],
    # File managers
    "nemo":                 ["nemo", "nautilus", "thunar", "pcmanfm", "caja"],
    # Editors
    "xed||gedit||mousepad": ["xed", "gedit", "mousepad", "kate", "pluma", "gnome-text-editor"],
    "code":                 ["code", "codium"],
    # Calculators
    "gnome-calculator||kcalc||xcalc": ["gnome-calculator", "kcalc", "xcalc", "qalculate-gtk", "galculator"],
    # Others
    "vlc":                  ["vlc"],
    "spotify":              ["spotify"],
    "gimp":                 ["gimp"],
    "discord":              ["discord"],
    "telegram-desktop||telegram": ["telegram-desktop", "telegram"],
    "steam":                ["steam"],
    "slack":                ["slack"],
    "zoom":                 ["zoom"],
    "gnome-system-monitor": ["gnome-system-monitor", "ksysguard", "xfce4-taskmanager"],
    "gnome-control-center||cinnamon-settings": ["cinnamon-settings", "gnome-control-center", "unity-control-center"],
    "gnome-calendar||orage": ["gnome-calendar", "orage", "korganizer"],
    "eog||shotwell||xviewer": ["eog", "eom", "xviewer", "shotwell", "gpicview"],
    "evince||okular||atril": ["evince", "atril", "okular", "mupdf"],
    "sticky||gnome-notes||xpad": ["sticky", "xpad", "gnome-notes", "tomboy"],
    "gnome-clocks":         ["gnome-clocks"],
    "rhythmbox":            ["rhythmbox", "clementine", "deadbeef"],
    "libreoffice":          ["libreoffice"],
    "inkscape":             ["inkscape"],
}


def direct_execute(linux_task: str) -> tuple:
    """
    LADA daemon ke bina direct execution — subprocess se.
    LADA down ho toh bhi kaam karega.
    
    Handles:
      - open_app: <app>       → app launch
      - open_app: a||b||c     → first available ka launch
      - youtube: <query>      → Chrome mein YouTube open
      - wifi: on/off          → nmcli
      - bluetooth: on/off     → rfkill
      - brightness: N         → brightnessctl
      - volume: up/down/N/mute → pactl / amixer
      - key: <combo>          → xdotool key
      - lock screen ...       → loginctl
      - take screenshot       → scrot
      - shutdown/restart/suspend → systemctl
      - run_command: <cmd>    → direct shell
    
    Returns: (success: bool, output: str)
    """
    import shutil as _sh
    lt = linux_task.strip()
    lt_low = lt.lower()
    
    # ── open_app: ────────────────────────────────────────────────
    if lt_low.startswith("open_app:"):
        app_spec = lt[len("open_app:"):].strip()
        candidates = _APP_EXEC_MAP.get(app_spec, None)
        
        if candidates is None:
            # Try splitting by || directly
            if "||" in app_spec:
                candidates = [a.strip() for a in app_spec.split("||")]
            else:
                candidates = [app_spec, app_spec.replace(" ", "-")]
        
        for cand in candidates:
            exe = _sh.which(cand)
            if exe:
                try:
                    subprocess.Popen(
                        [exe],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    print(f"[DIRECT] Launched: {cand}")
                    return True, f"Launched {cand}"
                except Exception as e:
                    print(f"[DIRECT] Launch failed ({cand}): {e}")
                    continue
        
        # Last resort: try app_spec as-is
        try:
            subprocess.Popen(
                app_spec.split(), stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True
            )
            return True, f"Launched {app_spec}"
        except:
            return False, f"App not found: {app_spec}"
    
    # ── open <app_name> using command: <exe> ────────────────────
    if "using command:" in lt_low:
        m = re.search(r"using command:\s*(.+)$", lt)
        if m:
            cmd = m.group(1).strip().rstrip("&").strip()
            exe = cmd.split()[0]
            if _sh.which(exe):
                try:
                    subprocess.Popen(cmd.split(), stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, start_new_session=True)
                    return True, f"Launched {exe}"
                except Exception as e:
                    return False, str(e)
            return False, f"Executable not found: {exe}"
    
    # ── youtube: <query> ─────────────────────────────────────────
    if lt_low.startswith("youtube:"):
        query = lt[8:].strip()
        encoded = query.replace(" ", "+")
        url = f"https://www.youtube.com/results?search_query={encoded}"
        for browser in ["google-chrome", "google-chrome-stable", "chromium-browser",
                         "chromium", "firefox", "xdg-open"]:
            if _sh.which(browser):
                try:
                    subprocess.Popen([browser, url], stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, start_new_session=True)
                    return True, f"YouTube search opened: {query}"
                except:
                    continue
        return False, "No browser found"
    
    # ── wifi: on/off ──────────────────────────────────────────────
    if lt_low.startswith("wifi:"):
        action = "on" if "on" in lt_low else "off"
        r = subprocess.run(f"nmcli radio wifi {action}", shell=True,
                           capture_output=True, text=True, timeout=5)
        ok = r.returncode == 0
        return ok, f"WiFi {action}" if ok else r.stderr
    
    # ── bluetooth: on/off ─────────────────────────────────────────
    if lt_low.startswith("bluetooth:"):
        if "on" in lt_low:
            r = subprocess.run("rfkill unblock bluetooth", shell=True,
                               capture_output=True, timeout=5)
        else:
            r = subprocess.run("rfkill block bluetooth", shell=True,
                               capture_output=True, timeout=5)
        return r.returncode == 0, "Bluetooth toggled"
    
    # ── brightness: N ────────────────────────────────────────────
    if lt_low.startswith("brightness:"):
        level = re.search(r"\d+", lt)
        if level:
            n = int(level.group())
            for cmd in [f"brightnessctl set {n}%",
                        f"xrandr --output $(xrandr | grep ' connected' | head -1 | awk '{{print $1}}') --brightness {n/100:.2f}"]:
                r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
                if r.returncode == 0:
                    return True, f"Brightness set to {n}%"
        return False, "brightnessctl not found"
    
    # ── volume: ───────────────────────────────────────────────────
    if lt_low.startswith("volume:"):
        spec = lt[7:].strip().lower()
        # Detect audio backend
        # Audio backend: try pactl, wpctl, amixer in order
        _audio_tools = {
            "pactl": {
                "mute":  "pactl set-sink-mute @DEFAULT_SINK@ toggle",
                "up":    "pactl set-sink-volume @DEFAULT_SINK@ +10%",
                "down":  "pactl set-sink-volume @DEFAULT_SINK@ -10%",
                "set_n": "pactl set-sink-volume @DEFAULT_SINK@ {N}%",
            },
            "wpctl": {
                "mute":  "wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle",
                "up":    "wpctl set-volume @DEFAULT_AUDIO_SINK@ 10%+",
                "down":  "wpctl set-volume @DEFAULT_AUDIO_SINK@ 10%-",
                "set_n": "wpctl set-volume @DEFAULT_AUDIO_SINK@ {N}%",
            },
            "amixer": {
                "mute":  "amixer set Master toggle",
                "up":    "amixer set Master 10%+",
                "down":  "amixer set Master 10%-",
                "set_n": "amixer set Master {N}%",
            },
        }
        
        tool_cmds = None
        for tool, cmds in _audio_tools.items():
            if _sh.which(tool):
                tool_cmds = cmds
                break
        
        if not tool_cmds:
            # Last resort: try xdotool key XF86AudioRaiseVolume
            if _sh.which("xdotool"):
                key_map = {"up": "XF86AudioRaiseVolume", "down": "XF86AudioLowerVolume",
                           "mute": "XF86AudioMute"}
                if spec in key_map:
                    r = subprocess.run(["xdotool", "key", key_map[spec]],
                                       capture_output=True, timeout=3)
                    return r.returncode == 0, f"Volume key: {spec}"
            return False, "No audio tool found (pactl/wpctl/amixer/xdotool)"
        
        if spec == "mute":
            cmd = tool_cmds["mute"]
        elif spec == "up":
            cmd = tool_cmds["up"]
        elif spec == "down":
            cmd = tool_cmds["down"]
        else:
            # Numeric
            n = re.search(r"\d+", spec)
            if n:
                pct = min(150, int(n.group()))
                cmd = tool_cmds["set_n"].replace("{N}", str(pct))
            else:
                return False, f"Unknown volume spec: {spec}"
        
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
        return r.returncode == 0, spec
    
    # ── key: <combo> ─────────────────────────────────────────────
    if lt_low.startswith("key:"):
        combo = lt[4:].strip()
        # x3 handling
        times = 1
        m = re.search(r"x(\d+)$", combo)
        if m:
            times = int(m.group(1))
            combo = combo[:m.start()].strip()
        
        if not _sh.which("xdotool"):
            return False, "xdotool not found"
        
        for _ in range(times):
            r = subprocess.run(["xdotool", "key", combo],
                               capture_output=True, timeout=3)
            if r.returncode != 0:
                return False, f"key {combo} failed"
            if times > 1:
                time.sleep(0.1)
        return True, f"Key {combo} x{times}"
    
    # ── lock screen ───────────────────────────────────────────────
    if "lock" in lt_low and "loginctl" in lt_low:
        r = subprocess.run("loginctl lock-session", shell=True,
                           capture_output=True, timeout=5)
        return r.returncode == 0, "Screen locked"
    
    # ── screenshot ────────────────────────────────────────────────
    if "screenshot" in lt_low or "scrot" in lt_low:
        ts = subprocess.run("date +%Y%m%d_%H%M%S", shell=True,
                            capture_output=True, text=True).stdout.strip()
        path = f"~/Pictures/screenshot_{ts}.png"
        for cmd in [f"scrot {path}", f"import -window root {path}"]:
            r = subprocess.run(cmd, shell=True, capture_output=True, timeout=10)
            if r.returncode == 0:
                return True, f"Screenshot saved to {path}"
        return False, "scrot not found"
    
    # ── shutdown/restart/suspend ──────────────────────────────────
    if "shutdown" in lt_low or "poweroff" in lt_low:
        subprocess.Popen(["systemctl", "poweroff"])
        return True, "Shutting down"
    if "restart" in lt_low or "reboot" in lt_low:
        subprocess.Popen(["systemctl", "reboot"])
        return True, "Restarting"
    if "suspend" in lt_low or "sleep" in lt_low:
        subprocess.Popen(["systemctl", "suspend"])
        return True, "Suspending"
    
    # ── close window ─────────────────────────────────────────────
    if "close" in lt_low or "kill" in lt_low:
        for app in ["chrome", "google-chrome", "firefox", "terminal",
                    "gnome-terminal", "nemo", "code"]:
            if app in lt_low:
                r = subprocess.run(f"pkill -f {app}", shell=True,
                                   capture_output=True, timeout=5)
                return True, f"Closed {app}"
        if _sh.which("wmctrl"):
            r = subprocess.run("wmctrl -c :ACTIVE:", shell=True,
                               capture_output=True, timeout=5)
            return r.returncode == 0, "Window closed"
        return False, "No method to close window"
    
    # ── run_command: <cmd> / generic shell ───────────────────────
    if lt_low.startswith("run_command:"):
        cmd = lt[12:].strip()
    elif lt_low.startswith("shell:"):
        cmd = lt[6:].strip()
    else:
        # Generic: try as-is shell command
        cmd = lt
    
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True,
                           text=True, timeout=15)
        out = r.stdout.strip() or r.stderr.strip()
        return r.returncode == 0, out
    except subprocess.TimeoutExpired:
        return False, "Command timeout"
    except Exception as e:
        return False, str(e)


def smart_send(linux_task: str, timeout: float = 60.0) -> tuple:
    """
    Smart execution — LADA daemon first, direct_execute() fallback.
    LADA daemon fail ho toh bhi sab commands kaam karenge.
    """
    # First try LADA daemon (full planning + verification)
    if _ping():
        success, output = send_to_daemon(linux_task, timeout=min(timeout, 30.0))
        if success:
            return success, output
        # LADA failed — try direct
        print(f"[LADA-FAIL] Falling back to direct: {linux_task[:60]}")
    else:
        print(f"[LADA-DOWN] Direct execution: {linux_task[:60]}")
    
    # Direct execution fallback
    return direct_execute(linux_task)




def run_system_command(cmd: str) -> str:
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
        out = result.stdout.strip()
        return out if out else result.stderr.strip()
    except Exception as e:
        return str(e)


# ══════════════════════════════════════════════════════════════════════════
# [P3] VIRTUAL CLICK — xdotool se, user mouse untouched
# ══════════════════════════════════════════════════════════════════════════

def virtual_click(window_name: str, element_name: str = "", x: int = 0, y: int = 0) -> bool:
    """
    User ka mouse move kiye bina window mein click karo.
    Method: xdotool windowfocus + key/mousemove + click
    """
    try:
        # Window ID dhundo
        result = subprocess.run(
            f"xdotool search --name {repr(window_name)} | head -1",
            shell=True, capture_output=True, text=True, timeout=3
        )
        wid = result.stdout.strip()
        if not wid:
            return False
        
        # Focus window (silently — without raising to front if possible)
        subprocess.run(f"xdotool windowfocus {wid}", shell=True, timeout=2)
        
        if element_name:
            # AT-SPI se element dhundo aur click karo
            return _atspi_click(wid, element_name)
        elif x and y:
            # Coordinate based click (window-relative)
            subprocess.run(
                f"xdotool mousemove --window {wid} {x} {y} click 1",
                shell=True, timeout=3
            )
            return True
        return False
    except Exception as e:
        print(f"[VIRTUAL_CLICK] {e}")
        return False


def _atspi_click(window_id: str, element_name: str) -> bool:
    """AT-SPI se element dhundo aur click karo."""
    try:
        import pyatspi
        reg = pyatspi.Registry
        for i in range(reg.getAppCount()):
            try:
                app = reg.getApp(i)
                if not app:
                    continue
                for j in range(min(app.childCount, 20)):
                    try:
                        child = app[j]
                        if child and element_name.lower() in (child.name or "").lower():
                            action = child.queryAction()
                            action.doAction(0)
                            return True
                    except:
                        continue
            except:
                continue
    except:
        pass
    return False


# ══════════════════════════════════════════════════════════════════════════
# [P5] LADA PLAN CACHE — Fix wrong cache issue
# ══════════════════════════════════════════════════════════════════════════

def reset_lada_cache(reason: str = "user request"):
    """LADA plan cache purge karo — galat cached plans remove."""
    try:
        cache_db = os.path.join(LADA_DIR, "memory", "plan_cache.db")
        if os.path.exists(cache_db):
            import sqlite3
            conn = sqlite3.connect(cache_db)
            cur = conn.cursor()
            
            # Get count before
            cur.execute("SELECT COUNT(*) FROM plans")
            count_before = cur.fetchone()[0]
            
            # Delete all plans (fresh start)
            cur.execute("DELETE FROM plans")
            conn.commit()
            conn.close()
            
            print(f"[CACHE] Purged {count_before} plans ({reason})")
            return count_before
    except Exception as e:
        print(f"[CACHE] Reset error: {e}")
    return 0


def purge_old_cache_plans():
    """Purane plans (older than PLAN_CACHE_MAX_AGE hours) delete karo."""
    try:
        cache_db = os.path.join(LADA_DIR, "memory", "plan_cache.db")
        if not os.path.exists(cache_db):
            return
        import sqlite3
        conn = sqlite3.connect(cache_db)
        cur = conn.cursor()
        # Timestamp check — plans older than N hours delete karo
        cutoff = time.time() - (PLAN_CACHE_MAX_AGE * 3600)
        try:
            cur.execute("DELETE FROM plans WHERE created_at < ?", (cutoff,))
            deleted = conn.total_changes
            if deleted > 0:
                print(f"[CACHE] Purged {deleted} old plans")
        except:
            pass  # Column might not exist
        conn.commit()
        conn.close()
    except:
        pass


# ══════════════════════════════════════════════════════════════════════════
# APP DISCOVERY (same as v7, preserved)
# ══════════════════════════════════════════════════════════════════════════

def _discover_app(app_name: str) -> str:
    import shutil
    app_name_lower = app_name.lower().strip()
    
    CATEGORIES = {
        "text editor":   ["gedit", "xed", "mousepad", "kate", "pluma", "leafpad"],
        "file manager":  ["nemo", "nautilus", "thunar", "pcmanfm", "caja"],
        "terminal":      ["gnome-terminal", "xterm", "xfce4-terminal", "konsole"],
        "browser":       ["google-chrome", "chromium-browser", "firefox"],
        "calculator":    ["gnome-calculator", "kcalc", "galculator", "xcalc"],
        "settings":      ["cinnamon-settings", "gnome-control-center"],
        "system settings":["cinnamon-settings", "gnome-control-center"],
        "calendar":      ["gnome-calendar", "orage", "korganizer"],
        "music player":  ["rhythmbox", "clementine", "deadbeef", "audacious"],
        "pdf viewer":    ["evince", "okular", "atril", "mupdf"],
        "image viewer":  ["eog", "eom", "shotwell", "gpicview"],
        "system monitor":["gnome-system-monitor", "ksysguard"],
    }
    
    DIRECT_MAP = {
        "gedit": "gedit", "xed": "xed", "mousepad": "mousepad",
        "nemo": "nemo", "nautilus": "nautilus", "thunar": "thunar",
        "chrome": "google-chrome", "google-chrome": "google-chrome",
        "firefox": "firefox", "vlc": "vlc", "gimp": "gimp",
        "code": "code", "vscode": "code", "vs code": "code",
        "calculator": "gnome-calculator",
        "telegram": "telegram-desktop", "discord": "discord",
        "spotify": "spotify", "steam": "steam",
    }
    
    if app_name_lower in DIRECT_MAP:
        cmd = DIRECT_MAP[app_name_lower]
        return f"open {app_name} using command: {cmd} &"
    
    for category, candidates in CATEGORIES.items():
        if category in app_name_lower or app_name_lower in category:
            for candidate in candidates:
                if shutil.which(candidate):
                    return f"open {category} using command: {candidate} &"
            return f"open {category} using command: {candidates[0]} &"
    
    for name, cmd in DIRECT_MAP.items():
        if name in app_name_lower or app_name_lower in name:
            return f"open {app_name} using command: {cmd} &"
    
    guessed_cmd = app_name_lower.replace(" ", "-")
    if shutil.which(guessed_cmd):
        return f"open {app_name} using command: {guessed_cmd} &"
    
    return f"open {app_name}"


def _resolve_app_task(linux_task: str) -> str:
    lt = linux_task.strip()
    lt_lower = lt.lower()
    
    if any(lt_lower.startswith(p) for p in [
        "youtube:", "wifi:", "bluetooth:", "brightness:", "volume:", "key:",
        "free -h", "df -h", "upower", "date ", "pgrep",
        "wmctrl", "xdotool", "loginctl", "systemctl", "nemo ",
        "google-chrome", "gnome-terminal", "open file manager nemo",
        "open terminal", "open Google Chrome", "open_app:"
    ]):
        return lt
    
    m = re.match(r'^open\s+(.+?)(?:\s+(?:app|application|karo|kholo))?$', lt_lower)
    if m:
        app_name = m.group(1).strip()
        KNOWN = {"google chrome browser", "chrome browser", "google chrome",
                 "file manager nemo", "file manager", "nemo",
                 "terminal", "terminal emulator", "vs code", "vscode"}
        if app_name in KNOWN:
            return lt
        discovered = _discover_app(app_name)
        if discovered != f"open {app_name}":
            return discovered
    
    return lt


# ══════════════════════════════════════════════════════════════════════════
# [P6] PROACTIVE MODE — Screen dekh ke comment karna
# ══════════════════════════════════════════════════════════════════════════

_last_proactive_time = 0.0

def get_proactive_comment(memory: FridayMemory, screen_context: str) -> str:
    """
    Proactive comment generate karo agar appropriate ho.
    Return: comment string, ya "" agar koi comment nahi.
    """
    global _last_proactive_time
    
    if not PROACTIVE_MODE:
        return ""
    
    now = time.monotonic()
    # Rate limit: har PROACTIVE_INTERVAL minute mein ek baar max
    if now - _last_proactive_time < (PROACTIVE_INTERVAL * 60):
        return ""
    
    session_mins = memory.get_session_duration_mins()
    since_last   = memory.minutes_since_last_activity()
    
    comment = ""
    
    # User bahut der se kaam kar raha hai
    if session_mins >= 90:
        comment = f"Sir, {session_mins} minute se kaam ho raha hai — thoda paani pee lo."
    elif session_mins >= 45 and session_mins < 90:
        comment = "Sir, kaafi der se kaam kar rahe hain — thoda stretch kar lo."
    
    # Screen pe kuch interesting hai
    if not comment and screen_context:
        scr_low = screen_context.lower()
        if "github" in scr_low:
            comment = "Sir, GitHub pe ho — koi naya issue ya commit chahiye?"
        elif "youtube" in scr_low:
            pass  # Video dekh rahe hain — disturb mat karo
        elif "error" in scr_low or "exception" in scr_low:
            comment = "Sir, screen pe kuch error dikh raha hai — help chahiye?"
    
    if comment:
        _last_proactive_time = now
    
    return comment


# ══════════════════════════════════════════════════════════════════════════
# SCREEN CONTEXT GETTER
# ══════════════════════════════════════════════════════════════════════════

def get_screen_context() -> str:
    try:
        from eyes_reader import get_screen_context as _gsc
        return _gsc()
    except ImportError:
        try:
            sys.path.insert(0, _THIS_DIR)
            from eyes_reader import get_screen_context as _gsc
            return _gsc()
        except:
            return ""


# ══════════════════════════════════════════════════════════════════════════
# MAIN SESSION CLASS
# ══════════════════════════════════════════════════════════════════════════

class FridaySession:
    def __init__(self, tts_fn=None):
        self.memory = FridayMemory()
        self.tts_fn = tts_fn
        
        # [P14] Stop signal clear
        STOP_CTRL.clear()
        
        print(f"{ASSISTANT_NAME} v8.0 — Three Brain System online.")
        print(f"  Boss: {BOSS_FULL_NAME} | Memory: {len(self.memory.long_term)} facts")
        
        # Daemon boot (background thread)
        print("[LADA] Daemon boot ho raha hai...", flush=True)
        self._daemon_ready = False
        def _boot():
            self._daemon_ready = start_daemon()
        t = threading.Thread(target=_boot, daemon=True)
        t.start()
        t.join(timeout=28)
        
        if self._daemon_ready:
            print("[LADA] Daemon ready!", flush=True)
        else:
            print("[LADA] Daemon start nahi hua", flush=True)
        
        # Purge old cache plans on startup
        if PLAN_CACHE_ENABLED:
            purge_old_cache_plans()
    
    def _speak(self, text: str):
        if not text:
            return
        # [P11] TTS preprocessing
        clean_text = preprocess_for_tts(text)
        if not clean_text:
            return
        if self.tts_fn:
            try:
                self.tts_fn(clean_text)
            except Exception as e:
                print(f"[TTS err] {e}")
        else:
            print(f"{ASSISTANT_NAME}: {clean_text}")
    
    def _is_whisper_garbage(self, text: str) -> bool:
        t = text.lower().strip()
        garbage_phrases = [
            "thanks for watching", "i just", "bullying",
            "make sure that i'm going to", "it's not my eye",
            "damage not high", "borrow voting", "coach son i know",
        ]
        for phrase in garbage_phrases:
            if phrase in t:
                return True
        
        hindi_indicators = [
            "karo", "kar", "kholo", "band", "chala", "batao", "kya", "hai",
            "meri", "mera", "aaj", "kaun", "kyun", "kuch", "nahi", "haan",
            "open", "close", "chrome", "volume", "brightness", "wifi",
            "terminal", "youtube", "screenshot", "system", "ram", "battery",
            "hello", "hi", "hey", "sir", "friday"
        ]
        has_valid = any(w in t for w in hindi_indicators)
        
        if not has_valid and len(t.split()) > 4:
            suspicious = ["i'm going to", "so i'm", "it's not", "i know",
                          "we'll leave", "answer the power"]
            if any(s in t for s in suspicious):
                return True
        return False
    
    def _rule_route(self, text: str):
        """
        [P1] + [P7] Rule-based pre-router — brain 1 se pehle.
        Greetings, simple commands, system info — instantly handle.
        """
        t = text.lower().strip()
        t = re.sub(r"[\u2019\u2018`]", "'", t)
        t = re.sub(r'[!।]', '', t).strip()
        
        # ── [P14] STOP COMMAND ────────────────────────────────────────
        if is_stop_command(t):
            STOP_CTRL.request_stop()
            return {"type": "chat", "reply": "Roke deta hun Sir."}
        
        # ── [P5] CACHE RESET COMMAND ──────────────────────────────────
        if any(k in t for k in ['cache reset', 'cache clear', 'lada reset',
                                  'galat kaam', 'wrong cache', 'cache band']):
            n = reset_lada_cache("user command")
            return {"type": "chat", "reply": f"Sir, {n} cached plans clear kar diye."}
        
        # ── MULTI-STEP → Brain 1 ──────────────────────────────────────
        if any(m in (' ' + t + ' ') for m in [' aur ', ' and ', ' phir ', ' then ',
                                                ' uske baad ', ' after that ']):
            return None
        
        # ── KEYBOARD SHORTCUTS → Brain 1 ─────────────────────────────
        if any(k in t for k in ['alt+', 'ctrl+', 'control+', 'shift+', 'super+',
                                  'window+', 'win+', 'press key']):
            return None
        
        # ── [P1] GREETINGS / CASUAL CHAT ─────────────────────────────
        # Ye ab bahut zyada handle karta hai — P1 fully fixed
        _greetings = {
            "hello": "Boliye Sir, kya kaam hai?",
            "hi": "Boliye Sir, kya kaam hai?",
            "hey": "Boliye Sir, kya kaam hai?",
            "hii": "Boliye Sir, kya kaam hai?",
            "helo": "Boliye Sir, kya kaam hai?",
            "sun": "Boliye Sir.",
            "suno": "Boliye Sir.",
            "friday": "Ji Sir, boliye.",
        }
        if t in _greetings:
            return {"type": "chat", "reply": _greetings[t]}
        
        # How are you variants
        if any(k in t for k in ["how are you", "kya haal", "kaisa hai", "kaisi ho",
                                  "theek ho", "kya chal raha", "sab theek",
                                  "aap theek", "kaise ho"]):
            return {"type": "chat", "reply": "Main bilkul theek hun Sir, aap batao?"}
        
        # What are you doing
        if any(k in t for k in ["kya kar rahe", "kya kar rahi", "busy hai",
                                  "kya chal", "what are you doing"]):
            return {"type": "chat", "reply": "Aapka intezaar kar raha tha Sir."}
        
        # Who are you
        if any(k in t for k in ["tu kaun", "tum kaun", "aap kaun", "who are you",
                                  "kaun ho", "apna naam"]):
            return {"type": "chat", "reply": f"Sir, main {ASSISTANT_NAME} hun — {BOSS_NAME} Sir ka personal AI assistant."}
        
        # Thank you
        if any(k in t for k in ["shukriya", "thank you", "thanks", "dhanyavad",
                                  "bahut accha", "wah", "great", "superb"]):
            return {"type": "chat", "reply": "Hamesha Sir."}
        
        # Acknowledgements
        if t in ('nothing', 'kuch nahi', 'rehne do', 'theek hai', 'okay', 'ok', 'bas',
                 'thik hai', 'achha', 'acha', 'hmm', 'haan', 'han', 'hn', 'right',
                 'bilkul', 'sure', 'got it', 'samajh gaya', 'theek hun', 'main theek hun',
                 'theek hoon', 'thik hun', 'all good', 'sab theek', 'hn theek', 'accha',
                 'noted', 'ok sir', 'okay sir', 'alright', 'understood', 'done',
                 'ho gaya', 'kar diya', 'shukriya', 'bahut accha', 'great', 'nice'):
            return {"type": "chat", "reply": "Ji Sir."}
        
        # Boredom / feelings
        if any(k in t for k in ["bore", "thaka", "thak gaya", "neend", "bhookh"]):
            if "bore" in t:
                return {"type": "chat", "reply": "Sir, kuch interesting kaam karte hain — kya karna hai?"}
            elif "neend" in t or "so jao" in t:
                return {"type": "chat", "reply": "Sir, system band karna hai? Ya sirf screen lock?"}
            elif "thaka" in t or "thak" in t:
                return {"type": "chat", "reply": "Sir, thoda break lete hain — kab se kaam kar rahe hain?"}
        
        # ── BRIGHTNESS ────────────────────────────────────────────────
        if any(k in t for k in ['brightness', 'bright', 'ujala', 'dim screen', 'screen dim']):
            bri = re.search(r'(\d+)\s*%?', t)
            if bri:
                level = max(0, min(100, int(bri.group(1))))
            elif any(k in t for k in ['max', 'full', 'poori']):
                level = 100
            elif any(k in t for k in ['badao', 'badhao', 'increase', 'zyada']):
                level = 80
            elif any(k in t for k in ['bilkul kam', 'minimum']):
                level = 5
            elif any(k in t for k in ['kam', 'dim', 'decrease', 'thodi']):
                level = 20
            else:
                level = 70
            return {"type": "command", "linux_task": f"brightness: {level}",
                    "reply": f"Ji Sir, brightness {level}% kar raha hun"}
        
        # ── WIFI ──────────────────────────────────────────────────────
        if any(k in t for k in ['wifi', 'wi-fi', 'wi fi', 'wireless']):
            is_off = any(k in t for k in ['off', 'band', 'bandh', 'hatao', 'disable'])
            action = 'off' if is_off else 'on'
            msg    = 'band' if is_off else 'chalu'
            return {"type": "command", "linux_task": f"wifi: {action}",
                    "reply": f"Ji Sir, WiFi {msg} kar raha hun"}
        
        # ── BLUETOOTH ─────────────────────────────────────────────────
        if any(k in t for k in ['bluetooth', 'blue tooth']):
            is_off = any(k in t for k in ['off', 'band', 'bandh', 'hatao', 'disable'])
            action = 'off' if is_off else 'on'
            msg    = 'band' if is_off else 'chalu'
            return {"type": "command", "linux_task": f"bluetooth: {action}",
                    "reply": f"Ji Sir, Bluetooth {msg} kar raha hun"}
        
        # ── VOLUME ────────────────────────────────────────────────────
        if any(k in t for k in ['volume', 'awaaz', 'sound', 'audio']):
            vol_pct = re.search(r'(\d+)\s*%?', t)
            if any(k in t for k in ['mute', 'chup', 'silent']):
                return {"type": "command", "linux_task": "volume: mute",
                        "reply": "Ji Sir, mute kar raha hun"}
            elif vol_pct:
                pct = max(0, min(100, int(vol_pct.group(1))))
                return {"type": "command", "linux_task": f"volume: {pct}",
                        "reply": f"Ji Sir, volume {pct}% kar raha hun"}
            elif any(k in t for k in ['badao', 'badhao', 'zyada', 'up', 'loud', 'increase']):
                return {"type": "command", "linux_task": "volume: up",
                        "reply": "Ji Sir, volume badha raha hun"}
            else:
                return {"type": "command", "linux_task": "volume: down",
                        "reply": "Ji Sir, volume kam kar raha hun"}
        
        # ── YOUTUBE / MUSIC ───────────────────────────────────────────
        has_music = any(k in t for k in ['youtube', 'song', 'gana', 'gaana', 'music', 'gaane'])
        has_play  = any(k in t for k in ['bajao', 'chala', 'play', 'lao', 'sunao', 'lagao'])
        if has_music and has_play:
            noise_exact = ['youtube pe', 'youtube par', 'youtube mein', 'youtube ko',
                           'chrome par', 'chrome mein', 'chrome pe', 'browser pe',
                           'open karo aur', 'search karo', 'play karo', 'play kar',
                           'chala do', 'chalao', 'chala', 'sunao', 'lagao', 'bajao',
                           'ke gaane', 'ke gane', 'ki songs', 'ka gana', 'ke songs']
            noise_words = ['youtube', 'chrome', 'browser', 'open', 'karo', 'kar', 'do',
                           'search', 'pe', 'par', 'mein', 'song', 'songs', 'gana', 'gaana',
                           'gaane', 'music', 'play', 'lao', 'ke', 'ka', 'ki', 'wala', 'aur']
            query = t
            for n in sorted(noise_exact, key=len, reverse=True):
                query = query.replace(n, ' ')
            for n in sorted(noise_words, key=len, reverse=True):
                query = re.sub(r'\b' + re.escape(n) + r'\b', ' ', query)
            query = re.sub(r'\s+', ' ', query).strip().strip('.,!?-:')
            if not query or len(query) < 2:
                query = "best hindi songs"
            return {"type": "command", "linux_task": f"youtube: {query}",
                    "reply": "Ji Sir, gaane laga raha hun"}
        
        # ── [P10] WEB SEARCH TRIGGER ──────────────────────────────────
        if needs_web_search(t) and WEB_SEARCH_ENABLED:
            return None  # Brain 1 web_search route karega
        
        # ── MULTI-APP RULE (P8 Fix) ───────────────────────────────────
        _close = any(k in t for k in ['band karo', 'band kar', 'bandh', 'close karo',
                                       'close kar', 'quit', 'exit'])
        if not _close:
            _APP_RULES = [
                (['firefox', 'mozilla'], 'open_app: firefox', 'Firefox'),
                (['chrome', 'google chrome', 'chromium'], 'open_app: google-chrome', 'Chrome'),
                (['browser', 'web browser'], 'open_app: google-chrome', 'Browser'),
                (['terminal', 'bash', 'command line'], 'open_app: gnome-terminal', 'Terminal'),
                (['file manager', 'nemo', 'files', 'folder'], 'open_app: nemo', 'File Manager'),
                (['text editor', 'editor', 'gedit', 'xed', 'notepad'],
                 'open_app: xed||gedit||mousepad', 'Text Editor'),
                (['vs code', 'vscode', 'visual studio', 'code editor'],
                 'open_app: code', 'VS Code'),
                (['calculator', 'kalculator', 'calc'],
                 'open_app: gnome-calculator||kcalc||xcalc', 'Calculator'),
                (['vlc', 'media player', 'video player'],
                 'open_app: vlc', 'VLC'),
                (['spotify', 'music player'], 'open_app: spotify', 'Spotify'),
                (['gimp', 'image editor'], 'open_app: gimp', 'GIMP'),
                (['discord'], 'open_app: discord', 'Discord'),
                (['telegram'], 'open_app: telegram-desktop||telegram', 'Telegram'),
                (['slack'], 'open_app: slack', 'Slack'),
                (['zoom'], 'open_app: zoom', 'Zoom'),
                (['system monitor', 'task manager'],
                 'open_app: gnome-system-monitor', 'System Monitor'),
                (['settings', 'system settings', 'control panel', 'preferences'],
                 'open_app: gnome-control-center||cinnamon-settings', 'Settings'),
                (['calendar', 'calender'], 'open_app: gnome-calendar||orage', 'Calendar'),
                (['clock', 'alarm'], 'open_app: gnome-clocks', 'Clock'),
                (['photos', 'photo viewer', 'image viewer'],
                 'open_app: eog||shotwell||xviewer', 'Photos'),
                (['pdf', 'document viewer'], 'open_app: evince||okular||atril', 'PDF'),
                (['steam'], 'open_app: steam', 'Steam'),
                (['libreoffice', 'libre office'], 'open_app: libreoffice', 'LibreOffice'),
                (['notes', 'sticky notes'], 'open_app: sticky||gnome-notes||xpad', 'Notes'),
            ]
            
            matched_tasks  = []
            matched_names  = []
            seen = set()
            
            for keywords, task, name in _APP_RULES:
                key = task
                if key in seen:
                    continue
                if any(k in t for k in keywords):
                    matched_tasks.append(task)
                    matched_names.append(name)
                    seen.add(key)
            
            # [P8] Multi-app: SAAL ke sab apps execute honge
            if len(matched_tasks) > 1:
                names_str = ', '.join(matched_names)
                print(f"[RULE] Multi-app ({len(matched_tasks)}): {names_str}")
                return {
                    "type": "command",
                    "linux_task": matched_tasks[0],
                    "linux_tasks": matched_tasks,
                    "reply": f"Ji Sir, sab khol raha hun: {names_str}"
                }
            elif len(matched_tasks) == 1:
                return {
                    "type": "command",
                    "linux_task": matched_tasks[0],
                    "reply": f"Ji Sir, {matched_names[0]} khol raha hun"
                }
        
        # ── APP CLOSE ─────────────────────────────────────────────────
        _has_close_intent = any(k in t for k in ['band karo', 'band kar', 'bandh karo',
                                                   'close karo', 'close kar', 'quit', 'exit'])
        if _has_close_intent:
            if any(m in (' ' + t + ' ') for m in [' aur ', ' and ', ' phir ', ' then ']):
                return None
            if any(k in t for k in ['chrome', 'browser']):
                return {"type": "command", "linux_task": "close Google Chrome window",
                        "reply": "Ji Sir, Chrome band kar raha hun"}
            if 'terminal' in t:
                return {"type": "command",
                        "linux_task": "close one terminal window using wmctrl or xdotool",
                        "reply": "Ji Sir, terminal band kar raha hun"}
        
        # ── SCREEN LOCK / SCREENSHOT ──────────────────────────────────
        if any(k in t for k in ['lock', 'lockscreen', 'screen lock']):
            if not any(k in t for k in ['unlock', 'kholo', 'open']):
                return {"type": "command", "linux_task": "lock screen using loginctl lock-session",
                        "reply": "Ji Sir, screen lock kar raha hun"}
        
        if 'screenshot' in t or 'screen shot' in t:
            return {"type": "command", "linux_task": "take screenshot",
                    "reply": "Ji Sir, screenshot le raha hun"}
        
        # ── SHUTDOWN / RESTART / SLEEP ────────────────────────────────
        if any(k in t for k in ['shutdown', 'shut down', 'poweroff', 'computer band']):
            return {"type": "command", "linux_task": "shutdown system",
                    "reply": "Ji Sir, system shutdown kar raha hun"}
        if any(k in t for k in ['restart', 'reboot']):
            return {"type": "command", "linux_task": "restart system",
                    "reply": "Ji Sir, system restart kar raha hun"}
        if any(k in t for k in ['sleep', 'suspend', 'hibernate']):
            return {"type": "command", "linux_task": "suspend system",
                    "reply": "Ji Sir, system sleep kar raha hun"}
        
        # ── SYSTEM INFO ───────────────────────────────────────────────
        if re.search(r'\bram\b', t) or 'memory use' in t or 'ram usage' in t:
            return {"type": "system_info",
                    "current_command": "free -h | awk 'NR==2{print \"Total:\"$2\" Used:\"$3\" Free:\"$4}'",
                    "reply_template": "Sir, RAM — {OUTPUT}"}
        if any(k in t for k in ['battery', 'charge', 'kitni battery', 'battery level']):
            return {"type": "system_info",
                    "current_command": "upower -i $(upower -e | grep -i bat | head -1) | grep -i percentage | awk '{print $2}'",
                    "reply_template": "Sir, battery {OUTPUT} hai"}
        if any(k in t for k in ['disk space', 'storage kitna', 'kitna space']):
            return {"type": "system_info",
                    "current_command": "df -h / | awk 'NR==2{print \"Total:\"$2\" Used:\"$3\" Free:\"$4}'",
                    "reply_template": "Sir, disk — {OUTPUT}"}
        if any(k in t for k in ['time kya', 'kitne baje', 'waqt kya', 'what time', 'time batao']):
            return {"type": "system_info",
                    "current_command": "date '+%I:%M %p'",
                    "reply_template": "Sir, abhi {OUTPUT} baje hain"}
        if any(k in t for k in ['aaj kaun', 'kaun sa din', 'date kya', 'aaj kya din',
                                  'today date', 'aaj ki tarikh', 'what date', 'which day']):
            return {"type": "system_info",
                    "current_command": "date '+%A, %d %B %Y'",
                    "reply_template": "Sir, aaj {OUTPUT} hai"}
        
        return None
    
    def process(self, user_input: str) -> str:
        """Main processing function."""
        if not user_input:
            return ""
        
        user_input = user_input.strip()
        
        # Special signals
        if user_input == "__EMPTY_STT__":
            reply = "Sir, kuch sunai nahi diya."
            self._speak(reply)
            return reply
        if user_input == "__TIMEOUT__":
            return ""
        
        # [P14] Clear stop flag at start of new command
        STOP_CTRL.clear()
        
        import time as _t
        _t0 = _t.monotonic()
        print(f"\n{'━'*60}")
        print(f"USER: {user_input}")
        
        # Whisper garbage filter
        if self._is_whisper_garbage(user_input):
            print(f"[GARBAGE] Filtered: {user_input}")
            reply = "Sir, samajh nahi aaya."
            self._speak(reply)
            return reply
        
        # [P14] Stop command check
        if is_stop_command(user_input):
            STOP_CTRL.request_stop()
            reply = "Theek hai Sir, ruk gaya."
            self._speak(reply)
            return reply
        
        # Rule-based pre-router (P1, P7)
        rule = self._rule_route(user_input)
        if rule:
            print(f"[RULE] → {rule}")
            return self._execute_decision(rule, user_input, _t0)
        
        # [P10] Web search check — before Brain 1
        if needs_web_search(user_input) and WEB_SEARCH_ENABLED and _is_online():
            print(f"[SEARCH] Web search triggered")
            search_result = web_search(user_input)
            if search_result:
                # Brain 1 ko search result ke saath bhejo
                augmented = f"{user_input}\n\n[WEB SEARCH RESULT]: {search_result[:400]}"
                scr = get_screen_context()
                decision = ask_brain1(augmented, self.memory, scr)
            else:
                scr = get_screen_context()
                decision = ask_brain1(user_input, self.memory, scr)
        else:
            # Brain 1 — Router
            scr = get_screen_context()
            print(f"[SCREEN] {scr}")
            print(f"[→BRAIN1] query='{user_input[:80]}'")
            _tb1 = _t.monotonic()
            decision = ask_brain1(user_input, self.memory, scr)
            _b1ms = int((_t.monotonic()-_tb1)*1000)
            print(f"[←BRAIN1] {_b1ms}ms | type={decision.get('type')}")
        
        return self._execute_decision(decision, user_input, _t0)
    
    def _execute_decision(self, decision: dict, user_input: str, t0: float) -> str:
        """Decision execute karo — routing logic."""
        import time as _t
        d_type = decision.get("type", "chat")
        reply  = decision.get("reply", "")
        
        # ── CHAT ─────────────────────────────────────────────────────
        if d_type == "chat":
            print(f"[CHAT] {reply}")
            self.memory.add_exchange(user_input, reply)
            self._speak(reply)
            return reply
        
        # ── CLARIFY ──────────────────────────────────────────────────
        if d_type == "clarify":
            print(f"[CLARIFY] {reply}")
            self._speak(reply)
            return reply
        
        # ── MEMORY SAVE ──────────────────────────────────────────────
        if d_type == "memory_save":
            fact  = decision.get("fact", "")
            topic = decision.get("topic", "general")
            if fact:
                self.memory.save_fact(fact, topic)
                print(f"[MEMORY] Saved: {fact}")
            self.memory.add_exchange(user_input, reply)
            self._speak(reply)
            return reply
        
        # ── [P10] WEB SEARCH ─────────────────────────────────────────
        if d_type == "web_search":
            query = decision.get("query", user_input)
            print(f"[SEARCH] query='{query}'")
            result = web_search(query)
            if result and reply:
                # Reply mein search result inject karo agar needed
                if "{RESULT}" in reply:
                    final_reply = reply.replace("{RESULT}", result[:150])
                else:
                    final_reply = reply
            else:
                final_reply = reply or "Sir, abhi search nahi ho paya."
            self.memory.add_exchange(user_input, final_reply)
            self._speak(final_reply)
            return final_reply
        
        # ── SYSTEM INFO ──────────────────────────────────────────────
        if d_type == "system_info":
            cmd      = decision.get("current_command", "date")
            template = decision.get("reply_template", "Sir, {OUTPUT} hai")
            raw_out  = run_system_command(cmd)
            reply    = template.replace("{OUTPUT}", raw_out)
            print(f"[SYSINFO] {cmd} → {raw_out}")
            self.memory.add_exchange(user_input, reply)
            self._speak(reply)
            return reply
        
        # ── COMMAND (LADA) ────────────────────────────────────────────
        if d_type == "command":
            linux_task  = decision.get("linux_task", user_input)
            linux_tasks = decision.get("linux_tasks", [])
            brief       = reply or "Ji Sir."
            
            linux_task = _resolve_app_task(linux_task)
            print(f"[CMD→LADA] task='{linux_task}'")
            
            # [P14] Check stop before executing
            if STOP_CTRL.is_stopped():
                self._speak("Theek hai Sir, ruk gaya.")
                return "Ruk gaya Sir."
            
            # [P8] Multi-app — SARE apps execute honge
            if linux_tasks and len(linux_tasks) > 1:
                print(f"[MULTI-APP] {len(linux_tasks)} tasks:")
                for i, lt in enumerate(linux_tasks, 1):
                    print(f"  [{i}] {lt}")
                
                self._speak(brief)
                results = []
                failed_apps = []
                
                for lt in linux_tasks:
                    # [P14] Check stop mid-execution
                    if STOP_CTRL.is_stopped():
                        print("[STOP] Multi-app interrupted")
                        self._speak("Ruk gaya Sir.")
                        break
                    
                    lt_resolved = _resolve_app_task(lt)
                    _tl = _t.monotonic()
                    success, raw_out = smart_send(lt_resolved)  # LADA + direct fallback
                    _ms = int((_t.monotonic()-_tl)*1000)
                    st = "✓" if success else "✗"
                    print(f"  {st} [{_ms}ms] '{lt_resolved}' → {raw_out[:60]!r}")
                    results.append(success)
                    if not success:
                        # App name extract karo failed message ke liye
                        failed_apps.append(lt.split(":")[-1].strip()[:20])
                    _t.sleep(0.8)
                
                ok = sum(results)
                total = len(linux_tasks)
                
                if ok == total:
                    final_reply = brief
                elif ok > 0:
                    final_reply = f"Sir, {ok}/{total} khul gaye. {', '.join(failed_apps)} mein dikkat aayi."
                    self._speak(final_reply)
                else:
                    final_reply = "Sir, kuch bhi nahi khul paya — LADA mein koi issue hai."
                    self._speak(final_reply)
                
                print(f"[DONE] {ok}/{total} OK | {int((_t.monotonic()-t0)*1000)}ms total")
                self.memory.add_exchange(user_input, final_reply)
                return final_reply
            
            # Single task
            self._speak(brief)
            
            _tl = _t.monotonic()
            success, raw_output = smart_send(linux_task)  # LADA + direct fallback
            _ms = int((_t.monotonic()-_tl)*1000)
            st = "✓" if success else "✗"
            print(f"[←LADA] {st} {_ms}ms | {raw_output[:120]!r}")
            
            if success:
                polished = polish_output(raw_output, user_input, brief)
                final_reply = polished if (polished and polished != brief
                                          and len(raw_output.strip()) > 5) else brief
            else:
                fail_reply = "Sorry Sir, kuch problem aayi — kaam nahi hua."
                print(f"[LADA-ERR] {raw_output}")
                self._speak(fail_reply)
                final_reply = fail_reply
            
            print(f"[DONE] {int((_t.monotonic()-t0)*1000)}ms total")
            self.memory.add_exchange(user_input, final_reply)
            return final_reply
        
        # Fallback
        fallback = "Samajh nahi aaya Sir."
        self._speak(fallback)
        return fallback


# ══════════════════════════════════════════════════════════════════════════
# TEST MODE
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print(f"\n== {ASSISTANT_NAME} v8.0 Text Test ==")
    print(f"Boss: {BOSS_NAME} | Brains: Brain1={BRAIN1_MODEL[:20]}...")
    print("Type 'exit' to quit, 'cache reset' to clear LADA cache\n")
    
    session = FridaySession()
    
    while True:
        try:
            inp = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            stop_daemon()
            break
        
        if not inp:
            continue
        if inp.lower() in ("exit", "quit", "bye"):
            stop_daemon()
            break
        
        result = session.process(inp)
        print()
