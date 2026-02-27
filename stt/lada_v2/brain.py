"""
LADA Brain — Unified Autonomous Loop
LADA (shell/planning) + LASA (screen/input) + WorldModel + FeedbackLoop

Yeh pura system ka dil hai.

Usage:
  python3 brain.py              → interactive
  python3 brain.py "open chrome" → single command
"""

import asyncio
import sys
import os
import json
try:
    import httpx as _httpx_mod
    import httpx
except ImportError:
    httpx = None
    _httpx_mod = None
from typing import Optional, List

# LADA imports
sys.path.insert(0, os.path.dirname(__file__))
from core.world_model   import WorldModel
from core.feedback_loop import FeedbackLoop, StepResult
from actions.system_actions  import SystemActions
from actions.browser_actions import BrowserActions
from perception.browser_dom      import BrowserDOMLayer
from utils.logger import get_logger

from lasa_agent import LASAAgent
LASA_AVAILABLE = True

log = get_logger("BRAIN")

# ── OpenRouter config (same as LADA planner) ──────────────────
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_KEY", "")
OPENROUTER_MODEL   = "openai/gpt-3.5-turbo"
OPENROUTER_URL     = "https://openrouter.ai/api/v1/chat/completions"

# ── Task routing: shell-first vs perception-loop ──────────────
# These patterns need screen perception (complex UI)
PERCEPTION_TASKS = [
    "click", "button par", "menu open", "dialog",
    "type karo", "fill", "form", "select", "choose",
    "dropdown", "popup", "window close",
]

BANNER = """
╔═══════════════════════════════════════════════════════════╗
║  LADA Brain — Autonomous Desktop Agent                    ║
║                                                           ║
║  Shell tasks  → fast (commands)                           ║
║  UI tasks     → smart (screen perception loop)            ║
║                                                           ║
║  Commands: see | windows | history | exit | <task>        ║
╚═══════════════════════════════════════════════════════════╝
"""


