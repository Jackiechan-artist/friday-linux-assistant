"""
LADA - Logger Utility
Structured, color-coded terminal + file logging.
"""

import logging
import sys
from pathlib import Path
from datetime import datetime

LOG_DIR = Path(__file__).parent.parent / "memory" / "logs"

# ANSI color codes
COLORS = {
    "DEBUG":    "\033[36m",   # Cyan
    "INFO":     "\033[32m",   # Green
    "WARNING":  "\033[33m",   # Yellow
    "ERROR":    "\033[31m",   # Red
    "CRITICAL": "\033[35m",   # Magenta
    "RESET":    "\033[0m",
}


class ColorFormatter(logging.Formatter):
    """Color-coded terminal formatter."""

    FMT = "[{levelname:<8}] [{name}] {message}"

    def format(self, record):
        color = COLORS.get(record.levelname, COLORS["RESET"])
        reset = COLORS["RESET"]
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S.%f")[:-3]
        level = f"{color}{record.levelname:<8}{reset}"
        msg = f"[{ts}] [{level}] [{record.name}] {record.getMessage()}"

        if record.exc_info:
            msg += "\n" + self.formatException(record.exc_info)

        return msg


class PlainFormatter(logging.Formatter):
    """Plain formatter for file output."""
    def format(self, record):
        ts = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        msg = f"[{ts}] [{record.levelname:<8}] [{record.name}] {record.getMessage()}"
        if record.exc_info:
            msg += "\n" + self.formatException(record.exc_info)
        return msg


class LADALogger:
    """
    Creates a configured logger for a LADA component.
    Outputs to console (colored) and rotating log file.
    """

    _initialized_loggers = set()

    def __init__(self, name: str, level: str = "INFO"):
        self._logger = logging.getLogger(f"LADA.{name}")

        if name not in self._initialized_loggers:
            self._setup(level)
            self._initialized_loggers.add(name)

    def _setup(self, level_str: str):
        """Set up handlers for this logger."""
        level = getattr(logging, level_str.upper(), logging.INFO)
        self._logger.setLevel(level)

        # Avoid adding duplicate handlers
        if self._logger.handlers:
            return

        # ── Console handler ──
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(ColorFormatter())
        self._logger.addHandler(console_handler)

        # ── File handler ──
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            log_file = LOG_DIR / f"lada_{datetime.now().strftime('%Y%m%d')}.log"
            file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(PlainFormatter())
            self._logger.addHandler(file_handler)
        except Exception:
            pass  # Continue without file logging

        # Don't propagate to root logger
        self._logger.propagate = False

    def debug(self, msg: str, *args, **kwargs):
        self._logger.debug(msg, *args, **kwargs)

    def info(self, msg: str, *args, **kwargs):
        self._logger.info(msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs):
        self._logger.warning(msg, *args, **kwargs)

    def error(self, msg: str, *args, **kwargs):
        self._logger.error(msg, *args, **kwargs)

    def critical(self, msg: str, *args, **kwargs):
        self._logger.critical(msg, *args, **kwargs)

    def set_level(self, level: str):
        """Change logging level at runtime."""
        self._logger.setLevel(getattr(logging, level.upper(), logging.INFO))

# Alias for LASA compatibility
def get_logger(name: str, level: str = "INFO"):
    return LADALogger(name, level)
