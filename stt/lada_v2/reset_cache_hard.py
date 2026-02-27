"""
LADA Plan Cache Hard Reset
Usage: python3 reset_cache_hard.py

Sab cached plans delete kar do — galat cached plans ki wajah se
wrong actions ho rahe the (e.g., 'sticky &' → open_terminal).
"""
import sqlite3
import os
from pathlib import Path

CACHE_DB = Path(__file__).parent / "memory" / "plan_cache.db"

if CACHE_DB.exists():
    conn = sqlite3.connect(CACHE_DB)
    count = conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0]
    conn.execute("DELETE FROM plans")
    conn.commit()
    conn.close()
    print(f"[RESET] {count} cached plans deleted from {CACHE_DB}")
else:
    print(f"[RESET] Cache DB not found at {CACHE_DB}")

print("[RESET] Done. LADA fresh start karega ab.")
