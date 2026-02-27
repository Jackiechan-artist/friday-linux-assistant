"""
LADA - Orchestrator v3
The control authority. Nothing executes without passing through here.

New in v3:
  ✔ StepGraph instead of flat list
  ✔ Per-step ErrorClassifier → targeted recovery
  ✔ Heartbeat sent to Watchdog every step
  ✔ ExecutionContext (live / dry_run / safe_mode)
  ✔ Async state machine (uses await transition)
  ✔ Partial replan after failure
  ✔ Rollback on critical failures

Flow:
  run(command)
    → _plan()           planner + schema validate
    → StepGraph.build() convert to tracked nodes
    → _exec_graph()     execute node by node
        → step_started heartbeat
        → StepExecutor.execute()
        → ErrorClassifier if fail
        → RetryPolicy with targeted fallback
        → Verifier
        → step heartbeat again
    → TaskResult
"""

import asyncio
import time
from typing import Optional

from core.planner            import Planner
from core.state_machine      import StateMachine, TaskState
from core.step_executor      import StepExecutor
from core.step_graph         import StepGraph, StepNode, StepStatus
from core.verifier           import Verifier
from core.recovery           import RecoveryEngine
from core.action_result      import ActionResult
from core.error_classifier   import ErrorClassifier, ErrorClass
from core.execution_context  import ExecutionContext, ExecMode, make_context
from core.execution_audit    import ExecutionAudit
from core.rollback_manager   import RollbackManager
from core.capability_detector import Capabilities
from utils.retry_policy      import RetryPolicy, RetryConfig
from utils.timeout           import TimeoutManager
from utils.watchdog          import Watchdog
from utils.schema_validator  import SchemaValidator
from utils.resource_monitor  import ResourceMonitor
from utils.logger            import LADALogger

logger = LADALogger("ORCHESTRATOR")


class TaskResult:
    def __init__(
        self,
        success: bool,
        task_name: str = "",
        command: str = "",
        steps_done: int = 0,
        steps_total: int = 0,
        duration_s: float = 0.0,
        error: str = "",
        graph: Optional[StepGraph] = None,
    ):
        self.success     = success
        self.task_name   = task_name
        self.command     = command
        self.steps_done  = steps_done
        self.steps_total = steps_total
        self.duration_s  = duration_s
        self.error       = error
        self.graph       = graph

    def __bool__(self): return self.success

    def __repr__(self):
        mark = "✓" if self.success else "✗"
        return (
            f"TaskResult({mark} {self.task_name} | "
            f"{self.steps_done}/{self.steps_total} steps | "
            f"{self.duration_s:.1f}s"
            + (f" | {self.error[:50]!r}" if self.error else "")
            + ")"
        )


