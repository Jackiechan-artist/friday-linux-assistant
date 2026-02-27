"""
LADA - Schema Validator
Validates AI-generated task plans against strict schema.
Prevents invalid or malformed plans from executing.
"""

from typing import Optional
from utils.logger import LADALogger

logger = LADALogger("SCHEMA_VALIDATOR")

# Valid action names
VALID_ACTIONS = {
    "open_app", "open_menu", "search", "click_result", "click_button",
    "type_text", "verify_window", "navigate", "find_and_click",
    "set_volume", "set_brightness", "focus_window", "close_window",
    "run_command", "wait_for_element", "scroll", "get_text", "open_terminal",
}

# Valid method names
VALID_METHODS = {"accessibility", "browser", "system", "cv", "auto"}

# Actions that REQUIRE a value
ACTIONS_REQUIRING_VALUE = {
    "open_app", "search", "click_result", "click_button",
    "type_text", "verify_window", "navigate", "find_and_click",
    "set_volume", "set_brightness", "focus_window", "close_window",
    "run_command", "wait_for_element", "get_text",
}

# Actions that MUST NOT contain pixel coordinates
COORDINATE_FORBIDDEN_ACTIONS = set(VALID_ACTIONS)  # All actions


class SchemaValidator:
    """
    Validates task plans from the AI planner.
    Enforces strict schema rules.
    """

    def validate_plan(self, plan: dict) -> bool:
        """
        Validate a complete task plan.
        Returns True if valid, False otherwise.
        """
        if not isinstance(plan, dict):
            logger.error("Plan is not a dict.")
            return False

        # Required top-level fields
        if "task" not in plan:
            logger.error("Plan missing 'task' field.")
            return False

        if "steps" not in plan:
            logger.error("Plan missing 'steps' field.")
            return False

        if not isinstance(plan["steps"], list):
            logger.error("'steps' must be a list.")
            return False

        if len(plan["steps"]) == 0:
            logger.error("Plan has no steps.")
            return False

        # Validate task name
        if not self._validate_task_name(plan["task"]):
            return False

        # Validate each step
        for i, step in enumerate(plan["steps"]):
            if not self._validate_step(step, step_num=i + 1):
                return False

        logger.debug(
            f"Plan '{plan['task']}' validated: {len(plan['steps'])} steps."
        )
        return True

    def _validate_task_name(self, task_name: str) -> bool:
        """Validate task name format."""
        if not isinstance(task_name, str) or not task_name:
            logger.error("Task name must be a non-empty string.")
            return False

        if len(task_name) > 100:
            logger.error("Task name too long (>100 chars).")
            return False

        return True

    def _validate_step(self, step: dict, step_num: int) -> bool:
        """Validate a single step."""
        if not isinstance(step, dict):
            logger.error(f"Step {step_num} is not a dict.")
            return False

        # Require 'action'
        if "action" not in step:
            logger.error(f"Step {step_num} missing 'action' field.")
            return False

        action = step["action"]

        # Check action is known
        if action not in VALID_ACTIONS:
            logger.warning(
                f"Step {step_num}: unknown action '{action}'. "
                f"Allowing as custom action."
            )
            # Don't fail — allow extension actions

        # Check value is present where required
        if action in ACTIONS_REQUIRING_VALUE:
            value = step.get("value", "")
            if not value or not str(value).strip():
                logger.error(
                    f"Step {step_num}: action '{action}' requires 'value' field."
                )
                return False

        # Check method is valid if provided
        if "method" in step:
            method = step["method"]
            if method not in VALID_METHODS:
                logger.warning(
                    f"Step {step_num}: unknown method '{method}'. "
                    f"Valid: {VALID_METHODS}"
                )
                # Don't fail — just warn

        # CRITICAL: Check for pixel coordinates in any field
        if not self._check_no_coordinates(step, step_num):
            return False

        return True

    def _check_no_coordinates(self, step: dict, step_num: int) -> bool:
        """
        Ensure step does not contain pixel coordinates.
        AI MUST NOT provide x/y positions.
        """
        forbidden_keys = {"x", "y", "coordinates", "pixels", "pixel", "pos", "position"}

        for key in step.keys():
            if key.lower() in forbidden_keys:
                logger.error(
                    f"Step {step_num}: FORBIDDEN coordinate key '{key}'. "
                    f"AI must not provide pixel coordinates!"
                )
                return False

        # Check value field doesn't look like coordinates
        value = str(step.get("value", ""))
        if self._looks_like_coordinates(value):
            logger.error(
                f"Step {step_num}: value '{value}' looks like pixel coordinates. "
                f"Blocking execution."
            )
            return False

        return True

    def _looks_like_coordinates(self, value: str) -> bool:
        """
        Detect if a value looks like pixel coordinates.
        E.g., "100,200" or "(500, 300)" or "x=100 y=200"
        """
        import re

        # Pattern: "number,number" or "(number, number)"
        coord_patterns = [
            r"^\(\d+,\s*\d+\)$",          # (100, 200)
            r"^\d+,\s*\d+$",               # 100,200
            r"^x\s*=\s*\d+.*y\s*=\s*\d+", # x=100 y=200
        ]

        value_stripped = value.strip()
        for pattern in coord_patterns:
            if re.match(pattern, value_stripped, re.IGNORECASE):
                return True

        return False

    def validate_step(self, step: dict) -> bool:
        """Public method to validate a single step."""
        return self._validate_step(step, step_num=0)

    def sanitize_plan(self, plan: dict) -> dict:
        """
        Sanitize a plan by removing/fixing invalid steps.
        Returns a cleaned plan.
        """
        if not isinstance(plan, dict):
            return {}

        sanitized = {
            "task": plan.get("task", "unknown"),
            "intent": plan.get("intent", ""),
            "steps": []
        }

        for i, step in enumerate(plan.get("steps", [])):
            if self._validate_step(step, step_num=i + 1):
                sanitized["steps"].append(step)
            else:
                logger.warning(f"Removed invalid step {i + 1}: {step}")

        return sanitized
