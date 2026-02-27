"""
LADA - Resource Monitor
Monitors system resources during execution.
Detects: CPU overload, memory pressure, display server issues.
Provides: throttle recommendations, pressure score.
"""

import subprocess
import time
import os
from typing import Optional
from utils.logger import LADALogger

logger = LADALogger("RESOURCE_MON")


class ResourceState:
    """Current system resource snapshot."""
    def __init__(self):
        self.cpu_pct      : float = 0.0
        self.mem_used_mb  : float = 0.0
        self.mem_total_mb : float = 0.0
        self.mem_pct      : float = 0.0
        self.lada_mem_mb  : float = 0.0
        self.display_ok   : bool  = True
        self.x11_ok       : bool  = True
        self.timestamp    : float = time.monotonic()

    @property
    def pressure_score(self) -> float:
        """
        0.0 = no pressure
        1.0 = critical

        Weighted: CPU 40%, memory 40%, display 20%
        """
        cpu_score  = min(self.cpu_pct / 100.0, 1.0) * 0.4
        mem_score  = min(self.mem_pct / 100.0, 1.0) * 0.4
        disp_score = (0.0 if self.display_ok else 1.0) * 0.2
        return cpu_score + mem_score + disp_score

    @property
    def is_high_pressure(self) -> bool:
        return self.pressure_score > 0.7

    @property
    def recommended_delay_s(self) -> float:
        """Extra delay to add to actions under pressure."""
        score = self.pressure_score
        if score < 0.3:
            return 0.0
        if score < 0.5:
            return 0.3
        if score < 0.7:
            return 0.8
        return 1.5

    def to_dict(self) -> dict:
        return {
            "cpu_pct":       round(self.cpu_pct, 1),
            "mem_pct":       round(self.mem_pct, 1),
            "mem_used_mb":   round(self.mem_used_mb, 1),
            "lada_mem_mb":   round(self.lada_mem_mb, 1),
            "display_ok":    self.display_ok,
            "x11_ok":        self.x11_ok,
            "pressure":      round(self.pressure_score, 3),
            "extra_delay_s": self.recommended_delay_s,
        }


class ResourceMonitor:
    """
    Checks system health at key points during execution.
    Not a continuous thread — called on-demand.
    """

    def __init__(self):
        self._last_state: Optional[ResourceState] = None
        self._check_interval_s = 5.0
        self._last_check_t: float = 0.0

    def check(self, force: bool = False) -> ResourceState:
        """
        Run a resource check.
        If called too recently, returns cached state (unless force=True).
        """
        now = time.monotonic()
        if not force and (now - self._last_check_t) < self._check_interval_s:
            return self._last_state or ResourceState()

        state = ResourceState()
        self._check_cpu(state)
        self._check_memory(state)
        self._check_display(state)
        self._last_state = state
        self._last_check_t = now

        if state.is_high_pressure:
            logger.warning(
                f"High resource pressure: {state.pressure_score:.2f} "
                f"(cpu={state.cpu_pct:.0f}% mem={state.mem_pct:.0f}%)"
            )

        return state

    def _check_cpu(self, state: ResourceState):
        try:
            import psutil
            state.cpu_pct = psutil.cpu_percent(interval=0.2)
        except ImportError:
            # Fallback: read /proc/stat
            state.cpu_pct = self._proc_cpu_pct()

    def _check_memory(self, state: ResourceState):
        try:
            import psutil
            vm = psutil.virtual_memory()
            state.mem_total_mb = vm.total / (1024 * 1024)
            state.mem_used_mb  = vm.used  / (1024 * 1024)
            state.mem_pct      = vm.percent
            proc = psutil.Process(os.getpid())
            state.lada_mem_mb  = proc.memory_info().rss / (1024 * 1024)
        except ImportError:
            state.mem_pct = self._proc_mem_pct()

    def _check_display(self, state: ResourceState):
        """Check X11 / display server is still alive."""
        display = os.environ.get("DISPLAY", "")
        if not display:
            state.display_ok = False
            state.x11_ok     = False
            return
        try:
            r = subprocess.run(
                ["xdpyinfo"],
                capture_output=True, timeout=2,
            )
            state.x11_ok    = r.returncode == 0
            state.display_ok = state.x11_ok
        except Exception:
            state.x11_ok    = False
            state.display_ok = False

    def _proc_cpu_pct(self) -> float:
        """Read CPU usage from /proc/stat (no psutil fallback)."""
        try:
            with open("/proc/stat") as f:
                line = f.readline()
            vals  = list(map(int, line.split()[1:]))
            idle  = vals[3]
            total = sum(vals)
            time.sleep(0.1)
            with open("/proc/stat") as f:
                line2 = f.readline()
            vals2  = list(map(int, line2.split()[1:]))
            idle2  = vals2[3]
            total2 = sum(vals2)
            d_total = total2 - total
            d_idle  = idle2  - idle
            if d_total == 0:
                return 0.0
            return 100.0 * (1.0 - d_idle / d_total)
        except Exception:
            return 0.0

    def _proc_mem_pct(self) -> float:
        """Read memory usage from /proc/meminfo (no psutil fallback)."""
        try:
            info = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        info[parts[0].rstrip(":")] = int(parts[1])
            total     = info.get("MemTotal", 1)
            available = info.get("MemAvailable", total)
            return 100.0 * (1.0 - available / total)
        except Exception:
            return 0.0

    def add_pressure_delay(self, base_delay_s: float) -> float:
        """
        Return adjusted delay accounting for current system pressure.
        Use instead of bare asyncio.sleep() in critical paths.
        """
        state = self.check()
        return base_delay_s + state.recommended_delay_s

    def assert_display_alive(self) -> bool:
        """Raise if display server is dead."""
        state = self.check(force=True)
        if not state.display_ok:
            logger.error("Display server is not responding!")
            return False
        return True
