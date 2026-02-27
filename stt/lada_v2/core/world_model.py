"""
LADA/LASA - WorldModel
Raw screen data ko AI ke liye simplified abstraction mein convert karta hai.

LLM ko raw AT-SPI ya coordinates kabhi nahi dene — yeh module ek clean
"symbolic world" banata hai jisme AI plan karti hai.

World State Structure:
    {
      "active_window": "Chrome",
      "task_in_progress": "opening youtube",
      "elements": [
        {"id": 1, "name": "New Tab", "role": "button", "clickable": true},
        ...
      ],
      "recent_actions": [...],
      "last_action_outcome": "success" / "failed" / "unexpected",
    }
"""

import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from utils.logger import get_logger

log = get_logger("WORLD_MODEL")


@dataclass
class ActionRecord:
    """Ek action ka record — kya kiya, kya expect tha, kya hua."""
    action:       str
    value:        str        = ""
    expected:     str        = ""      # expected outcome
    observed:     str        = ""      # actual outcome
    success:      bool       = False
    timestamp:    float      = field(default_factory=time.monotonic)

    def to_dict(self) -> dict:
        return {
            "action":   self.action,
            "value":    self.value[:60],
            "expected": self.expected,
            "observed": self.observed,
            "success":  self.success,
        }


@dataclass
class ElementSnapshot:
    """UI element ka lightweight snapshot — stable ID ke saath."""
    id:        int
    name:      str
    role:      str
    cx:        int     = 0
    cy:        int     = 0
    enabled:   bool    = True
    focused:   bool    = False
    value:     str     = ""
    window:    str     = ""

    def is_clickable(self) -> bool:
        return self.enabled and self.role in {
            "button", "push button", "menu item", "check box",
            "radio button", "menu", "link", "tab", "icon",
            "toggle button", "combo box", "list item", "tree item", "cell"
        }

    def to_dict(self) -> dict:
        d = {"id": self.id, "name": self.name, "role": self.role}
        if self.cx or self.cy:
            d["pos"] = f"({self.cx},{self.cy})"
        if not self.enabled:
            d["disabled"] = True
        if self.focused:
            d["focused"] = True
        if self.value:
            d["value"] = self.value[:50]
        return d


