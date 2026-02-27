"""
LADA - Watchdog (Heartbeat-Based, v3)

Design:
  Main thread sends heartbeat every N seconds.
  Watchdog checks last heartbeat timestamp.
  If expired → trigger recovery (NOT random kill).

Safe rules:
  ✔ Never kills main thread directly
  ✔ Only sets abort flag — Orchestrator checks and handles cleanly
  ✔ Separate check intervals for: heartbeat, process health, resources
"""

import threading
import time
import subprocess
import os
from typing import Optional, Callable
from utils.logger import LADALogger

logger = LADALogger("WATCHDOG")

# ── Tunable thresholds ─────────────────────────────────────
HEARTBEAT_INTERVAL_S     = 3.0    # main thread must pulse every 3s
HEARTBEAT_TIMEOUT_S      = 15.0   # if no pulse for 15s → frozen
PROCESS_CHECK_INTERVAL_S = 5.0    # how often to check monitored processes
RESOURCE_CHECK_INTERVAL_S = 8.0   # how often to check CPU/mem
CPU_SPIKE_THRESHOLD      = 92.0   # %
MEMORY_LIMIT_MB          = 600    # MB for LADA process


class WatchdogEvent:
    HEARTBEAT_TIMEOUT = "heartbeat_timeout"
    PROCESS_DEAD      = "process_dead"
    WINDOW_CRASH      = "window_crash"
    CPU_SPIKE         = "cpu_spike"
    MEMORY_HIGH       = "memory_high"


