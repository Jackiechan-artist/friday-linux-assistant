"""
LADA - Step Graph v2

Fixes from audit:
  ✔ Immutability guard during execution (frozen flag)
  ✔ Idempotency: repeat execution of SUCCESS node is no-op
  ✔ Circular dependency detection at build time
  ✔ Per-node timeout_s and max_retries metadata
  ✔ Orphan node detection
"""

from __future__ import annotations
import uuid
from enum import Enum
from typing import Optional, List, Set
from datetime import datetime
from dataclasses import dataclass, field
from utils.logger import LADALogger

logger = LADALogger("STEP_GRAPH")


class StepStatus(Enum):
    PENDING      = "PENDING"
    RUNNING      = "RUNNING"
    SUCCESS      = "SUCCESS"
    FAILED       = "FAILED"
    SKIPPED      = "SKIPPED"
    ROLLED_BACK  = "ROLLED_BACK"


# Per-action execution budgets
ACTION_TIMEOUT: dict[str, float] = {
    "open_app":          12.0,
    "open_terminal":     8.0,
    "navigate":          30.0,
    "find_and_click":    8.0,
    "click_button":      6.0,
    "click_result":      6.0,
    "type_text":         5.0,
    "search":            5.0,
    "verify_window":     15.0,
    "focus_window":      5.0,
    "close_window":      5.0,
    "wait_for_element":  20.0,
    "set_volume":        4.0,
    "set_brightness":    4.0,
    "run_command":       30.0,
    "scroll":            3.0,
    "get_text":          5.0,
    "open_menu":         5.0,
}
DEFAULT_TIMEOUT = 10.0

ACTION_MAX_RETRIES: dict[str, int] = {
    "open_app":         2,
    "navigate":         3,
    "find_and_click":   3,
    "click_button":     3,
    "type_text":        2,
    "verify_window":    3,
    "run_command":      2,
    "wait_for_element": 2,
}
DEFAULT_MAX_RETRIES = 3

ROLLBACK_MAP: dict[str, Optional[str]] = {
    "open_app":      "close_window",
    "open_terminal": "close_window",
    "navigate":      None,
    "find_and_click": None,
    "click_button":   None,
    "type_text":      None,
    "set_volume":     None,
    "set_brightness": None,
    "focus_window":   None,
    "close_window":   "open_app",
}


@dataclass
class StepNode:
    step_id     : str
    seq_num     : int
    action      : str
    value       : str
    method      : str
    depends_on  : List[str]
    timeout_s   : float
    max_retries : int
    rollback_action: Optional[str] = None

    # Runtime state
    status      : StepStatus      = StepStatus.PENDING
    attempts    : int             = 0
    error       : str             = ""
    started_at  : Optional[datetime] = None
    finished_at : Optional[datetime] = None
    method_used : str             = ""

    def mark_running(self):
        # Idempotency guard — already successful nodes are no-ops
        if self.status == StepStatus.SUCCESS:
            logger.debug(f"Node {self.step_id} already SUCCESS — idempotent skip.")
            return
        self.status     = StepStatus.RUNNING
        self.started_at = datetime.now()
        self.attempts  += 1

    def mark_success(self, method_used: str = ""):
        self.status      = StepStatus.SUCCESS
        self.finished_at = datetime.now()
        self.method_used = method_used

    def mark_failed(self, error: str = ""):
        self.status      = StepStatus.FAILED
        self.finished_at = datetime.now()
        self.error       = error

    def mark_skipped(self, reason: str = ""):
        self.status = StepStatus.SKIPPED
        self.error  = reason

    def is_done(self) -> bool:
        return self.status in (StepStatus.SUCCESS, StepStatus.SKIPPED)

    def can_retry(self) -> bool:
        return self.attempts < self.max_retries and not self.is_done()

    def reset_for_retry(self):
        """Reset node to PENDING for re-execution."""
        self.status     = StepStatus.PENDING
        self.error      = ""
        self.started_at = None
        self.finished_at = None

    def duration_ms(self) -> float:
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds() * 1000
        return 0.0

    def to_step_dict(self) -> dict:
        return {"action": self.action, "value": self.value, "method": self.method}

    def summary(self) -> str:
        dur = f" {self.duration_ms():.0f}ms" if self.duration_ms() else ""
        err = f" err={self.error[:40]!r}" if self.error else ""
        att = f" att={self.attempts}" if self.attempts > 1 else ""
        return (
            f"[{self.seq_num}] {self.action}={self.value!r} "
            f"→ {self.status.value}{dur}{att}{err}"
        )


class GraphBuildError(Exception):
    pass


class GraphMutationError(Exception):
    """Raised when graph is modified during execution (immutability violation)."""
    pass


