import os
"""
LADA Cache Reset Script
Run this ONCE to clear all old/wrong cached plans.
Usage: python3 reset_cache.py

Ye script lada_v2/memory/ folder mein se plan_cache.db dhundhta hai
aur saare purane galat plans delete karke fresh start karta hai.
"""

import sqlite3
import json
import time
from pathlib import Path

# ─── DB path dhundho ───────────────────────────────────────────────────────
# Commonly: lada_v2/memory/plan_cache.db  ya  ~/.local/share/lada/plan_cache.db
candidates = [
    Path(__file__).parent / "lada_v2" / "memory" / "plan_cache.db",
    Path.home() / "lada_v2" / "memory" / "plan_cache.db",
    Path(os.path.dirname(__file__)) / "memory" / "plan_cache.db",
]

db_path = None
for c in candidates:
    if c.exists():
        db_path = c
        break

if db_path is None:
    # Search more broadly
    import subprocess
    r = subprocess.run(["find", str(Path.home()), "-name", "plan_cache.db", "-type", "f"],
                       capture_output=True, text=True, timeout=10)
    found = r.stdout.strip().splitlines()
    if found:
        db_path = Path(found[0])

if db_path is None:
    print("❌ plan_cache.db nahi mili. Manually path dein:")
    db_path = Path(input("Path: ").strip())

print(f"✅ Cache DB mili: {db_path}")

conn = sqlite3.connect(str(db_path))

# ─── Purane plans dikhao ──────────────────────────────────────────────────
rows = conn.execute("SELECT original, plan_json FROM plan_cache ORDER BY hits DESC").fetchall()
print(f"\n📋 Abhi {len(rows)} plans cached hain:")
for orig, plan_json in rows:
    plan = json.loads(plan_json)
    task = plan.get("task", "?")
    steps = plan.get("steps", [])
    action = steps[0].get("action", "?") if steps else "?"
    print(f"  '{orig}' → task={task}, action={action}")

# ─── Sab delete karo ─────────────────────────────────────────────────────
conn.execute("DELETE FROM plan_cache")
conn.commit()
print(f"\n🗑️  Saare {len(rows)} purane plans delete kiye.")

print("\n✅ Cache reset complete! Ab LADA fresh start karega.")
print("   Pehli baar har command pe thoda time lagega (AI call hogi),")
print("   lekin phir sahi plans cache honge.\n")

conn.close()
