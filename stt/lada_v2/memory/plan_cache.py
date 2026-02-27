"""
LADA - PlanCache (Self-Learning Layer)

Kya karta hai:
  - Har successful AI plan ko SQLite mein save karta hai
  - Agali baar same ya similar command aaye toh AI call nahi karta
  - Similarity matching: "open chrome" == "chrome kholo" == "chrome open karo"
  - Safe learning: sirf successful plans save hote hain, failed plans nahi
  - Command normalization: Hindi/Hinglish/English sab ek tarah treat karta hai

Flow:
  User command → normalize → similarity check →
    HIT:  cached plan return (0ms, no API)
    MISS: AI call → plan → validate → cache save → return

Usage in planner.py:
  cache = PlanCache()
  plan = cache.get("open chrome")        # None if miss
  cache.save("open chrome", plan_dict)   # save after success
"""

import json
import sqlite3
import time
import re
from pathlib import Path
from typing import Optional
from utils.logger import LADALogger

logger = LADALogger("PLAN_CACHE")

CACHE_DB = Path(__file__).parent / "plan_cache.db"

# ── Hindi/Hinglish normalization map ─────────────────────────
# Yeh words synonyms hain — normalize karke same key banate hain
SYNONYM_MAP = {
    # Open commands
    "kholo": "open", "kholna": "open", "open karo": "open",
    "chalo": "open", "shuru karo": "open", "start karo": "open",
    "launch karo": "open", "chalao": "open",

    # Close commands
    "band karo": "close", "band kar do": "close", "close karo": "close",
    "hatao": "close", "bند": "close",

    # Delete commands
    "delete karo": "delete", "delete kar do": "delete",
    "mita do": "delete", "hata do": "delete", "remove karo": "delete",

    # Create/write commands
    "banao": "create", "bana do": "create", "create karo": "create",
    "likho": "write", "type karo": "write", "likh do": "write",

    # Save commands
    "save karo": "save", "save kar do": "save", "store karo": "save",

    # Search commands
    "search karo": "search", "dhundo": "search", "khojo": "search",
    "search kar": "search",

    # Play commands
    "play karo": "play", "chalao": "play", "sunao": "play",
    "bajao": "play",

    # App names
    "chrome": "chrome", "google chrome": "chrome", "browser": "chrome",
    "file manager": "files", "nemo": "files",
    "terminal": "terminal", "bash": "terminal",

    # Common words to strip (noise)
    "mera": "", "meri": "", "mujhe": "", "dekstop": "desktop",
    "par": "", "ko": "", "ka": "", "ki": "", "ke": "",
    "ek": "", "aur": "and", "or": "and", "ha": "", "hai": "",
    "karo": "", "kar": "", "do": "", "de": "",
    "usko": "", "usse": "", "usme": "",
    "naam": "name", "naam ki": "", "naam ka": "",
}


