"""
LADA - Linux Autonomous Desktop Agent
Main Entry Point

Architecture (correct order):
  User Command
       ↓
  Orchestrator          ← control authority
       ↓
  Planner (AI)          ← generates plan
       ↓
  StepExecutor          ← runs each step
       ↓
  Action Layers         ← UI / Browser / System
       ↓
  Verifier              ← check every step
       ↓
  Recovery / Watchdog   ← handle failures
       ↓
  Memory + Logs
"""

import asyncio
import sys
import json
import argparse

from core.orchestrator        import Orchestrator
from core.planner             import Planner
from core.verifier            import Verifier
from core.capability_detector import CapabilityDetector
from perception.accessibility     import AccessibilityLayer
from perception.browser_dom       import BrowserDOMLayer
from perception.cv_detector       import CVDetector
from actions.ui_actions       import UIActions
from actions.browser_actions  import BrowserActions
from actions.system_actions   import SystemActions
from memory.context_store     import ContextStore
from memory.learning_engine   import LearningEngine
from utils.logger             import LADALogger

logger = LADALogger("LADA_MAIN")


class LADA:
    """
    LADA top-level container.
    Wires all sub-systems together and delegates to Orchestrator.
    """

    def __init__(self, exec_mode: str = "live"):
        logger.info("Initializing LADA...")

        self.context_store   = ContextStore()
        self.learning_engine = LearningEngine(self.context_store)
        self._exec_mode      = exec_mode
        self.cap_detector    = CapabilityDetector()
        self.capabilities    = None

        self.accessibility = AccessibilityLayer()
        self.browser_dom   = BrowserDOMLayer()
        self.cv_detector   = CVDetector()

        self.ui_actions      = UIActions(
            accessibility=self.accessibility,
            cv_detector=self.cv_detector,
        )
        self.browser_actions = BrowserActions(browser_dom=self.browser_dom)
        self.system_actions  = SystemActions()

        self.planner     = Planner(context_store=self.context_store)
        self.verifier    = Verifier()
        self.orchestrator = None

        logger.info("LADA initialized.")

    async def boot(self):
        logger.info("Boot sequence starting...")
        self.capabilities = await self.cap_detector.detect()
        self.context_store.save_context("capabilities", self.capabilities.to_dict())
        self.context_store.save_context("system", self.capabilities.to_dict())

        await self.accessibility.initialize()
        await self.cv_detector.initialize()

        self.orchestrator = Orchestrator(
            ui_actions=self.ui_actions,
            browser_actions=self.browser_actions,
            system_actions=self.system_actions,
            context_store=self.context_store,
            verifier=self.verifier,
            planner=self.planner,
            capabilities=self.capabilities,
            exec_mode=self._exec_mode,
        )
        logger.info("Boot complete.")
        return self.capabilities

    async def run_command(self, user_command: str):
        """Run one command through the Orchestrator pipeline."""
        if not self.orchestrator:
            logger.error("LADA not booted. Call boot() first.")
            return None
        return await self.orchestrator.run(user_command)

    async def shutdown(self):
        logger.info("Shutting down...")
        await self.browser_actions.cleanup()
        await self.accessibility.cleanup()
        if self.orchestrator:
            self.orchestrator.watchdog.stop()
        logger.info("Shutdown complete.")


BANNER = """
╔═══════════════════════════════════════════════════════╗
║        LADA - Linux Autonomous Desktop Agent          ║
║                                                       ║
║  Commands:                                            ║
║    status   → system capabilities                     ║
║    history  → last 10 tasks                           ║
║    health   → learning engine report                  ║
║    exit     → quit                                    ║
║    <text>   → execute as task                         ║
╚═══════════════════════════════════════════════════════╝
"""


async def interactive_mode(agent: LADA):
    print(BANNER)

    while True:
        try:
            raw = input("LADA> ").strip()
        except (KeyboardInterrupt, EOFError):
            break

        if not raw:
            continue
        cmd = raw.lower()

        if cmd == "exit":
            break

        elif cmd == "status":
            cap = agent.context_store.get_context("capabilities", {})
            print(json.dumps(cap, indent=2))

        elif cmd == "history":
            logs = agent.context_store.get_recent_task_log(limit=10)
            for entry in logs:
                mark = "✓" if entry["status"] == "success" else "✗"
                print(
                    f"  {mark} [{entry['task_name']}] "
                    f"{entry['command']!r} "
                    f"({entry['duration_seconds']:.1f}s)"
                )

        elif cmd == "health":
            report = agent.learning_engine.get_health_report()
            if report:
                print("Consistently failing patterns:")
                for k, v in report.items():
                    print(f"  {k}: {v}")
            else:
                print("  No persistent failure patterns detected.")

        else:
            result = await agent.run_command(raw)
            if result:
                mark = "✓ Success" if result.success else "✗ Failed"
                print(
                    f"\n[{mark}] "
                    f"{result.steps_done}/{result.steps_total} steps "
                    f"in {result.duration_s:.1f}s"
                )
                if result.error:
                    print(f"  Error: {result.error}")
            print()

    print("\nGoodbye!")


async def main():
    parser = argparse.ArgumentParser(
        description="LADA - Linux Autonomous Desktop Agent"
    )
    parser.add_argument("command", nargs="?",
                        help="Single command (omit for interactive mode)")
    parser.add_argument("--no-boot", action="store_true",
                        help="Skip boot/detection")
    parser.add_argument("--debug", action="store_true",
                        help="Enable debug logging")
    parser.add_argument(
        "--mode",
        choices=["live", "dry_run", "safe_mode"],
        default="live",
        help="Execution mode: live (default), dry_run (simulate only), safe_mode",
    )
    args = parser.parse_args()

    if args.debug:
        LADALogger("LADA_MAIN").set_level("DEBUG")

    agent = LADA(exec_mode=args.mode)

    if not args.no_boot:
        await agent.boot()

    try:
        if args.command:
            result = await agent.run_command(args.command)
            sys.exit(0 if result and result.success else 1)
        else:
            await interactive_mode(agent)
    finally:
        await agent.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
