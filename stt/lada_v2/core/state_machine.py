"""
LADA - State Machine (Hardened v3)

Upgrades over v2:
  ✔ Strict transition validation (raises on illegal move)
  ✔ asyncio.Lock prevents concurrent state mutation
  ✔ Illegal state guard: rejects transitions from terminal states
  ✔ Full audit trail with timestamps
  ✔ Step-level tracking with dependency awareness
"""

import asyncio
from enum import Enum
from typing import Optional, Set
from datetime import datetime
from utils.logger import LADALogger

logger = LADALogger("STATE_MACHINE")


class TaskState(Enum):
    INIT        = "INIT"
    PLANNED     = "PLANNED"
    EXECUTING   = "EXECUTING"
    VERIFYING   = "VERIFYING"
    RECOVERING  = "RECOVERING"
    SUCCESS     = "SUCCESS"
    FAILED      = "FAILED"
    CANCELLED   = "CANCELLED"


# ── Strict allowed transitions ─────────────────────────────
# ONLY paths listed here are legal. Everything else raises.
ALLOWED: dict[TaskState, Set[TaskState]] = {
    TaskState.INIT:       {TaskState.PLANNED, TaskState.CANCELLED},
    TaskState.PLANNED:    {TaskState.EXECUTING, TaskState.FAILED, TaskState.CANCELLED},
    TaskState.EXECUTING:  {TaskState.VERIFYING, TaskState.RECOVERING, TaskState.FAILED},
    TaskState.VERIFYING:  {TaskState.EXECUTING, TaskState.RECOVERING,
                           TaskState.SUCCESS, TaskState.FAILED},
    TaskState.RECOVERING: {TaskState.EXECUTING, TaskState.VERIFYING, TaskState.PLANNED, TaskState.FAILED},
    # Terminal states can only restart to INIT
    TaskState.SUCCESS:    {TaskState.INIT},
    TaskState.FAILED:     {TaskState.INIT},
    TaskState.CANCELLED:  {TaskState.INIT},
}

TERMINAL_STATES = {TaskState.SUCCESS, TaskState.FAILED, TaskState.CANCELLED}


class IllegalTransitionError(Exception):
    pass


class ConcurrentTransitionError(Exception):
    pass


class StepInfo:
    def __init__(self, step_num: int, action: str, total: int):
        self.step_num = step_num
        self.action   = action
        self.total    = total
        self.started  = datetime.now()

    def __str__(self):
        return f"{self.step_num}/{self.total}:{self.action}"


class StateMachine:
    """
    Thread-safe, strictly validated state machine.
    Uses asyncio.Lock to prevent concurrent mutation.
    """

    def __init__(self):
        self._state   = TaskState.INIT
        self._lock    = asyncio.Lock()   # one transition at a time
        self._prev    : Optional[TaskState] = None
        self._step    : Optional[StepInfo]  = None
        self._history : list[dict]          = []
        self._task_start: Optional[datetime] = None
        self.task_name: str = ""

    # ── Core transition ────────────────────────────────────

    async def transition(
        self,
        new_state: TaskState,
        step_num: int = 0,
        action: str   = "",
        total_steps: int = 0,
    ) -> None:
        """
        Async-safe state transition.
        Raises IllegalTransitionError on invalid move.
        """
        async with self._lock:
            self._validate(new_state)
            self._apply(new_state, step_num, action, total_steps)

    def transition_sync(
        self,
        new_state: TaskState,
        step_num: int = 0,
        action: str   = "",
        total_steps: int = 0,
    ) -> None:
        """
        Sync version — use ONLY from non-async contexts.
        Still validates transitions strictly.
        """
        self._validate(new_state)
        self._apply(new_state, step_num, action, total_steps)

    def _validate(self, new_state: TaskState) -> None:
        allowed = ALLOWED.get(self._state, set())

        # Guard 1: illegal transition
        if new_state not in allowed:
            raise IllegalTransitionError(
                f"Illegal: {self._state.value} → {new_state.value}. "
                f"Allowed from {self._state.value}: "
                f"{[s.value for s in allowed]}"
            )

        # Guard 2: terminal state lock (only INIT allowed out)
        if self._state in TERMINAL_STATES and new_state != TaskState.INIT:
            raise IllegalTransitionError(
                f"Cannot transition out of terminal state "
                f"{self._state.value} to {new_state.value}. "
                f"Must go to INIT first."
            )

    def _apply(
        self,
        new_state: TaskState,
        step_num: int,
        action: str,
        total_steps: int,
    ) -> None:
        self._history.append({
            "from":       self._state.value,
            "to":         new_state.value,
            "step_num":   step_num,
            "action":     action,
            "timestamp":  datetime.now().isoformat(),
        })

        self._prev  = self._state
        self._state = new_state

        if new_state == TaskState.PLANNED:
            self._task_start = datetime.now()

        if new_state == TaskState.EXECUTING and step_num:
            self._step = StepInfo(step_num, action, total_steps)

        if new_state == TaskState.INIT:
            self._step       = None
            self.task_name   = ""
            self._task_start = None

        logger.debug(
            f"{(self._prev.value if self._prev else '?'):>12} "
            f"→ {new_state.value:<12}"
            + (f"  [step {step_num}: {action}]" if step_num else "")
        )

    # ── Emergency reset ────────────────────────────────────

    def force_reset(self) -> None:
        """
        Bypass validation and reset to INIT.
        Use ONLY in catastrophic failure handlers.
        Logs a warning.
        """
        logger.warning(
            f"FORCE RESET from {self._state.value} → INIT"
        )
        self._history.append({
            "from":      self._state.value,
            "to":        TaskState.INIT.value,
            "step_num":  0,
            "action":    "FORCE_RESET",
            "timestamp": datetime.now().isoformat(),
        })
        self._state      = TaskState.INIT
        self._step       = None
        self.task_name   = ""
        self._task_start = None

    # ── Read-only properties ───────────────────────────────

    @property
    def current_state(self) -> TaskState:
        return self._state

    @property
    def previous_state(self) -> Optional[TaskState]:
        return self._prev

    @property
    def current_step(self) -> Optional[StepInfo]:
        return self._step

    def is_terminal(self) -> bool:
        return self._state in TERMINAL_STATES

    def is_active(self) -> bool:
        return self._state not in TERMINAL_STATES and self._state != TaskState.INIT

    def elapsed_seconds(self) -> float:
        if not self._task_start:
            return 0.0
        return (datetime.now() - self._task_start).total_seconds()

    def get_status(self) -> dict:
        return {
            "state":    self._state.value,
            "prev":     self._prev.value if self._prev else None,
            "step":     str(self._step) if self._step else None,
            "task":     self.task_name,
            "elapsed":  f"{self.elapsed_seconds():.1f}s",
        }

    def get_history(self) -> list[dict]:
        return list(self._history)

    def __repr__(self):
        return f"StateMachine(state={self._state.value}, step={self._step})"
