"""
LADA - Rollback Manager
Executes rollback actions when a task fails mid-way.
Maintains audit trail of what was rolled back.

Usage:
  rm = RollbackManager(system_actions)
  rm.push(rollback_step)           # register a reversible action
  await rm.rollback_all()          # undo everything in reverse order
"""

import asyncio
from typing import List, Optional
from utils.logger import LADALogger

logger = LADALogger("ROLLBACK")


class RollbackEntry:
    def __init__(self, step: dict, source_step_id: str = ""):
        self.step           = step
        self.source_step_id = source_step_id
        self.executed       = False
        self.success        = False
        self.error          = ""


class RollbackManager:
    """
    Tracks reversible actions and executes them in LIFO order on failure.
    """

    def __init__(self, system_actions=None, ui_actions=None):
        self.system_actions  = system_actions
        self.ui_actions      = ui_actions
        self._stack: List[RollbackEntry] = []

    def push(self, rollback_step: dict, source_step_id: str = ""):
        """Register a rollback action for a completed step."""
        if not rollback_step.get("action"):
            return
        entry = RollbackEntry(rollback_step, source_step_id)
        self._stack.append(entry)
        logger.debug(
            f"Rollback registered: {rollback_step.get('action')} "
            f"(for step {source_step_id})"
        )

    def push_from_graph(self, graph) -> int:
        """
        Register all rollback actions from a StepGraph.
        Returns number of rollbacks registered.
        """
        rollback_steps = graph.rollback_all()
        for step in rollback_steps:
            self.push(step, source_step_id=step.get("_for", ""))
        logger.info(f"Registered {len(rollback_steps)} rollback steps from graph.")
        return len(rollback_steps)

    async def rollback_all(self) -> List[dict]:
        """
        Execute all registered rollbacks in LIFO order.
        Returns list of rollback result dicts.
        """
        if not self._stack:
            logger.debug("No rollback steps registered.")
            return []

        logger.warning(
            f"Executing {len(self._stack)} rollback actions..."
        )
        results = []

        for entry in reversed(self._stack):
            result = await self._execute_rollback(entry)
            results.append(result)

        self._stack.clear()
        successful = sum(1 for r in results if r.get("success"))
        logger.info(
            f"Rollback complete: {successful}/{len(results)} succeeded."
        )
        return results

    async def rollback_from(self, failed_step_id: str, graph) -> List[dict]:
        """
        Rollback only steps after failed_step_id.
        """
        steps = graph.rollback_steps_after(failed_step_id)
        if not steps:
            return []

        logger.warning(
            f"Partial rollback from step {failed_step_id}: "
            f"{len(steps)} actions"
        )
        results = []
        for step in steps:
            entry = RollbackEntry(step, source_step_id=step.get("_for", ""))
            result = await self._execute_rollback(entry)
            results.append(result)
        return results

    async def _execute_rollback(self, entry: RollbackEntry) -> dict:
        action = entry.step.get("action", "")
        value  = entry.step.get("value", "")
        logger.info(f"  Rolling back: {action}={value!r}")

        try:
            # Use system actions for most rollbacks
            executor = None
            if self.system_actions:
                executor = self.system_actions.execute
            elif self.ui_actions:
                executor = self.ui_actions.execute

            if executor:
                ok = await asyncio.wait_for(
                    executor(entry.step),
                    timeout=8.0,
                )
                entry.executed = True
                entry.success  = bool(ok)
                if not ok:
                    entry.error = "Rollback action returned False"
            else:
                entry.error = "No executor available for rollback"

        except asyncio.TimeoutError:
            entry.error = "Rollback timeout"
            logger.warning(f"  Rollback timeout: {action}")
        except Exception as e:
            entry.error = str(e)
            logger.warning(f"  Rollback error: {action}: {e}")

        return {
            "action":       action,
            "value":        value,
            "source_step":  entry.source_step_id,
            "success":      entry.success,
            "error":        entry.error,
        }

    def clear(self):
        """Clear rollback stack (call after successful completion)."""
        self._stack.clear()

    def __len__(self):
        return len(self._stack)
