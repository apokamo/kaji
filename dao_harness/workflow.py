"""Workflow YAML loader and validator for dao_harness."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .errors import WorkflowValidationError
from .models import CycleDefinition, Step, Workflow


def load_workflow(path: Path) -> Workflow:
    """YAML ファイルからワークフロー定義をロードする。

    Args:
        path: ワークフロー定義ファイルのパス

    Returns:
        Workflow: パースされたワークフロー定義

    Raises:
        WorkflowValidationError: YAML パースエラーまたはバリデーションエラー
    """
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise WorkflowValidationError(f"YAML parse error: {e}") from e
    return _parse_workflow(data)


def load_workflow_from_str(yaml_str: str) -> Workflow:
    """YAML 文字列からワークフロー定義をロードする。

    Args:
        yaml_str: ワークフロー定義のYAML文字列

    Returns:
        Workflow: パースされたワークフロー定義

    Raises:
        WorkflowValidationError: YAML パースエラーまたはバリデーションエラー
    """
    try:
        data = yaml.safe_load(yaml_str)
    except yaml.YAMLError as e:
        raise WorkflowValidationError(f"YAML parse error: {e}") from e
    return _parse_workflow(data)


VALID_EXECUTION_POLICIES = {"auto", "sandbox", "interactive"}

_STEP_REQUIRED_KEYS = ("id", "skill", "agent")


def _parse_workflow(data: dict[str, Any]) -> Workflow:
    """YAML data dict をワークフローオブジェクトに変換する。"""
    if not isinstance(data, dict):
        raise WorkflowValidationError("Workflow definition must be a YAML mapping")

    raw_steps = data.get("steps", [])
    if raw_steps is None:
        raise WorkflowValidationError("'steps' must be a list, got null")
    if not isinstance(raw_steps, list):
        raise WorkflowValidationError(f"'steps' must be a list, got {type(raw_steps).__name__}")

    steps = []
    for i, step_data in enumerate(raw_steps):
        if not isinstance(step_data, dict):
            raise WorkflowValidationError(
                f"Step at index {i} must be a mapping, got {type(step_data).__name__}"
            )
        missing = [k for k in _STEP_REQUIRED_KEYS if k not in step_data]
        if missing:
            raise WorkflowValidationError(
                f"Step at index {i} missing required key(s): {', '.join(missing)}"
            )
        raw_on = step_data.get("on") or step_data.get(True) or {}
        if not isinstance(raw_on, dict):
            raise WorkflowValidationError(
                f"Step '{step_data['id']}' 'on' must be a mapping, got {type(raw_on).__name__}"
            )
        steps.append(
            Step(
                id=step_data["id"],
                skill=step_data["skill"],
                agent=step_data["agent"],
                model=step_data.get("model"),
                effort=step_data.get("effort"),
                max_budget_usd=step_data.get("max_budget_usd"),
                max_turns=step_data.get("max_turns"),
                timeout=step_data.get("timeout"),
                resume=step_data.get("resume"),
                on=raw_on,
            )
        )

    raw_cycles = data.get("cycles", {})
    if raw_cycles is None:
        raw_cycles = {}
    if not isinstance(raw_cycles, dict):
        raise WorkflowValidationError(
            f"'cycles' must be a mapping, got {type(raw_cycles).__name__}"
        )

    cycles = []
    for cycle_name, cycle_data in raw_cycles.items():
        if not isinstance(cycle_data, dict):
            raise WorkflowValidationError(
                f"Cycle '{cycle_name}' must be a mapping, got {type(cycle_data).__name__}"
            )
        cycle_required = ("entry", "loop", "max_iterations", "on_exhaust")
        missing_cycle = [k for k in cycle_required if k not in cycle_data]
        if missing_cycle:
            raise WorkflowValidationError(
                f"Cycle '{cycle_name}' missing required key(s): {', '.join(missing_cycle)}"
            )
        raw_loop = cycle_data["loop"]
        if not isinstance(raw_loop, list):
            raise WorkflowValidationError(
                f"Cycle '{cycle_name}' 'loop' must be a list, got {type(raw_loop).__name__}"
            )
        raw_max_iter = cycle_data["max_iterations"]
        if not isinstance(raw_max_iter, int) or isinstance(raw_max_iter, bool):
            raise WorkflowValidationError(
                f"Cycle '{cycle_name}' 'max_iterations' must be an integer, "
                f"got {type(raw_max_iter).__name__}"
            )
        if raw_max_iter < 1:
            raise WorkflowValidationError(
                f"Cycle '{cycle_name}' 'max_iterations' must be >= 1, got {raw_max_iter}"
            )
        cycles.append(
            CycleDefinition(
                name=cycle_name,
                entry=cycle_data["entry"],
                loop=raw_loop,
                max_iterations=raw_max_iter,
                on_exhaust=cycle_data["on_exhaust"],
            )
        )

    execution_policy = data.get("execution_policy", "auto")
    if execution_policy not in VALID_EXECUTION_POLICIES:
        raise WorkflowValidationError(
            f"execution_policy must be one of {sorted(VALID_EXECUTION_POLICIES)}, "
            f"got '{execution_policy}'"
        )

    return Workflow(
        name=data.get("name", ""),
        description=data.get("description", ""),
        execution_policy=execution_policy,
        steps=steps,
        cycles=cycles,
    )


def validate_workflow(workflow: Workflow) -> None:
    """ワークフロー定義の静的検証。

    Args:
        workflow: 検証対象のワークフロー

    Raises:
        WorkflowValidationError: 検証エラーがある場合
    """
    errors: list[str] = []
    valid_verdicts = {"PASS", "RETRY", "BACK", "ABORT"}

    # ワークフローレベルの検証
    if not workflow.steps:
        errors.append("Workflow must have at least one step")

    # ステップレベルの検証
    for step in workflow.steps:
        # 1. resume 先が存在し、同一 agent であること
        if step.resume:
            target = workflow.find_step(step.resume)
            if not target:
                errors.append(f"Step '{step.id}' resumes unknown step '{step.resume}'")
            elif target.agent != step.agent:
                errors.append(
                    f"Step '{step.id}' resumes '{step.resume}' but agents differ "
                    f"({step.agent} != {target.agent})"
                )

        # 2. on の遷移先が存在すること
        for verdict, next_id in step.on.items():
            if next_id != "end" and not workflow.find_step(next_id):
                errors.append(
                    f"Step '{step.id}' transitions to unknown step '{next_id}' on {verdict}"
                )

        # 3. verdict 値が有効であること
        for verdict in step.on:
            if verdict not in valid_verdicts:
                errors.append(f"Step '{step.id}' has invalid verdict '{verdict}'")

    # サイクルレベルの検証
    for cycle in workflow.cycles:
        # 4. loop が非空であること
        if not cycle.loop:
            errors.append(f"Cycle '{cycle.name}' loop must not be empty")
            continue

        # 5. entry ステップが存在すること
        if not workflow.find_step(cycle.entry):
            errors.append(f"Cycle '{cycle.name}' entry step '{cycle.entry}' not found")

        # 6. loop 内ステップが存在すること
        for step_id in cycle.loop:
            if not workflow.find_step(step_id):
                errors.append(f"Cycle '{cycle.name}' loop step '{step_id}' not found")

        # 7. loop 末尾ステップが RETRY 時に loop 先頭へ遷移すること
        tail_step = workflow.find_step(cycle.loop[-1])
        if tail_step and tail_step.on.get("RETRY") != cycle.loop[0]:
            errors.append(
                f"Cycle '{cycle.name}' loop tail '{cycle.loop[-1]}' RETRY should "
                f"transition to loop head '{cycle.loop[0]}'"
            )

        # 8. entry/loop 内ステップが PASS 時にサイクル外へ遷移すること
        all_cycle_steps = {cycle.entry} | set(cycle.loop)
        has_exit = False
        for cycle_step_id in all_cycle_steps:
            cycle_step = workflow.find_step(cycle_step_id)
            if cycle_step and cycle_step.on.get("PASS") not in all_cycle_steps:
                has_exit = True
                break
        if not has_exit:
            errors.append(f"Cycle '{cycle.name}' has no exit (PASS never leaves the cycle)")

        # 9. on_exhaust が有効な verdict であること
        if cycle.on_exhaust not in valid_verdicts:
            errors.append(f"Cycle '{cycle.name}' on_exhaust '{cycle.on_exhaust}' is invalid")

    if errors:
        raise WorkflowValidationError(errors)