class WorldModel:
    """
    AI ka internal map of the world.

    Ye module:
    1. LASA ScreenState ko symbolic state mein convert karta hai
    2. Har action + outcome record karta hai
    3. AI ko clean context provide karta hai
    4. Expected vs actual compare karke mismatch detect karta hai
    """

    def __init__(self, max_history: int = 20):
        self._max_history     = max_history
        self._action_history: List[ActionRecord] = []
        self._element_map:    Dict[int, ElementSnapshot] = {}
        self._next_id:        int = 1

        # Current world state
        self.active_window:   str  = ""
        self.open_windows:    List[str] = []
        self.focused_element: str  = ""
        self.task_goal:       str  = ""
        self.task_status:     str  = "idle"   # idle / in_progress / success / failed
        self.consecutive_failures: int = 0
        self.last_updated:    float = 0.0

    # ──────────────────────────────────────────────
    # UPDATE FROM LASA SCREEN STATE
    # ──────────────────────────────────────────────

    def update_from_screen(self, screen_state) -> None:
        """
        LASA ScreenState se world model update karo.
        Har element ko stable ID assign karo.
        """
        self._element_map.clear()
        self._next_id = 1

        if screen_state is None:
            return

        # Windows
        self.open_windows = [w.title for w in screen_state.windows]
        self.active_window = screen_state.focused_window or (
            self.open_windows[0] if self.open_windows else ""
        )

        # Focused element
        if screen_state.focused_element:
            self.focused_element = screen_state.focused_element.name

        # Elements — assign stable IDs, deduplicate
        seen_names = {}
        for el in screen_state.elements:
            # Skip elements with no useful identity
            if not el.name and not el.is_clickable():
                continue

            # Deduplicate same name+role combinations
            key = f"{el.name}::{el.role}::{el.window}"
            if key in seen_names:
                continue
            seen_names[key] = True

            snap = ElementSnapshot(
                id=self._next_id,
                name=el.name,
                role=el.role,
                cx=el.cx,
                cy=el.cy,
                enabled=el.enabled,
                focused=el.focused,
                value=el.value[:50] if el.value else "",
                window=el.window,
            )
            self._element_map[self._next_id] = snap
            self._next_id += 1

        self.last_updated = time.monotonic()
        log.info(
            f"World updated: window='{self.active_window}' "
            f"elements={len(self._element_map)} "
            f"windows={len(self.open_windows)}"
        )

    def update_from_wmctrl(self) -> None:
        """
        Fallback: sirf window list update karo wmctrl se.
        Used when AT-SPI unavailable.
        """
        import subprocess
        try:
            r = subprocess.run(
                ["wmctrl", "-l"], capture_output=True, text=True, timeout=3
            )
            self.open_windows = []
            for line in r.stdout.strip().split("\n"):
                parts = line.split(None, 3)
                if len(parts) >= 4:
                    self.open_windows.append(parts[3].strip())
            if self.open_windows:
                self.active_window = self.open_windows[0]
        except Exception:
            pass

    # ──────────────────────────────────────────────
    # GOAL & TASK TRACKING
    # ──────────────────────────────────────────────

    def set_goal(self, goal: str, clear_history: bool = True) -> None:
        self.task_goal   = goal
        self.task_status = "in_progress"
        self.consecutive_failures = 0
        if clear_history:
            self._action_history.clear()  # new goal = fresh history
        log.info(f"Goal set: {goal}")

    def mark_success(self) -> None:
        self.task_status = "success"
        self.consecutive_failures = 0

    def mark_failed(self) -> None:
        self.task_status = "failed"

    # ──────────────────────────────────────────────
    # ACTION RECORDING
    # ──────────────────────────────────────────────

    def record_action(
        self,
        action:   str,
        value:    str  = "",
        expected: str  = "",
        observed: str  = "",
        success:  bool = True,
    ) -> None:
        """Record an action and its outcome."""
        rec = ActionRecord(
            action=action,
            value=value,
            expected=expected,
            observed=observed,
            success=success,
        )
        self._action_history.append(rec)
        if len(self._action_history) > self._max_history:
            self._action_history.pop(0)

        if success:
            self.consecutive_failures = 0
        else:
            self.consecutive_failures += 1

        if self.consecutive_failures >= 3:
            log.warning(
                f"3 consecutive failures — may need replanning"
            )

    # ──────────────────────────────────────────────
    # ELEMENT LOOKUP
    # ──────────────────────────────────────────────

    def get_element_by_id(self, element_id: int) -> Optional[ElementSnapshot]:
        return self._element_map.get(element_id)

    def get_element_by_name(self, name: str, role: str = "") -> Optional[ElementSnapshot]:
        name_lower = name.lower()
        role_lower = role.lower()
        for el in self._element_map.values():
            nm = name_lower in el.name.lower()
            rl = (not role_lower) or (role_lower in el.role.lower())
            if nm and rl:
                return el
        return None

    def get_clickable_elements(self) -> List[ElementSnapshot]:
        return [el for el in self._element_map.values() if el.is_clickable()]

    def get_elements_in_window(self, window_title: str) -> List[ElementSnapshot]:
        wt = window_title.lower()
        return [el for el in self._element_map.values()
                if wt in el.window.lower()]

    # ──────────────────────────────────────────────
    # AI CONTEXT GENERATION
    # ──────────────────────────────────────────────

    def to_ai_context(self, max_elements: int = 40) -> str:
        """
        AI ke liye clean text context banao.
        Yeh LADA planner ko bheja jayega.
        """
        lines = []

        # Current state
        lines.append(f"ACTIVE WINDOW: {self.active_window or 'None'}")
        if len(self.open_windows) > 1:
            others = [w for w in self.open_windows if w != self.active_window]
            lines.append(f"OTHER WINDOWS: {', '.join(others[:5])}")

        if self.focused_element:
            lines.append(f"FOCUSED: {self.focused_element}")

        # Goal
        if self.task_goal:
            lines.append(f"CURRENT GOAL: {self.task_goal}")

        # Recent actions (last 5)
        if self._action_history:
            lines.append("\nRECENT ACTIONS:")
            for rec in self._action_history[-5:]:
                status = "✓" if rec.success else "✗"
                lines.append(
                    f"  {status} {rec.action}('{rec.value[:30]}')"
                    + (f" → expected: {rec.expected}" if rec.expected else "")
                    + (f" | observed: {rec.observed}" if rec.observed and not rec.success else "")
                )

        if self.consecutive_failures >= 2:
            lines.append(
                f"\n⚠ WARNING: {self.consecutive_failures} consecutive failures — consider replanning"
            )

        # Elements
        clickable = self.get_clickable_elements()
        lines.append(f"\nCLICKABLE ELEMENTS ({len(clickable)} total):")
        for el in clickable[:max_elements]:
            d = el.to_dict()
            focused_mark = " ◄" if el.focused else ""
            val_str = f" = '{el.value}'" if el.value else ""
            lines.append(
                f"  [{el.id}] {el.role}: '{el.name}'{val_str}{focused_mark}"
            )
        if len(clickable) > max_elements:
            lines.append(f"  ... +{len(clickable)-max_elements} more")

        # Text fields separately (useful for knowing where to type)
        text_fields = [
            el for el in self._element_map.values()
            if el.role in ("text", "entry", "password text", "spin button", "combo box")
        ]
        if text_fields:
            lines.append(f"\nTEXT FIELDS:")
            for tf in text_fields[:10]:
                lines.append(
                    f"  [{tf.id}] '{tf.name}'"
                    + (f" = '{tf.value}'" if tf.value else "")
                    + (" [FOCUSED]" if tf.focused else "")
                )

        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Full state as dict (for logging/debugging)."""
        return {
            "active_window":        self.active_window,
            "open_windows":         self.open_windows,
            "focused_element":      self.focused_element,
            "task_goal":            self.task_goal,
            "task_status":          self.task_status,
            "consecutive_failures": self.consecutive_failures,
            "element_count":        len(self._element_map),
            "recent_actions":       [r.to_dict() for r in self._action_history[-5:]],
        }

    def summary(self) -> str:
        return (
            f"WorldModel["
            f"window='{self.active_window}' "
            f"elements={len(self._element_map)} "
            f"failures={self.consecutive_failures} "
            f"status={self.task_status}]"
        )
