"""
LADA - Context Store (Memory System)
Stores system context, success patterns, error logs.
SQLite for structured data, JSON for config.
"""

import json
import sqlite3
import time
from typing import Optional, Any, List
from pathlib import Path
from utils.logger import LADALogger

logger = LADALogger("CONTEXT_STORE")

# Paths
MEMORY_DIR = Path(__file__).parent
CONTEXT_FILE = MEMORY_DIR / "context_store.json"
LOGS_DB = MEMORY_DIR / "logs.db"


class ContextStore:
    """
    Persistent memory for LADA.
    - JSON for context key-value pairs
    - SQLite for structured logs and patterns
    """

    def __init__(self):
        self._context: dict = {}
        self._load_context()
        self._init_db()

    # ── JSON CONTEXT ──────────────────────────────────────────

    def _load_context(self):
        """Load context from JSON file."""
        try:
            if CONTEXT_FILE.exists():
                with open(CONTEXT_FILE) as f:
                    self._context = json.load(f)
                logger.debug("Context loaded from file.")
        except Exception as e:
            logger.warning(f"Could not load context: {e}")
            self._context = {}

    def _save_context_file(self):
        """Save context to JSON file."""
        try:
            MEMORY_DIR.mkdir(parents=True, exist_ok=True)
            with open(CONTEXT_FILE, "w") as f:
                json.dump(self._context, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save context: {e}")

    def save_context(self, key: str, value: Any):
        """Store a context value."""
        self._context[key] = value
        self._save_context_file()
        logger.debug(f"Context saved: {key}")

    def get_context(self, key: str, default: Any = None) -> Any:
        """Retrieve a context value."""
        return self._context.get(key, default)

    def update_context(self, key: str, updates: dict):
        """Merge updates into an existing dict context."""
        existing = self._context.get(key, {})
        if isinstance(existing, dict):
            existing.update(updates)
        else:
            existing = updates
        self._context[key] = existing
        self._save_context_file()

    def delete_context(self, key: str):
        """Remove a context key."""
        if key in self._context:
            del self._context[key]
            self._save_context_file()

    def get_all_context(self) -> dict:
        """Get complete context dict."""
        return self._context.copy()

    # ── SQLITE LOGS ───────────────────────────────────────────

    def _init_db(self):
        """Initialize SQLite database for structured logging."""
        try:
            MEMORY_DIR.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(LOGS_DB))
            cur = conn.cursor()

            # Task execution log
            cur.execute("""
                CREATE TABLE IF NOT EXISTS task_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    task_name TEXT,
                    command TEXT,
                    status TEXT,
                    duration_seconds REAL,
                    steps_count INTEGER,
                    error_msg TEXT
                )
            """)

            # Successful method patterns
            cur.execute("""
                CREATE TABLE IF NOT EXISTS success_patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    app_name TEXT,
                    action TEXT,
                    method TEXT,
                    success_count INTEGER DEFAULT 1
                )
            """)

            # Error patterns for learning
            cur.execute("""
                CREATE TABLE IF NOT EXISTS error_patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    action TEXT,
                    method TEXT,
                    error_text TEXT,
                    count INTEGER DEFAULT 1
                )
            """)

            # App launch times (for performance tuning)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS app_launch_times (
                    app_name TEXT PRIMARY KEY,
                    avg_launch_seconds REAL,
                    sample_count INTEGER DEFAULT 1,
                    last_updated REAL
                )
            """)

            conn.commit()
            conn.close()
            logger.debug("SQLite database initialized.")

        except Exception as e:
            logger.warning(f"DB init failed: {e}")

    def _get_conn(self) -> sqlite3.Connection:
        """Get SQLite connection."""
        return sqlite3.connect(str(LOGS_DB))

    # ── SUCCESS / FAILURE LOGGING ─────────────────────────────

    def log_success(self, command: str, plan: dict, duration: float = 0):
        """Log a successful task execution."""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO task_log
                    (timestamp, task_name, command, status, duration_seconds, steps_count)
                VALUES (?, ?, ?, 'success', ?, ?)
            """, (
                time.time(),
                plan.get("task", "unknown"),
                command,
                duration,
                len(plan.get("steps", []))
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug(f"log_success error: {e}")

    def log_failure(self, command: str, task_name: str, error: str = "", duration: float = 0):
        """Log a failed task execution."""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO task_log
                    (timestamp, task_name, command, status, duration_seconds, error_msg)
                VALUES (?, ?, ?, 'failed', ?, ?)
            """, (
                time.time(),
                task_name,
                command,
                duration,
                error[:500]
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug(f"log_failure error: {e}")

    def record_success_pattern(self, app_name: str, action: str, method: str):
        """Record that a specific method worked for an action."""
        try:
            conn = self._get_conn()
            cur = conn.cursor()

            # Check if pattern exists
            cur.execute("""
                SELECT id, success_count FROM success_patterns
                WHERE app_name=? AND action=? AND method=?
            """, (app_name, action, method))
            row = cur.fetchone()

            if row:
                cur.execute("""
                    UPDATE success_patterns
                    SET success_count=?, timestamp=?
                    WHERE id=?
                """, (row[1] + 1, time.time(), row[0]))
            else:
                cur.execute("""
                    INSERT INTO success_patterns (timestamp, app_name, action, method)
                    VALUES (?, ?, ?, ?)
                """, (time.time(), app_name, action, method))

            conn.commit()
            conn.close()

        except Exception as e:
            logger.debug(f"record_success_pattern error: {e}")

    def get_best_method(self, app_name: str, action: str) -> Optional[str]:
        """Get the historically most successful method for an action."""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("""
                SELECT method FROM success_patterns
                WHERE app_name=? AND action=?
                ORDER BY success_count DESC
                LIMIT 1
            """, (app_name, action))
            row = cur.fetchone()
            conn.close()
            return row[0] if row else None
        except Exception:
            return None

    def record_error_pattern(self, action: str, method: str, error: str):
        """Record an error pattern for adaptive learning."""
        try:
            conn = self._get_conn()
            cur = conn.cursor()

            error_short = error[:200]
            cur.execute("""
                SELECT id, count FROM error_patterns
                WHERE action=? AND method=? AND error_text=?
            """, (action, method, error_short))
            row = cur.fetchone()

            if row:
                cur.execute(
                    "UPDATE error_patterns SET count=?, timestamp=? WHERE id=?",
                    (row[1] + 1, time.time(), row[0])
                )
            else:
                cur.execute("""
                    INSERT INTO error_patterns (timestamp, action, method, error_text)
                    VALUES (?, ?, ?, ?)
                """, (time.time(), action, method, error_short))

            conn.commit()
            conn.close()

        except Exception as e:
            logger.debug(f"record_error_pattern error: {e}")

    def record_app_launch_time(self, app_name: str, launch_seconds: float):
        """Update rolling average of app launch time."""
        try:
            conn = self._get_conn()
            cur = conn.cursor()

            cur.execute(
                "SELECT avg_launch_seconds, sample_count FROM app_launch_times WHERE app_name=?",
                (app_name,)
            )
            row = cur.fetchone()

            if row:
                avg = row[0]
                n = row[1]
                new_avg = (avg * n + launch_seconds) / (n + 1)
                cur.execute("""
                    UPDATE app_launch_times
                    SET avg_launch_seconds=?, sample_count=?, last_updated=?
                    WHERE app_name=?
                """, (new_avg, n + 1, time.time(), app_name))
            else:
                cur.execute("""
                    INSERT INTO app_launch_times
                        (app_name, avg_launch_seconds, sample_count, last_updated)
                    VALUES (?, ?, 1, ?)
                """, (app_name, launch_seconds, time.time()))

            conn.commit()
            conn.close()

        except Exception as e:
            logger.debug(f"record_app_launch_time error: {e}")

    def get_app_launch_time(self, app_name: str) -> float:
        """Get expected launch time for an app."""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT avg_launch_seconds FROM app_launch_times WHERE app_name=?",
                (app_name,)
            )
            row = cur.fetchone()
            conn.close()
            return row[0] if row else 1.5  # default 1.5s
        except Exception:
            return 1.5

    def get_recent_task_log(self, limit: int = 20) -> List[dict]:
        """Get recent task execution history."""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("""
                SELECT task_name, command, status, duration_seconds, timestamp, error_msg
                FROM task_log
                ORDER BY timestamp DESC
                LIMIT ?
            """, (limit,))
            rows = cur.fetchall()
            conn.close()
            return [
                {
                    "task_name": r[0],
                    "command": r[1],
                    "status": r[2],
                    "duration_seconds": r[3],
                    "timestamp": r[4],
                    "error_msg": r[5]
                }
                for r in rows
            ]
        except Exception:
            return []
