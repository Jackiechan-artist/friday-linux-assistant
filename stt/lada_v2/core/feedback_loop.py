"""
LADA/LASA - FeedbackLoop
Ye hai asli autonomy.

Har action ke baad:
  1. Screen phir se dekho (perceive)
  2. Expected state se compare karo
  3. Mismatch hai? → Replan
  4. Goal achieve hua? → Done

Main loop:

  while not goal_achieved:
      state   = perceive()
      model.update(state)
      plan    = planner.generate(goal, model.to_ai_context())
      action  = plan.next_step()
      result  = executor.run(action)
      model.record_action(result)
      if failure: planner.replan()
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional, List, Callable, Any
from core.world_model import WorldModel
from utils.logger import get_logger

log = get_logger("FEEDBACK")


@dataclass
class StepResult:
    action:     str
    value:      str
    success:    bool
    output:     str   = ""
    error:      str   = ""
    duration:   float = 0.0
    confidence: float = 1.0   # 0.0–1.0  how sure we are this step worked correctly

    # Confidence rules:
    #   1.0  = shell command ran, returncode=0, output verified
    #   0.8  = AT-SPI click succeeded, element found by exact name
    #   0.6  = AT-SPI click succeeded, element found by partial name
    #   0.4  = coordinate click, no verification possible
    #   0.2  = action ran but outcome unclear (no feedback)
    #   0.0  = explicit failure

    @property
    def is_low_confidence(self) -> bool:
        return self.confidence < 0.5

    @property
    def needs_verification(self) -> bool:
        """Should the loop re-perceive and verify before continuing?"""
        return self.success and self.confidence < 0.7


@dataclass
class LoopResult:
    goal:           str
    success:        bool
    steps_taken:    int
    total_time_s:   float
    failure_reason: str = ""
    avg_confidence: float = 1.0
    step_log:       List[StepResult] = field(default_factory=list)

    def summary(self) -> str:
        status = "✓ SUCCESS" if self.success else "✗ FAILED"
        conf   = f" conf={self.avg_confidence:.2f}" if self.avg_confidence < 0.9 else ""
        return (
            f"{status}: '{self.goal}' | "
            f"{self.steps_taken} steps | "
            f"{self.total_time_s:.1f}s{conf}"
            + (f" | {self.failure_reason}" if not self.success else "")
        )


def _avg_conf(steps: list) -> float:
    """Average confidence across all steps."""
    if not steps:
        return 1.0
    return round(sum(s.confidence for s in steps) / len(steps), 3)


class FeedbackLoop:
    """
    Autonomous execution loop.

    Ye LADA ke Orchestrator se alag hai:
    - Orchestrator ek fixed plan execute karta hai
    - FeedbackLoop har step ke baad screen dekh ke decide karta hai

    Use karo jab task complex ho ya multi-step UI interaction ho.
    """

    def __init__(
        self,
        world_model: WorldModel,
        perceive_fn:    Callable,   # async fn() → ScreenState | None
        execute_fn:     Callable,   # async fn(action, value) → StepResult
        replan_fn:      Callable,   # async fn(goal, context) → List[dict]
        max_steps:      int   = 15,
        step_timeout_s: float = 30.0,
        replan_on_fail: bool  = True,
    ):
        self.model          = world_model
        self._perceive      = perceive_fn
        self._execute       = execute_fn
        self._replan        = replan_fn
        self.max_steps      = max_steps
        self.step_timeout   = step_timeout_s
        self.replan_on_fail = replan_on_fail

        self._current_plan:  List[dict] = []
        self._plan_index:    int = 0
        self._replan_count:  int = 0
        self._max_replans:   int = 3

    # ──────────────────────────────────────────────
    # MAIN LOOP
    # ──────────────────────────────────────────────

    async def run(self, goal: str) -> LoopResult:
        """
        Execute goal using perception-action-feedback loop.
        """
        log.info(f"FeedbackLoop starting: '{goal}'")
        self.model.set_goal(goal)

        start_time = time.monotonic()
        step_log   = []
        steps_done = 0

        # Step 1: Initial perception
        await self._perceive_and_update()

        # Step 2: Initial plan
        self._current_plan = await self._make_plan(goal)
        if not self._current_plan:
            return LoopResult(
                goal=goal, success=False,
                steps_taken=0, total_time_s=0,
                failure_reason="Could not generate initial plan",
            )

        log.info(f"Initial plan: {len(self._current_plan)} steps")
        self._plan_index = 0

        # Main loop
        while steps_done < self.max_steps:
            # Get next action from plan
            step = self._next_step()
            if step is None:
                # Plan exhausted
                log.info("Plan complete — checking goal achievement")
                await self._perceive_and_update()
                self.model.mark_success()
                break

            action = step.get("action", "")
            value  = step.get("value", "")

            log.info(f"Step {steps_done+1}: {action}('{value[:40]}')")

            # Execute with timeout
            try:
                result = await asyncio.wait_for(
                    self._execute(action, value),
                    timeout=self.step_timeout
                )
            except asyncio.TimeoutError:
                result = StepResult(
                    action=action, value=value,
                    success=False, error=f"Timeout after {self.step_timeout}s",
                    confidence=0.0
                )

            steps_done += 1
            step_log.append(result)

            # Log confidence level
            conf_label = (
                "HIGH" if result.confidence >= 0.7
                else "LOW ⚠" if result.confidence >= 0.4
                else "VERY LOW ⚠⚠"
            )
            log.info(
                f"  confidence={result.confidence:.2f} [{conf_label}] | "
                f"{'✓' if result.success else '✗'} {action}"
            )

            # Record in world model
            self.model.record_action(
                action=action,
                value=value,
                expected=step.get("expected_outcome", ""),
                observed=result.output if result.success else result.error,
                success=result.success,
            )

            # Low confidence → re-perceive and verify before continuing
            if result.success and result.needs_verification:
                log.info(f"  Low confidence ({result.confidence:.2f}) — re-verifying state...")
                await asyncio.sleep(0.6)
                await self._perceive_and_update()
                # Check if world state changed as expected
                expected_outcome = step.get("expected_outcome", "")
                if expected_outcome:
                    ctx = self.model.to_ai_context()
                    if expected_outcome.lower() not in ctx.lower():
                        log.warning(
                            f"  Expected '{expected_outcome}' not confirmed — "
                            f"marking as uncertain"
                        )
                        # Downgrade: treat as soft failure, allow replan
                        self.model.record_action(
                            action=f"verify_{action}", value=expected_outcome,
                            expected=expected_outcome, observed="not confirmed",
                            success=False,
                        )
            else:
                # Normal: short wait then perceive
                await asyncio.sleep(0.4)
                await self._perceive_and_update()

            # Check for critical failures
            if not result.success:
                log.warning(f"Step failed: {action}({value[:30]}) — {result.error}")

                # Classify failure type
                err = result.error.lower()
                is_fatal = any(k in err for k in [
                    "permission denied", "not installed", "command not found",
                    "no such file or directory", "syntax error"
                ])
                is_transient = any(k in err for k in [
                    "timeout", "not found", "not ready", "temporarily",
                    "try again", "element", "focus"
                ])
                failure_type = "fatal" if is_fatal else "transient" if is_transient else "unknown"
                log.info(f"  Failure type: {failure_type}")

                # Fatal failures → no point retrying, stop immediately
                if is_fatal:
                    total_time = time.monotonic() - start_time
                    return LoopResult(
                        goal=goal, success=False,
                        steps_taken=steps_done,
                        total_time_s=total_time,
                        failure_reason=f"Fatal error ({failure_type}): {result.error[:80]}",
                        avg_confidence=_avg_conf(step_log),
                        step_log=step_log,
                    )

                # Transient / unknown → replan if allowed
                if self.replan_on_fail and self._replan_count < self._max_replans:
                    log.info(f"Replanning (attempt {self._replan_count+1}/{self._max_replans})")
                    new_plan = await self._make_plan(goal)
                    if new_plan:
                        self._current_plan = new_plan
                        self._plan_index   = 0
                        self._replan_count += 1
                        log.info(f"New plan: {len(new_plan)} steps")
                        continue

                if self.model.consecutive_failures >= 3:
                    total_time = time.monotonic() - start_time
                    return LoopResult(
                        goal=goal, success=False,
                        steps_taken=steps_done,
                        total_time_s=total_time,
                        failure_reason=f"3 consecutive {failure_type} failures. Last: {result.error[:60]}",
                        avg_confidence=_avg_conf(step_log),
                        step_log=step_log,
                    )

        total_time = time.monotonic() - start_time
        success    = self.model.task_status in ("success", "in_progress")

        result_obj = LoopResult(
            goal=goal,
            success=success,
            steps_taken=steps_done,
            total_time_s=total_time,
            avg_confidence=_avg_conf(step_log),
            step_log=step_log,
        )
        log.info(result_obj.summary())
        return result_obj

    # ──────────────────────────────────────────────
    # INTERNAL
    # ──────────────────────────────────────────────

    async def _perceive_and_update(self) -> None:
        """Perceive screen state and update world model."""
        try:
            state = await self._perceive()
            if state is not None:
                self.model.update_from_screen(state)
            else:
                self.model.update_from_wmctrl()
        except Exception as e:
            log.warning(f"Perception error: {e}")
            self.model.update_from_wmctrl()

    async def _make_plan(self, goal: str) -> List[dict]:
        """Generate plan from AI given current world context."""
        context = self.model.to_ai_context()
        try:
            plan = await self._replan(goal, context)
            return plan or []
        except Exception as e:
            log.error(f"Plan generation failed: {e}")
            return []

    def _next_step(self) -> Optional[dict]:
        """Get next step from current plan."""
        if self._plan_index < len(self._current_plan):
            step = self._current_plan[self._plan_index]
            self._plan_index += 1
            return step
        return None

    def reset(self) -> None:
        """Reset loop state for a new task."""
        self._current_plan = []
        self._plan_index   = 0
        self._replan_count = 0