class Brain:
    """
    Unified brain combining:
    - LASA: screen reading + accurate input
    - LADA: shell execution + planning
    - WorldModel: state abstraction
    - FeedbackLoop: perceive → act → verify → replan
    """

    def __init__(self):
        self.world    = WorldModel()
        self.sys_act  = SystemActions()
        self.browser_dom = BrowserDOMLayer()
        self.browser_act = BrowserActions(browser_dom=self.browser_dom)
        self.lasa: Optional["LASAAgent"] = None
        self._history: List[dict] = []

    async def start(self) -> None:
        log.info("Brain starting...")

        if LASA_AVAILABLE:
            self.lasa = LASAAgent()
            ok = await self.lasa.start()
            if ok:
                log.info("LASA connected — screen perception active")
            else:
                log.warning("LASA started without AT-SPI")
        else:
            log.warning("LASA not found — running without screen perception")
            log.warning(f"Expected LASA at: {LASA_PATH}")

        # Initial world snapshot
        await self._update_world()
        log.info(f"Brain ready. {self.world.summary()}")

    # ──────────────────────────────────────────────
    # MAIN ENTRY: run a task
    # ──────────────────────────────────────────────

    async def run(self, goal: str) -> bool:
        """
        Run a task. Automatically routes to:
        - Shell execution (fast) for file/info/app tasks
        - Perception loop (smart) for UI interaction tasks
        """
        log.info(f"Goal: '{goal}'")
        self.world.set_goal(goal)

        # Route decision
        if self._needs_perception(goal):
            log.info("Route: PERCEPTION LOOP (UI task)")
            return await self._run_perception_loop(goal)
        else:
            log.info("Route: SHELL EXECUTOR (fast task)")
            return await self._run_shell_task(goal)

    # ──────────────────────────────────────────────
    # ROUTE 1: Shell execution (LADA style)
    # ──────────────────────────────────────────────

    async def _run_shell_task(self, goal: str) -> bool:
        """Fast path — generate shell plan and execute."""
        plan = await self._generate_plan(goal, use_world_context=False)
        if not plan:
            print(f"[Brain] Could not plan: '{goal}'")
            return False

        print(f"[Brain] Plan: {len(plan)} step(s)")
        success = True

        for i, step in enumerate(plan, 1):
            action = step.get("action", "")
            value  = step.get("value", "")
            method = step.get("method", "system")

            print(f"  Step {i}: {action}('{value[:50]}')")
            result = await self._execute_step(action, value, method)

            self.world.record_action(
                action=action, value=value,
                success=result.success,
                observed=result.output or result.error,
            )

            if result.success:
                if result.output:
                    print(f"  [✓] {result.output[:200]}")
                else:
                    print(f"  [✓] Done")
            else:
                print(f"  [✗] {result.error[:100]}")
                success = False
                break

        self._history.append({"goal": goal, "success": success})
        return success

    # ──────────────────────────────────────────────
    # ROUTE 2: Perception loop (LASA style)
    # ──────────────────────────────────────────────

    async def _run_perception_loop(self, goal: str) -> bool:
        """
        Perception-action-feedback loop for complex UI tasks.
        Requires LASA to be available.
        """
        if not self.lasa:
            log.warning("LASA not available — falling back to shell")
            return await self._run_shell_task(goal)

        loop = FeedbackLoop(
            world_model   = self.world,
            perceive_fn   = self._perceive,
            execute_fn    = self._execute_step,
            replan_fn     = self._generate_plan_with_context,
            max_steps     = 12,
            replan_on_fail= True,
        )

        result = await loop.run(goal)
        print(f"\n[Brain] {result.summary()}")

        self._history.append({
            "goal":    goal,
            "success": result.success,
            "steps":   result.steps_taken,
        })
        return result.success

    # ──────────────────────────────────────────────
    # PERCEPTION
    # ──────────────────────────────────────────────

    async def _perceive(self):
        """Read current screen state via LASA."""
        if self.lasa:
            return await self.lasa.see()
        return None

    async def _update_world(self) -> None:
        """Update world model from current screen."""
        state = await self._perceive()
        if state:
            self.world.update_from_screen(state)
        else:
            self.world.update_from_wmctrl()

    # ──────────────────────────────────────────────
    # EXECUTION
    # ──────────────────────────────────────────────

    async def _execute_step(
        self,
        action: str,
        value:  str,
        method: str = "system",
    ) -> StepResult:
        """Execute one action step, routing to correct handler."""
        import subprocess
        import time
        t0 = time.monotonic()

        try:
            # ── Shell commands ──────────────────────────────
            if action == "run_command":
                return await self._exec_shell(value)

            # ── App launch ──────────────────────────────────
            if action == "open_app":
                ok = await self.sys_act.execute(
                    {"action": "open_app", "value": value, "method": "system"}
                )
                return StepResult(action, value, bool(ok), "App launched" if ok else "", "")

            # ── Open terminal ───────────────────────────────
            if action == "open_terminal":
                ok = await self.sys_act.execute(
                    {"action": "open_terminal", "method": "system"}
                )
                return StepResult(action, value, bool(ok))

            # ── Browser navigate ────────────────────────────
            if action == "navigate":
                ok = await self.browser_act.execute(
                    {"action": "navigate", "value": value, "method": "browser"}
                )
                return StepResult(action, value, bool(ok))

            # ── LASA UI actions ─────────────────────────────
            if self.lasa:
                if action == "click_on":
                    ok, conf = await self.lasa.click_on(value)
                    return StepResult(action, value, ok,
                                      f"Clicked '{value}'" if ok else "",
                                      f"'{value}' not found" if not ok else "",
                                      confidence=conf)

                if action == "type":
                    ok = await self.lasa.type(value)
                    return StepResult(action, value, ok)

                if action == "key":
                    ok = await self.lasa.press(value)
                    return StepResult(action, value, ok)

                if action == "click_at":
                    parts = value.split(",")
                    if len(parts) == 2:
                        x, y = int(parts[0].strip()), int(parts[1].strip())
                        ok = await self.lasa.click_at(x, y)
                        return StepResult(action, value, ok)

                if action == "scroll":
                    ok = await self.lasa.scroll(value)
                    return StepResult(action, value, ok)

                if action == "wait_for":
                    el = await self.lasa.wait_for(value, timeout=8)
                    return StepResult(action, value, el is not None,
                                      f"Found: {el}" if el else "",
                                      "Not found" if el is None else "")

                if action in ("open_menu", "search", "click_result"):
                    ok = await self.sys_act.execute(
                        {"action": action, "value": value, "method": method}
                    )
                    return StepResult(action, value, bool(ok))

            # ── System actions ──────────────────────────────
            if action in ("set_volume", "set_brightness", "focus_window",
                          "close_window", "open_menu"):
                ok = await self.sys_act.execute(
                    {"action": action, "value": value, "method": "system"}
                )
                return StepResult(action, value, bool(ok))

            log.warning(f"Unknown action: {action}")
            return StepResult(action, value, False, "", f"Unknown action: {action}")

        except Exception as e:
            return StepResult(
                action, value, False, "",
                str(e)[:100],
                time.monotonic() - t0
            )

    async def _exec_shell(self, command: str) -> StepResult:
        """Execute shell command."""
        import subprocess
        try:
            # Non-blocking for GUI launchers
            if command.strip().startswith("xdg-open") or command.strip().endswith(" &"):
                clean = command.strip().rstrip("&").strip()
                subprocess.Popen(clean, shell=True,
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
                await asyncio.sleep(1.5)
                return StepResult("run_command", command, True, "Launched")

            result = subprocess.run(
                command, shell=True,
                capture_output=True, text=True, timeout=30
            )
            out = result.stdout.strip()
            err = result.stderr.strip()
            ok  = result.returncode == 0 or bool(out)
            return StepResult("run_command", command, ok, out, err if not ok else "")

        except subprocess.TimeoutExpired:
            return StepResult("run_command", command, False, "", "Timeout")
        except Exception as e:
            return StepResult("run_command", command, False, "", str(e))

    # ──────────────────────────────────────────────
    # PLANNING
    # ──────────────────────────────────────────────

    async def _generate_plan(
        self,
        goal: str,
        use_world_context: bool = True,
    ) -> List[dict]:
        """Generate execution plan via AI."""
        context = self.world.to_ai_context() if use_world_context else ""
        return await self._generate_plan_with_context(goal, context)

    async def _generate_plan_with_context(
        self,
        goal: str,
        context: str,
    ) -> List[dict]:
        """Generate plan with world model context."""

        system_prompt = """You are a Linux desktop automation planner.
Convert natural language goals into executable step lists.
You understand Hindi/Hinglish commands.

RESPOND ONLY with a JSON array of steps, nothing else.

Available actions:
  run_command   → shell command (file ops, info, xdg-open)
  open_app      → launch app (value = executable name)
  open_terminal → open terminal
  navigate      → open URL in browser
  click_on      → click element by name (LASA)
  type          → type text (LASA)
  key           → press key e.g. "ctrl+s", "Return" (LASA)
  click_at      → click at "x,y" coords (LASA)
  scroll        → scroll "up"/"down" (LASA)
  wait_for      → wait for element to appear (LASA)
  set_volume    → set volume 0-100
  close_window  → close window by title

Shell-first rules:
  - File ops (cp, mv, rm, echo) → run_command
  - App info (battery, disk) → run_command
  - Open unknown file → run_command with xdg-open
  - Open GUI app → open_app
  - UI clicks/typing → click_on + type + key

Output format (JSON array ONLY):
[
  {"action": "open_app", "value": "google-chrome", "method": "system"},
  {"action": "navigate", "value": "https://youtube.com"},
  {"action": "click_on", "value": "Search", "expected_outcome": "search box focused"}
]
"""

        user_msg = f"Goal: {goal}"
        if context:
            user_msg += f"\n\nCurrent screen state:\n{context}"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    OPENROUTER_URL,
                    headers={
                        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                        "Content-Type":  "application/json",
                    },
                    json={
                        "model": OPENROUTER_MODEL,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user",   "content": user_msg},
                        ],
                        "temperature": 0.1,
                        "max_tokens":  400,
                    }
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"].strip()

            # Parse JSON array
            import re
            content = re.sub(r"```(?:json)?\s*", "", content)
            content = re.sub(r"```\s*$", "", content).strip()
            start = content.find("[")
            end   = content.rfind("]") + 1
            if start == -1:
                log.error("No JSON array in plan response")
                return self._fallback_plan(goal)

            plan = json.loads(content[start:end])
            log.info(f"Plan generated: {len(plan)} steps")
            return plan

        except Exception as e:
            log.error(f"Plan generation error: {e}")
            return self._fallback_plan(goal)

    def _fallback_plan(self, goal: str) -> List[dict]:
        """Rule-based fallback when AI unavailable."""
        g = goal.lower()

        if any(k in g for k in ["battery", "batter"]):
            return [{"action": "run_command",
                     "value": "upower -i $(upower -e | grep -i bat | head -1) | grep percentage",
                     "method": "system"}]

        if any(k in g for k in ["chrome", "browser"]):
            return [{"action": "open_app", "value": "google-chrome", "method": "system"}]

        if any(k in g for k in ["file manager", "nemo", "files"]):
            return [{"action": "open_app", "value": "nemo", "method": "system"}]

        if any(k in g for k in ["disk", "storage", "df"]):
            return [{"action": "run_command", "value": "df -h /", "method": "system"}]

        if any(k in g for k in ["ram", "memory", "mem"]):
            return [{"action": "run_command", "value": "free -h", "method": "system"}]

        return []

    def _needs_perception(self, goal: str) -> bool:
        """Does this task need LASA screen perception?"""
        g = goal.lower()
        return any(k in g for k in PERCEPTION_TASKS)

    # ──────────────────────────────────────────────
    # UTILITY COMMANDS
    # ──────────────────────────────────────────────

    async def see(self) -> None:
        """Print what's on screen right now."""
        await self._update_world()
        if self.lasa:
            print(await self.lasa.describe())
        else:
            print(self.world.to_ai_context())

    def show_history(self) -> None:
        if not self._history:
            print("No history yet.")
            return
        print(f"\n{len(self._history)} tasks:")
        for i, h in enumerate(self._history[-10:], 1):
            mark = "✓" if h["success"] else "✗"
            print(f"  {i}. {mark} {h['goal']}")
        print()


# ──────────────────────────────────────────────────────────────
# INTERACTIVE CLI
# ──────────────────────────────────────────────────────────────

async def interactive(brain: Brain):
    print(BANNER)
    while True:
        try:
            cmd = input("Brain> ").strip()
            if not cmd:
                continue

            if cmd in ("exit", "quit", "q"):
                print("Goodbye!")
                break
            elif cmd == "see":
                await brain.see()
            elif cmd == "windows":
                wins = await brain.lasa.windows() if brain.lasa else []
                for w in wins:
                    print(f"  {w}")
            elif cmd == "history":
                brain.show_history()
            elif cmd == "world":
                print(brain.world.to_ai_context())
            else:
                ok = await brain.run(cmd)
                status = "[✓ Success]" if ok else "[✗ Failed]"
                print(f"\n{status}\n")

        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except EOFError:
            break


async def main():
    brain = Brain()
    await brain.start()

    if len(sys.argv) > 1:
        cmd = " ".join(sys.argv[1:])
        ok  = await brain.run(cmd)
        sys.exit(0 if ok else 1)
    else:
        await interactive(brain)


if __name__ == "__main__":
    asyncio.run(main())