class PlanCache:
    """
    Smart plan cache with fuzzy command matching.
    Sirf verified successful plans store karta hai.
    """

    def __init__(self, similarity_threshold: float = 0.72):
        self.threshold = similarity_threshold
        self._init_db()
        self._mem_cache: dict = {}
        self._load_to_memory()
        # Clean up any wrong generic plans from previous sessions
        purged = self.purge_generic_plans()
        if purged:
            logger.info(f"Startup purge: removed {purged} wrong cached plans")

    # ──────────────────────────────────────────────────────────
    # PUBLIC API
    # ──────────────────────────────────────────────────────────

    def get(self, command: str) -> Optional[dict]:
        """
        Find a cached plan for this command.
        Returns plan dict if found, None if cache miss.
        """
        normalized = self._normalize(command)
        
        # 1. Exact match (fastest)
        if normalized in self._mem_cache:
            entry = self._mem_cache[normalized]
            logger.info(f"Cache HIT (exact): '{command}' → task={entry['plan'].get('task','?')}")
            self._bump_hit(normalized)
            return entry["plan"]

        # 2. Fuzzy similarity match
        best_key, best_score = self._best_match(normalized)
        if best_score >= self.threshold:
            entry = self._mem_cache[best_key]
            logger.info(
                f"Cache HIT (fuzzy {best_score:.2f}): '{command}' "
                f"≈ '{entry['original']}' → task={entry['plan'].get('task','?')}"
            )
            self._bump_hit(best_key)
            return entry["plan"]

        logger.info(f"Cache MISS: '{command}' (best score={best_score:.2f})")
        return None

    def save(self, command: str, plan: dict, verified: bool = True) -> None:
        """
        Save a successful plan to cache.
        Only saves if plan has steps and task name.
        Never saves empty or error plans.
        Never saves generic plans for specific commands (prevents wrong cache hits).
        """
        if not plan or not plan.get("steps") or not plan.get("task"):
            logger.debug(f"Not caching invalid plan for '{command}'")
            return

        # Safety check: don't cache plans with suspicious commands
        if self._is_unsafe(plan):
            logger.warning(f"Not caching potentially unsafe plan for '{command}'")
            return

        # Specificity check: don't cache a generic fallback for a specific command
        # Example: "chrome par youtube search karo" → open_browser is WRONG to cache
        if self._is_generic_plan_for_specific_command(command, plan):
            logger.warning(
                f"Not caching: generic plan '{plan.get('task')}' "
                f"for specific command '{command[:50]}'"
            )
            return

        normalized = self._normalize(command)
        entry = {
            "original":  command,
            "normalized": normalized,
            "plan":      plan,
            "hits":      0,
            "saved_at":  time.time(),
            "verified":  verified,
        }
        self._mem_cache[normalized] = entry
        self._save_to_db(entry)
        logger.info(f"Cached plan: '{command}' → task={plan.get('task')}")

    def _is_generic_plan_for_specific_command(self, command: str, plan: dict) -> bool:
        """
        Detect when a generic fallback plan is being saved for a specific command.
        Example bad cache: "youtube search karo" → open_browser
        Example good cache: "open chrome" → open_browser
        """
        cmd = command.lower()
        task = plan.get("task", "").lower()
        steps = plan.get("steps", [])
        step_actions = [s.get("action", "") for s in steps]

        # Generic tasks that should only be cached for simple/matching commands
        GENERIC_TASKS = {"open_browser", "open_file_manager", "open_terminal"}

        if task not in GENERIC_TASKS:
            return False  # specific task, fine to cache

        # If command has keywords that imply MORE than just opening the app,
        # don't cache the generic open_browser plan
        SPECIFIC_KEYWORDS = [
            "youtube", "search", "play", "video", "song", "gana",
            "tab", "new tab", "incognito", "download", "navigate",
            "google", "facebook", "gmail", "maps", "translate",
            "copy", "paste", "screenshot", "history", "bookmark",
            # Hindi specific
            "par jao", "mein jao", "kholo aur", "search karo",
        ]
        cmd_has_specific = any(k in cmd for k in SPECIFIC_KEYWORDS)

        # Simple command like "open chrome" or "chrome kholo" → fine to cache as open_browser
        simple_open = any(k in cmd for k in ["open chrome", "chrome kholo", "chrome open", "chrome chalao"])

        if cmd_has_specific and not simple_open:
            return True  # block: this is too specific for a generic plan

        return False

    def invalidate(self, command: str) -> None:
        """Remove a cached plan (call when a plan fails at runtime)."""
        normalized = self._normalize(command)
        if normalized in self._mem_cache:
            del self._mem_cache[normalized]
            self._delete_from_db(normalized)
            logger.info(f"Cache invalidated: '{command}'")

    def purge_broken_plans(self) -> int:
        """
        FIX v7.1: Startup pe broken plans saaf karo.
        open_menu/search/click_result approach — ye AT-SPI menu search hai
        jo unreliable hai. In plans ko cache se hata do permanently.
        """
        BROKEN_ACTIONS = {"open_menu", "search", "click_result",
                          "simulate_key_press", "execute_command"}
        to_delete = []
        for normalized, entry in list(self._mem_cache.items()):
            plan = entry["plan"]
            steps = plan.get("steps", [])
            actions = {s.get("action", "") for s in steps}
            if actions & BROKEN_ACTIONS:
                to_delete.append(normalized)
                logger.warning(
                    f"Purging broken plan: '{entry['original']}' → {actions & BROKEN_ACTIONS}"
                )

        for key in to_delete:
            original = self._mem_cache[key]["original"]
            del self._mem_cache[key]
            self._delete_from_db(key)

        if to_delete:
            logger.info(f"Purged {len(to_delete)} broken plans from cache")
        return len(to_delete)

    def purge_generic_plans(self) -> int:
        """
        Remove wrongly cached generic plans from DB and memory.
        Call this once at startup to clean old bad entries.
        Returns count of purged entries.
        """
        to_delete = []
        for normalized, entry in list(self._mem_cache.items()):
            if self._is_generic_plan_for_specific_command(
                entry["original"], entry["plan"]
            ):
                to_delete.append(normalized)

        for key in to_delete:
            original = self._mem_cache[key]["original"]
            del self._mem_cache[key]
            self._delete_from_db(key)
            logger.info(f"Purged wrong cache: '{original}'")

        if to_delete:
            logger.info(f"Purged {len(to_delete)} wrong generic plans from cache")
        return len(to_delete)
        """Return cache statistics."""
        total = len(self._mem_cache)
        total_hits = sum(e["hits"] for e in self._mem_cache.values())
        return {
            "cached_plans": total,
            "total_hits":   total_hits,
            "top_commands": sorted(
                [(e["original"], e["hits"]) for e in self._mem_cache.values()],
                key=lambda x: x[1], reverse=True
            )[:5],
        }

    def stats(self) -> dict:
        """Return cache statistics as a dictionary."""
        total_hits = sum(e["hits"] for e in self._mem_cache.values())
        top_commands = sorted(
            [(e["original"], e["hits"]) for e in self._mem_cache.values()],
            key=lambda x: x[1],
            reverse=True,
        )[:5]
        return {
            "cached_plans": len(self._mem_cache),
            "total_hits": total_hits,
            "top_commands": top_commands,
        }

    def show_stats(self) -> str:
        s = self.stats()
        lines = [
            f"📚 Plan Cache: {s['cached_plans']} plans stored",
            f"   Total cache hits: {s['total_hits']}",
            "   Top commands:",
        ]
        for cmd, hits in s["top_commands"]:
            lines.append(f"     • '{cmd}' — used {hits}x from cache")
        return "\n".join(lines)

    # ──────────────────────────────────────────────────────────
    # NORMALIZATION
    # ──────────────────────────────────────────────────────────

    def _normalize(self, command: str) -> str:
        """
        Normalize command to a canonical form.
        'Chrome kholo' → 'open chrome'
        'mera desktop par chrome open karo' → 'open chrome'
        """
        cmd = command.lower().strip()

        # Apply synonym map (longest match first)
        sorted_syns = sorted(SYNONYM_MAP.keys(), key=len, reverse=True)
        for syn in sorted_syns:
            replacement = SYNONYM_MAP[syn]
            cmd = cmd.replace(syn, f" {replacement} ")

        # Remove punctuation, extra spaces
        cmd = re.sub(r"[^\w\s]", " ", cmd)
        cmd = re.sub(r"\s+", " ", cmd).strip()

        # Remove single chars that are noise
        tokens = [t for t in cmd.split() if len(t) > 1]
        return " ".join(tokens)

    # ──────────────────────────────────────────────────────────
    # SIMILARITY
    # ──────────────────────────────────────────────────────────

    def _best_match(self, normalized: str) -> tuple:
        """Find best matching cached command using token overlap."""
        if not self._mem_cache:
            return "", 0.0

        query_tokens = set(normalized.split())
        best_key   = ""
        best_score = 0.0

        for key, entry in self._mem_cache.items():
            cached_tokens = set(key.split())
            score = self._jaccard(query_tokens, cached_tokens)
            if score > best_score:
                best_score = score
                best_key   = key

        return best_key, best_score

    @staticmethod
    def _jaccard(a: set, b: set) -> float:
        """Jaccard similarity: intersection / union."""
        if not a and not b:
            return 1.0
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    # ──────────────────────────────────────────────────────────
    # SAFETY CHECK
    # ──────────────────────────────────────────────────────────

    def _is_unsafe(self, plan: dict) -> bool:
        """
        Block caching of plans with dangerous commands.
        Sirf obviously destructive commands block karo.
        """
        UNSAFE_PATTERNS = [
            "rm -rf", "rm -r /", "dd if=", "mkfs",
            "chmod 777 /", "> /dev/", ":(){ :|:& };:",
            "sudo rm", "shred",
        ]
        plan_str = json.dumps(plan).lower()
        for pattern in UNSAFE_PATTERNS:
            if pattern in plan_str:
                return True
        return False

    # ──────────────────────────────────────────────────────────
    # SQLITE PERSISTENCE
    # ──────────────────────────────────────────────────────────

    def _init_db(self):
        with sqlite3.connect(str(CACHE_DB)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS plan_cache (
                    normalized  TEXT PRIMARY KEY,
                    original    TEXT NOT NULL,
                    plan_json   TEXT NOT NULL,
                    hits        INTEGER DEFAULT 0,
                    saved_at    REAL NOT NULL,
                    verified    INTEGER DEFAULT 1
                )
            """)
            conn.commit()
        # [P5] Purge old plans on init
        self._purge_old_by_age()

    def _purge_old_by_age(self):
        """[P5] Delete plans older than PLAN_CACHE_MAX_AGE_HOURS from .env."""
        try:
            import os as _os
            max_age_h = int(_os.environ.get("PLAN_CACHE_MAX_AGE_HOURS", "24"))
            cutoff = time.time() - (max_age_h * 3600)
            with sqlite3.connect(str(CACHE_DB)) as conn:
                cur = conn.execute("DELETE FROM plan_cache WHERE saved_at < ?", (cutoff,))
                if cur.rowcount > 0:
                    logger.info(f"[P5] Purged {cur.rowcount} old cached plans (>{max_age_h}h)")
                conn.commit()
        except Exception as e:
            logger.warning(f"Cache age purge error: {e}")

    def purge_all(self) -> int:
        """[P5] User command: sab cache saaf karo."""
        try:
            with sqlite3.connect(str(CACHE_DB)) as conn:
                cur = conn.execute("DELETE FROM plan_cache")
                n = cur.rowcount
                conn.commit()
            self._mem_cache.clear()
            logger.info(f"[P5] purge_all: removed {n} plans")
            return n
        except Exception as e:
            logger.warning(f"purge_all error: {e}")
            return 0

    def _load_to_memory(self):
        """Load all cached plans into memory at startup."""
        try:
            with sqlite3.connect(str(CACHE_DB)) as conn:
                rows = conn.execute(
                    "SELECT normalized, original, plan_json, hits, saved_at, verified "
                    "FROM plan_cache ORDER BY hits DESC"
                ).fetchall()
            for row in rows:
                normalized, original, plan_json, hits, saved_at, verified = row
                try:
                    plan = json.loads(plan_json)
                    self._mem_cache[normalized] = {
                        "original":   original,
                        "normalized": normalized,
                        "plan":       plan,
                        "hits":       hits,
                        "saved_at":   saved_at,
                        "verified":   bool(verified),
                    }
                except Exception:
                    pass
            if self._mem_cache:
                logger.info(f"Loaded {len(self._mem_cache)} cached plans from disk")
        except Exception as e:
            logger.warning(f"Cache load error: {e}")

    def _save_to_db(self, entry: dict):
        try:
            with sqlite3.connect(str(CACHE_DB)) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO plan_cache
                    (normalized, original, plan_json, hits, saved_at, verified)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    entry["normalized"],
                    entry["original"],
                    json.dumps(entry["plan"]),
                    entry["hits"],
                    entry["saved_at"],
                    int(entry["verified"]),
                ))
                conn.commit()
        except Exception as e:
            logger.warning(f"Cache save error: {e}")

    def _delete_from_db(self, normalized: str):
        try:
            with sqlite3.connect(str(CACHE_DB)) as conn:
                conn.execute("DELETE FROM plan_cache WHERE normalized = ?", (normalized,))
                conn.commit()
        except Exception as e:
            logger.warning(f"Cache delete error: {e}")

    def _bump_hit(self, normalized: str):
        """Increment hit counter in memory and DB."""
        if normalized in self._mem_cache:
            self._mem_cache[normalized]["hits"] += 1
            hits = self._mem_cache[normalized]["hits"]
            try:
                with sqlite3.connect(str(CACHE_DB)) as conn:
                    conn.execute(
                        "UPDATE plan_cache SET hits = ? WHERE normalized = ?",
                        (hits, normalized)
                    )
                    conn.commit()
            except Exception:
                pass
