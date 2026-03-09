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


def _parse_workflow(data: dict[str, Any]) -> Workflow:
    """YAML data dict をワークフローオブジェクトに変換する。"""
    if not isinstance(data, dict):
        raise WorkflowValidationError("Workflow definition must be a YAML mapping")

    steps = []
    for step_data in data.get("steps", []):
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
                on=step_data.get("on") or step_data.get(True) or {},
            )
        )

    cycles = []
    for cycle_name, cycle_data in data.get("cycles", {}).items():
        cycles.append(
            CycleDefinition(
                name=cycle_name,
                entry=cycle_data["entry"],
                loop=cycle_data["loop"],
                max_iterations=cycle_data["max_iterations"],
                on_exhaust=cycle_data["on_exhaust"],
            )
        )

    return Workflow(
        name=data.get("name", ""),
        description=data.get("description", ""),
        execution_policy=data.get("execution_policy", "auto"),
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
        # 4. entry ステップが存在すること
        if not workflow.find_step(cycle.entry):
            errors.append(f"Cycle '{cycle.name}' entry step '{cycle.entry}' not found")

        # 5. loop 内ステップが存在すること
        for step_id in cycle.loop:
            if not workflow.find_step(step_id):
                errors.append(f"Cycle '{cycle.name}' loop step '{step_id}' not found")

        # 6. loop 末尾ステップが RETRY 時に loop 先頭へ遷移すること
        tail_step = workflow.find_step(cycle.loop[-1])
        if tail_step and tail_step.on.get("RETRY") != cycle.loop[0]:
            errors.append(
                f"Cycle '{cycle.name}' loop tail '{cycle.loop[-1]}' RETRY should "
                f"transition to loop head '{cycle.loop[0]}'"
            )

        # 7. entry/loop 内ステップが PASS 時にサイクル外へ遷移すること
        all_cycle_steps = {cycle.entry} | set(cycle.loop)
        has_exit = False
        for cycle_step_id in all_cycle_steps:
            cycle_step = workflow.find_step(cycle_step_id)
            if cycle_step and cycle_step.on.get("PASS") not in all_cycle_steps:
                has_exit = True
                break
        if not has_exit:
            errors.append(f"Cycle '{cycle.name}' has no exit (PASS never leaves the cycle)")

        # 8. on_exhaust が有効な verdict であること
        if cycle.on_exhaust not in valid_verdicts:
            errors.append(f"Cycle '{cycle.name}' on_exhaust '{cycle.on_exhaust}' is invalid")

    if errors:
        raise WorkflowValidationError(errors)