class Orchestrator:
    """v3 — full control authority with graph execution."""

    def __init__(
        self,
        ui_actions,
        browser_actions,
        system_actions,
        context_store,
        verifier:     Optional[Verifier]     = None,
        planner:      Optional[Planner]      = None,
        capabilities: Optional[Capabilities] = None,
        exec_mode:    str                    = "live",
    ):
        self.context_store  = context_store
        self.capabilities   = capabilities

        # Sub-systems
        self.planner        = planner or Planner(context_store=context_store)
        self.state_machine  = StateMachine()
        self.verifier       = verifier or Verifier()
        self.recovery       = RecoveryEngine()
        self.classifier     = ErrorClassifier()
        self.schema_val     = SchemaValidator()
        self.timeout_mgr    = TimeoutManager()
        self.retry_policy   = RetryPolicy(RetryConfig(
            max_attempts=3, base_delay_s=0.6, backoff_factor=1.5,
        ))
        self.watchdog       = Watchdog()

        self.step_executor  = StepExecutor(
            ui_actions=ui_actions,
            browser_actions=browser_actions,
            system_actions=system_actions,
            timeout_manager=self.timeout_mgr,
            capabilities=capabilities,
        )

        # Keep refs for recovery
        self.ui_actions      = ui_actions
        self.browser_actions = browser_actions
        self.system_actions  = system_actions

        # New in v4
        self.audit           = ExecutionAudit()
        self.rollback_mgr    = RollbackManager(
            system_actions=system_actions,
            ui_actions=ui_actions,
        )
        self.resource_monitor = ResourceMonitor()

        # Browser isolation: only one browser action at a time
        self._browser_lock   = asyncio.Lock()

        # Execution mode
        self._exec_mode_str = exec_mode

        # Watchdog abort flag
        self._abort = False

        # Register watchdog alert callback
        self.watchdog.on_alert(self._on_watchdog_alert)

    # ══════════════════════════════════════════════════════
    # PUBLIC
    # ══════════════════════════════════════════════════════

    async def run(
        self,
        user_command: str,
        exec_mode: str = "",
    ) -> TaskResult:
        """Execute one user command end-to-end."""
        start_t = time.monotonic()
        mode    = exec_mode or self._exec_mode_str
        ctx     = make_context(mode, task_name="pending")
        self._abort = False

        loop = asyncio.get_event_loop()
        self.watchdog.start(loop=loop)

        logger.info("═" * 56)
        logger.info(f"  Command : {user_command!r}")
        logger.info(f"  Mode    : {ctx.mode.value.upper()}")
        logger.info("═" * 56)

        # Resource check before starting
        res = self.resource_monitor.check()
        if res.is_high_pressure:
            logger.warning(
                f"System under pressure (score={res.pressure_score:.2f}) — "
                f"adding {res.recommended_delay_s:.1f}s startup delay"
            )
            await asyncio.sleep(res.recommended_delay_s)

        # Start audit trail
        self.audit.start_task(
            task_name="pending", command=user_command, mode=mode
        )
        self.rollback_mgr.clear()

        # ── 1. Plan ────────────────────────────────────────
        await self.state_machine.transition(TaskState.PLANNED)
        plan = await self._plan(user_command)
        if not plan:
            await self.state_machine.transition(TaskState.FAILED)
            await self._force_reset_state()   # ← fix: reset so next command works
            self.watchdog.stop()
            return TaskResult(
                success=False, command=user_command,
                error="Planning failed", duration_s=time.monotonic() - start_t,
            )

        ctx.task_name = plan.get("task", "unknown")

        # Dry-run: just simulate
        if ctx.mode == ExecMode.DRY_RUN:
            dry_log = ctx.simulate(plan)
            self.watchdog.stop()
            await self.state_machine.transition(TaskState.SUCCESS)
            await self.state_machine.transition(TaskState.INIT)
            return TaskResult(
                success=True,
                task_name=ctx.task_name,
                command=user_command,
                steps_done=0,
                steps_total=len(plan.get("steps", [])),
                duration_s=time.monotonic() - start_t,
                error="dry_run: no actions executed",
            )

        # ── 2. Build graph ─────────────────────────────────
        logger.info(f"📋 Building execution graph...")
        graph = StepGraph.from_plan(plan)
        logger.info(f"📊 Graph: {len(graph.nodes)} steps, task={graph.task_name}")

        # ── 3. Execute ─────────────────────────────────────
        logger.info(f"▶️  Starting execution...")
        graph.freeze()   # immutable during execution
        result = await self._exec_graph(graph, ctx)

        # ── 4. Finish ──────────────────────────────────────
        duration = time.monotonic() - start_t

        if result.success:
            await self.state_machine.transition(TaskState.SUCCESS)
            if self.context_store:
                self.context_store.log_success(
                    user_command, plan, duration=duration
                )
            # ── Cache plan ONLY on success ──────────────────
            # This prevents bad/failed plans from polluting the cache.
            try:
                self.planner.cache.save(user_command, plan, verified=True)
                logger.debug(f"Plan cached after successful execution: {plan.get('task')}")
            except Exception:
                pass
        else:
            if self.state_machine.current_state not in (
                TaskState.FAILED, TaskState.CANCELLED
            ):
                await self.state_machine.transition(TaskState.FAILED)
            if self.context_store:
                self.context_store.log_failure(
                    user_command, ctx.task_name,
                    error=result.error, duration=duration
                )
            # FIX v7.1: Failure pe cache invalidate karo — broken plan delete ho jaaye
            # Agali baar same command pe fresh AI plan banega
            try:
                self.planner.cache.invalidate(user_command)
                logger.info(f"Cache invalidated after failure: '{user_command[:40]}'")
            except Exception:
                pass

        result.duration_s = duration
        result.graph      = graph
        logger.info(f"  {result}")

        # End audit
        ctx_snap = ctx.snapshot() if hasattr(ctx, "snapshot") else {}
        self.audit.end_task(
            success=result.success,
            error=result.error,
            context_snap=ctx_snap,
        )

        # Rollback if failed and graph has completed steps
        if not result.success and graph:
            rb_results = await self.rollback_mgr.rollback_all()
            if rb_results:
                self.audit.rollback_event(rb_results)

        self.watchdog.stop()
        self.state_machine.force_reset()
        return result

    # ══════════════════════════════════════════════════════
    # GRAPH EXECUTION
    # ══════════════════════════════════════════════════════

    async def _exec_graph(
        self, graph: StepGraph, ctx: ExecutionContext
    ) -> TaskResult:
        """Execute all nodes in the StepGraph."""
        task_name = graph.task_name
        failed_error = ""

        while True:
            ready = graph.pending_nodes()
            if not ready:
                break

            for node in ready:
                # Watchdog abort check
                if self._abort or self.watchdog.abort_requested:
                    node.mark_failed("Watchdog abort")
                    failed_error = self.watchdog.abort_reason or "Watchdog abort"
                    await self.state_machine.transition(TaskState.FAILED)
                    return self._make_result(graph, task_name, "", failed_error)

                # Check execution context
                if not ctx.can_execute(node.to_step_dict()):
                    node.mark_skipped("blocked by exec_context")
                    logger.info(
                        f"  [{node.seq_num}] SKIPPED: {node.action} "
                        f"(mode={ctx.mode.value})"
                    )
                    continue

                # Execute node
                outcome = await self._exec_node(node, graph, ctx)
                if not outcome:
                    failed_error = node.error
                    return self._make_result(
                        graph, task_name, "", failed_error
                    )

            # Check for failed nodes that block progress
            if graph.has_failed():
                fn = graph.failed_nodes()[0]
                failed_error = fn.error
                return self._make_result(graph, task_name, "", failed_error)

        done, total = graph.progress()
        success = done == total
        return self._make_result(graph, task_name, "", "" if success else "incomplete")

    async def _exec_node(self, node: StepNode, graph: StepGraph, ctx: ExecutionContext) -> bool:
        """Execute a single StepNode with full pipeline."""
        step  = node.to_step_dict()
        label = f"[{node.seq_num}] {node.action}={node.value!r}"

        await self.state_machine.transition(
            TaskState.EXECUTING,
            step_num=node.seq_num,
            action=node.action,
            total_steps=len(graph.nodes),
        )

        node.mark_running()
        self.watchdog.step_started(node.action, label)
        ctx.record_step_start(node.action, node.value)

        logger.info(f"  🔹 Step {node.seq_num}/{len(graph.nodes)}: {node.action}('{node.value}') via {node.method}")

        # Audit
        self.audit.step_start(node.step_id, node.action, node.value, node.method)

        # Resource pressure delay
        res = self.resource_monitor.check()
        if res.recommended_delay_s > 0:
            await asyncio.sleep(res.recommended_delay_s)

        # Browser isolation: browser steps use a lock to prevent focus conflicts
        is_browser_step = node.method == "browser" or node.action in {
            "navigate", "wait_for_element", "find_and_click"
        }

        if is_browser_step:
            async with self._browser_lock:
                result = await self.retry_policy.execute_with_retry(
                    fn=self.step_executor.execute,
                    step=step,
                    label=label,
                )
        else:
            result = await self.retry_policy.execute_with_retry(
                fn=self.step_executor.execute,
                step=step,
                label=label,
            )

        self.watchdog.step_finished()
        self.watchdog.heartbeat(label)   # pulse after each step

        if not result:
            # Classify failure
            classified = self.classifier.classify(
                "executor returned None or False",
                action=node.action,
                method=node.method,
            )
            logger.warning(f"  {label} FAILED → {classified.error_class.value}")

            # Level 3 recovery
            await self.state_machine.transition(TaskState.RECOVERING)
            recovered = await self.recovery.full_reset(step, self)
            if not recovered:
                node.mark_failed(
                    f"{classified.error_class.value}: all retries exhausted"
                )
                await self.state_machine.transition(TaskState.FAILED)
                return False
            result = ActionResult.ok(action=node.action, value=node.value,
                                     method="recovery")

        # ── Verify ─────────────────────────────────────
        await self.state_machine.transition(
            TaskState.VERIFYING, step_num=node.seq_num
        )
        verified = await self._verify(step)

        if not verified:
            logger.warning(f"  {label} → verify FAILED")
            v_ok = await self.recovery.recover_verify_failure(step, self)
            if not v_ok:
                node.mark_failed("verification failed after recovery")
                await self.state_machine.transition(TaskState.FAILED)
                return False

        node.mark_success(method_used=result.method)
        logger.info(
            f"  ✅ Step {node.seq_num} OK: {node.action} ({result.method}, {result.execution_time_ms:.0f}ms)"
        )

        # Update execution context
        ctx.record_step_end(node.action, result.method, success=True)
        if node.action == "open_app":
            ctx.active_app = node.value

        # Audit step_end
        self.audit.step_end(
            step_id=node.step_id,
            action=node.action,
            success=True,
            method_used=result.method,
            dur_ms=result.execution_time_ms,
        )

        # Register rollback for reversible steps
        if node.rollback_action:
            self.rollback_mgr.push(
                {"action": node.rollback_action, "value": node.value, "method": "system"},
                source_step_id=node.step_id,
            )

        # Record learning
        if self.context_store:
            self.context_store.record_success_pattern(
                graph.task_name, node.action, result.method
            )

        return True

    # ══════════════════════════════════════════════════════
    # HELPERS
    # ══════════════════════════════════════════════════════

    async def _plan(self, command: str) -> Optional[dict]:
        try:
            plan = await self.timeout_mgr.run_safe(
                self.planner.plan(command),
                action="default",
                timeout_override=30.0,
                fallback=None,
            )
        except Exception as e:
            logger.error(f"Planner exception: {e}")
            return None

        if not plan:
            return None

        if not self.schema_val.validate_plan(plan):
            sanitized = self.schema_val.sanitize_plan(plan)
            if sanitized.get("steps"):
                return sanitized
            return None

        return plan

    async def _verify(self, step: dict) -> bool:
        # FIX v7.1: run_command steps ke liye verification skip karo.
        # SystemActions._run_command() pehle se hi returncode check karta hai.
        # Extra verify call sirf delay karta hai aur kabhi kabhi false fail deta hai.
        if step.get("action") == "run_command":
            return True
        # open_app, open_terminal bhi skip — process check unreliable hoti hai
        # jab app newly launched ho aur window appear hone mein time lag raha ho
        if step.get("action") in ("open_app", "open_terminal", "open_menu",
                                   "search", "click_result"):
            return True
        return await self.timeout_mgr.run_safe(
            self.verifier.verify_step(step),
            action="verify_window",
            timeout_override=8.0,
            fallback=True,
        )

    def _make_result(
        self, graph, task_name, command, error
    ) -> TaskResult:
        done, total = graph.progress()
        success = not error and done == total

        # NOTE: State reset is handled by force_reset() in run() at line 268.
        # Do NOT reset here — causes race condition with next command's PLANNED transition.

        return TaskResult(
            success=success,
            task_name=task_name,
            command=command,
            steps_done=done,
            steps_total=total,
            error=error,
            graph=graph,
        )

    async def _force_reset_state(self):
        """Force state machine back to INIT after any terminal state."""
        try:
            from core.state_machine import TaskState
            async with self.state_machine._lock:
                self.state_machine._state = TaskState.INIT
        except Exception:
            pass

    def _on_watchdog_alert(self, event: str, message: str):
        logger.error(f"WATCHDOG ALERT [{event}]: {message}")
        # Only abort on actual freeze (heartbeat timeout), not on resource warnings
        if event == "heartbeat_timeout":
            self._abort = True

    def get_status(self) -> dict:
        return {
            "state":    self.state_machine.current_state.value,
            "watchdog": self.watchdog.get_status(),
        }