class Watchdog:
    """
    Safe background watchdog using heartbeat pattern.

    Usage:
        wd = Watchdog()
        wd.on_alert(my_callback)
        wd.start()

        # In main loop — call this regularly:
        wd.heartbeat()

        wd.stop()
    """

    def __init__(self):
        self._thread           : Optional[threading.Thread] = None
        self._stop_event       = threading.Event()
        self._running          = False

        # ── Heartbeat state ──
        self._last_heartbeat   : float = time.monotonic()
        self._heartbeat_lock   = threading.Lock()
        self._step_label       : str   = ""

        # ── Abort flag (set by watchdog, read by orchestrator) ──
        self.abort_requested   : bool = False
        self.abort_reason      : str  = ""

        # ── Monitored processes ──
        self._watched_procs    : set[str] = set()

        # ── Callbacks ──
        self._alert_cb         : Optional[Callable] = None

        # ── Timing trackers ──
        self._last_proc_check  : float = 0.0
        self._last_res_check   : float = 0.0

        # ── Stats ──
        self.events_count      : int   = 0
        self.last_event        : Optional[str] = None

    # ── Public API ─────────────────────────────────────────

    def start(self, loop=None):
        """Start the watchdog thread."""
        if self._running:
            return
        self._stop_event.clear()
        self.abort_requested = False
        self._running        = True
        self._loop           = loop
        self._last_heartbeat = time.monotonic()
        self._thread = threading.Thread(
            target=self._run,
            name="LADA-Watchdog",
            daemon=True,
        )
        self._thread.start()
        logger.info("Watchdog started (heartbeat mode).")

    def stop(self):
        """Stop the watchdog cleanly."""
        self._running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        logger.debug("Watchdog stopped.")

    def heartbeat(self, step_label: str = ""):
        """
        Called by the main execution loop every step / sub-step.
        Resets the frozen-detection clock.
        """
        with self._heartbeat_lock:
            self._last_heartbeat = time.monotonic()
            if step_label:
                self._step_label = step_label

    def on_alert(self, callback: Callable):
        """Register callback: fn(event_type: str, message: str)"""
        self._alert_cb = callback

    # Aliases kept for backwards compat with Orchestrator
    def on_timeout(self, cb): self.on_alert(cb)
    def on_crash(self, cb):   self.on_alert(cb)

    def watch_process(self, name: str):
        self._watched_procs.add(name)

    def unwatch_process(self, name: str):
        self._watched_procs.discard(name)

    # Called by orchestrator to notify step lifecycle
    def step_started(self, action: str, label: str = ""):
        self.heartbeat(step_label=label or action)

    def step_finished(self):
        self.heartbeat(step_label="")

    # ── Internal loop ──────────────────────────────────────

    def _run(self):
        logger.debug("Watchdog loop active.")
        while not self._stop_event.is_set():
            now = time.monotonic()

            # 1. Heartbeat check (most important)
            self._check_heartbeat(now)

            # 2. Process health (every N seconds)
            if now - self._last_proc_check >= PROCESS_CHECK_INTERVAL_S:
                self._check_processes()
                self._last_proc_check = now

            # 3. Resource check (less frequent)
            if now - self._last_res_check >= RESOURCE_CHECK_INTERVAL_S:
                self._check_resources()
                self._last_res_check = now

            # Sleep in small increments so stop_event is responsive
            self._stop_event.wait(timeout=1.0)

    def _check_heartbeat(self, now: float):
        """Check if main thread has been silent too long."""
        with self._heartbeat_lock:
            age = now - self._last_heartbeat

        if age > HEARTBEAT_TIMEOUT_S:
            msg = (
                f"Heartbeat timeout: {age:.0f}s since last pulse. "
                f"Step: '{self._step_label}'"
            )
            logger.error(f"WATCHDOG: {msg}")
            # Set abort flag — do NOT touch the thread
            self.abort_requested = True
            self.abort_reason    = msg
            self._fire(WatchdogEvent.HEARTBEAT_TIMEOUT, msg)
            # Reset clock so we don't fire every second
            with self._heartbeat_lock:
                self._last_heartbeat = now

    def _check_processes(self):
        """Check monitored processes are still alive."""
        dead = set()
        for proc in list(self._watched_procs):
            try:
                r = subprocess.run(
                    ["pgrep", "-f", proc],
                    capture_output=True, text=True, timeout=2,
                )
                if r.returncode != 0:
                    msg = f"Monitored process died: {proc}"
                    logger.warning(f"WATCHDOG: {msg}")
                    self._fire(WatchdogEvent.PROCESS_DEAD, msg)
                    dead.add(proc)
            except Exception:
                pass
        self._watched_procs -= dead

        # Check for "not responding" windows
        try:
            r = subprocess.run(
                ["wmctrl", "-l"], capture_output=True, text=True, timeout=2,
            )
            if r.returncode == 0 and "(not responding)" in r.stdout.lower():
                msg = "Window 'Not Responding' detected."
                logger.warning(f"WATCHDOG: {msg}")
                self._fire(WatchdogEvent.WINDOW_CRASH, msg)
        except Exception:
            pass

    def _check_resources(self):
        """Check CPU and memory usage. Only WARN — never abort on resource spike."""
        try:
            import psutil
            cpu = psutil.cpu_percent(interval=0.5)
            if cpu > CPU_SPIKE_THRESHOLD:
                # Only log warning — do NOT set abort_requested
                # CPU spikes are normal during app launch etc.
                logger.warning(f"WATCHDOG: CPU spike: {cpu:.0f}% (warning only, not aborting)")

            proc = psutil.Process(os.getpid())
            mem_mb = proc.memory_info().rss / (1024 * 1024)
            if mem_mb > MEMORY_LIMIT_MB:
                logger.warning(f"WATCHDOG: Memory high: {mem_mb:.0f} MB (warning only)")
        except ImportError:
            pass
        except Exception:
            pass

    def _fire(self, event_type: str, message: str):
        self.events_count += 1
        self.last_event    = event_type
        if self._alert_cb:
            try:
                # Call sync — never await from a thread
                import inspect
                if not inspect.iscoroutinefunction(self._alert_cb):
                    self._alert_cb(event_type, message)
                elif self._loop and self._loop.is_running():
                    import asyncio
                    asyncio.run_coroutine_threadsafe(
                        self._alert_cb(event_type, message), self._loop
                    )
            except Exception as e:
                logger.debug(f"Alert callback error: {e}")

    def get_status(self) -> dict:
        with self._heartbeat_lock:
            age = time.monotonic() - self._last_heartbeat
        return {
            "running":           self._running,
            "heartbeat_age_s":   round(age, 1),
            "abort_requested":   self.abort_requested,
            "abort_reason":      self.abort_reason,
            "current_step":      self._step_label,
            "events_count":      self.events_count,
            "last_event":        self.last_event,
            "watched_processes": list(self._watched_procs),
        }
