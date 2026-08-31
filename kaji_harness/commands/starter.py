"""CLI handlers for deterministic managed-starter operations."""

from __future__ import annotations

import json
import sys

from pydantic import ValidationError

from ..starter_release import ReleasePlanInput, build_release_plan
from ..starter_tracking import (
    TaskPlanInput,
    TrackingPlanInput,
    build_task_plan,
    build_tracking_plan,
)
from .exit_codes import EXIT_INVALID_INPUT, EXIT_OK


def cmd_starter_release_plan() -> int:
    """Read release observations from stdin and emit a deterministic JSON plan."""
    try:
        raw = json.loads(sys.stdin.read())
        observation = ReleasePlanInput.model_validate(raw)
    except (json.JSONDecodeError, ValidationError) as exc:
        sys.stderr.write(f"Error: invalid starter release-plan input: {exc}\n")
        return EXIT_INVALID_INPUT
    plan = build_release_plan(observation)
    sys.stdout.write(plan.model_dump_json() + "\n")
    return EXIT_OK


def cmd_starter_tracking_plan() -> int:
    """Read tracking-issue observations from stdin and emit a deterministic JSON plan."""
    try:
        raw = json.loads(sys.stdin.read())
        observation = TrackingPlanInput.model_validate(raw)
    except (json.JSONDecodeError, ValidationError) as exc:
        sys.stderr.write(f"Error: invalid starter tracking-plan input: {exc}\n")
        return EXIT_INVALID_INPUT
    plan = build_tracking_plan(observation)
    sys.stdout.write(plan.model_dump_json() + "\n")
    return EXIT_OK


def cmd_starter_task_plan() -> int:
    """Read task-sync observations from stdin and emit a deterministic JSON plan."""
    try:
        raw = json.loads(sys.stdin.read())
        observation = TaskPlanInput.model_validate(raw)
    except (json.JSONDecodeError, ValidationError) as exc:
        sys.stderr.write(f"Error: invalid starter task-plan input: {exc}\n")
        return EXIT_INVALID_INPUT
    plan = build_task_plan(observation)
    sys.stdout.write(plan.model_dump_json() + "\n")
    return EXIT_OK