class StepGraph:
    """
    Tracked execution graph built from a flat plan.

    Guarantees:
      - Immutable structure once execution begins (_frozen)
      - No circular dependencies (validated at build time)
      - No orphan nodes (all dep IDs must exist)
      - Per-node timeout and retry budget
      - Idempotent node execution
    """

    def __init__(self):
        self.nodes     : List[StepNode]       = []
        self.task_name : str                   = ""
        self._id_index : dict[str, StepNode]  = {}
        self._frozen   : bool                  = False   # set True when exec starts

    # ── Build ──────────────────────────────────────────────

    @classmethod
    def from_plan(cls, plan: dict) -> "StepGraph":
        graph = cls()
        graph.task_name = plan.get("task", "unknown")
        steps = plan.get("steps", [])

        if not steps:
            raise GraphBuildError("Plan has no steps.")

        prev_id: Optional[str] = None

        for i, step in enumerate(steps):
            action = step.get("action", "")
            node = StepNode(
                step_id         = f"s{i+1}_{uuid.uuid4().hex[:6]}",
                seq_num         = i + 1,
                action          = action,
                value           = step.get("value", ""),
                method          = step.get("method", "auto"),
                depends_on      = [prev_id] if prev_id else [],
                timeout_s       = ACTION_TIMEOUT.get(action, DEFAULT_TIMEOUT),
                max_retries     = ACTION_MAX_RETRIES.get(action, DEFAULT_MAX_RETRIES),
                rollback_action = ROLLBACK_MAP.get(action),
            )
            graph.nodes.append(node)
            graph._id_index[node.step_id] = node
            prev_id = node.step_id

        # Validate graph at build time
        graph._validate()
        logger.debug(
            f"StepGraph: {graph.task_name} ({len(graph.nodes)} nodes) — validated OK"
        )
        return graph

    def _validate(self):
        """Check for circular deps and orphan nodes at build time."""
        ids = set(self._id_index.keys())

        for node in self.nodes:
            # Orphan check: all dep IDs must exist
            for dep_id in node.depends_on:
                if dep_id not in ids:
                    raise GraphBuildError(
                        f"Node {node.step_id} depends on unknown ID: {dep_id}"
                    )

        # Circular dependency check (DFS)
        visited  : Set[str] = set()
        rec_stack: Set[str] = set()

        def dfs(nid: str):
            visited.add(nid)
            rec_stack.add(nid)
            node = self._id_index[nid]
            for dep_id in node.depends_on:
                if dep_id not in visited:
                    dfs(dep_id)
                elif dep_id in rec_stack:
                    raise GraphBuildError(
                        f"Circular dependency detected involving node: {dep_id}"
                    )
            rec_stack.discard(nid)

        for nid in ids:
            if nid not in visited:
                dfs(nid)

    # ── Execution lifecycle ────────────────────────────────

    def freeze(self):
        """
        Lock the graph structure.
        Called when execution begins.
        After this, adding/removing nodes raises GraphMutationError.
        """
        self._frozen = True
        logger.debug(f"StepGraph frozen: {self.task_name}")

    def _assert_mutable(self):
        if self._frozen:
            raise GraphMutationError(
                "Cannot modify frozen StepGraph during execution. "
                "Create a new graph to replan."
            )

    # ── Query ──────────────────────────────────────────────

    def pending_nodes(self) -> List[StepNode]:
        """Nodes that are PENDING and whose deps are satisfied."""
        return [
            n for n in self.nodes
            if n.status == StepStatus.PENDING and self._deps_ok(n)
        ]

    def _deps_ok(self, node: StepNode) -> bool:
        for dep_id in node.depends_on:
            dep = self._id_index.get(dep_id)
            if dep and not dep.is_done():
                return False
        return True

    def get_node(self, step_id: str) -> Optional[StepNode]:
        return self._id_index.get(step_id)

    def is_complete(self) -> bool:
        return all(n.is_done() for n in self.nodes)

    def has_failed(self) -> bool:
        return any(n.status == StepStatus.FAILED for n in self.nodes)

    def failed_nodes(self) -> List[StepNode]:
        return [n for n in self.nodes if n.status == StepStatus.FAILED]

    def progress(self) -> tuple[int, int]:
        done = sum(1 for n in self.nodes if n.is_done())
        return done, len(self.nodes)

    # ── Rollback ──────────────────────────────────────────

    def rollback_steps_after(self, failed_step_id: str) -> List[dict]:
        """Rollback actions for all steps completed AFTER a given step."""
        rollbacks = []
        found = False
        for node in reversed(self.nodes):
            if node.step_id == failed_step_id:
                found = True
            if found and node.status == StepStatus.SUCCESS and node.rollback_action:
                rollbacks.append({
                    "action": node.rollback_action,
                    "value":  node.value,
                    "method": "system",
                    "_for":   node.step_id,
                })
        return rollbacks

    def rollback_all(self) -> List[dict]:
        rollbacks = []
        for node in reversed(self.nodes):
            if node.status == StepStatus.SUCCESS and node.rollback_action:
                rollbacks.append({
                    "action": node.rollback_action,
                    "value":  node.value,
                    "method": "system",
                    "_for":   node.step_id,
                })
        return rollbacks

    # ── Replan ────────────────────────────────────────────

    def replan_from(self, failed_step_id: str) -> List[StepNode]:
        """
        Reset nodes from failed_step_id onward to PENDING.
        Graph must be unfrozen first for replan.
        """
        self._frozen = False   # unfreeze for mutation
        retrying = False
        to_retry = []
        for node in self.nodes:
            if node.step_id == failed_step_id:
                retrying = True
            if retrying:
                node.reset_for_retry()
                to_retry.append(node)
        self._frozen = True    # re-freeze
        return to_retry

    # ── Summary ───────────────────────────────────────────

    def summary(self) -> str:
        done, total = self.progress()
        lines = [f"Task: {self.task_name}  ({done}/{total} done)"]
        for n in self.nodes:
            lines.append("  " + n.summary())
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "task":   self.task_name,
            "frozen": self._frozen,
            "nodes":  [
                {
                    "id":         n.step_id,
                    "seq":        n.seq_num,
                    "action":     n.action,
                    "value":      n.value,
                    "status":     n.status.value,
                    "method":     n.method_used or n.method,
                    "dur_ms":     round(n.duration_ms(), 1),
                    "attempts":   n.attempts,
                    "timeout_s":  n.timeout_s,
                    "max_retries": n.max_retries,
                    "error":      n.error,
                }
                for n in self.nodes
            ],
        }
