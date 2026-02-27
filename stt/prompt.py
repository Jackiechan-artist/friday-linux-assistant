"""
FRIDAY Prompt Config
System prompt is defined inside friday_brain.py (FRIDAY_SYSTEM).
BOSS_NAME and ASSISTANT_NAME are loaded from .env at runtime.
"""
import os

BOSS_NAME      = os.environ.get("BOSS_NAME", "Sir")
ASSISTANT_NAME = os.environ.get("ASSISTANT_NAME", "FRIDAY")
