"""Workflow YAML loader and validator for kaji_harness."""

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
        if "on" in step_data:
            raw_on = step_data["on"]
        elif True in step_data:
            # YAML 1.1 interprets bare `on` as boolean True
            raw_on = step_data[True]
        else:
            raise WorkflowValidationError(f"Step '{step_data['id']}' missing required key 'on'")
        if not isinstance(raw_on, dict):
            raise WorkflowValidationError(
                f"Step '{step_data['id']}' 'on' must be a mapping, got {type(raw_on).__name__}"
            )
        if not raw_on:
            raise WorkflowValidationError(f"Step '{step_data['id']}' 'on' must not be empty")
        raw_inject_verdict = step_data.get("inject_verdict", False)
        if not isinstance(raw_inject_verdict, bool):
            raise WorkflowValidationError(
                f"Step '{step_data['id']}' 'inject_verdict' must be a boolean, "
                f"got {type(raw_inject_verdict).__name__}"
            )
        raw_step_workdir = step_data.get("workdir")
        if raw_step_workdir is not None:
            if not isinstance(raw_step_workdir, str):
                raise WorkflowValidationError(
                    f"Step '{step_data['id']}' 'workdir' must be a string, "
                    f"got {type(raw_step_workdir).__name__}"
                )
            if not raw_step_workdir:
                raise WorkflowValidationError(
                    f"Step '{step_data['id']}' 'workdir' must not be empty"
                )
            try:
                expanded_step_workdir = Path(raw_step_workdir).expanduser()
            except RuntimeError as e:
                raise WorkflowValidationError(
                    f"Step '{step_data['id']}' 'workdir' expansion failed: {e}"
                ) from e
            if not expanded_step_workdir.is_absolute():
                raise WorkflowValidationError(
                    f"Step '{step_data['id']}' 'workdir' must be an absolute path, "
                    f"got '{raw_step_workdir}'"
                )
            raw_step_workdir = str(expanded_step_workdir)

        raw_timeout = step_data.get("timeout")
        if raw_timeout is not None:
            if not isinstance(raw_timeout, int) or isinstance(raw_timeout, bool):
                raise WorkflowValidationError(
                    f"Step '{step_data['id']}' 'timeout' must be an integer, "
                    f"got {type(raw_timeout).__name__}"
                )
            if raw_timeout <= 0:
                raise WorkflowValidationError(
                    f"Step '{step_data['id']}' 'timeout' must be a positive integer, "
                    f"got {raw_timeout}"
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
                timeout=raw_timeout,
                workdir=raw_step_workdir,
                resume=step_data.get("resume"),
                inject_verdict=raw_inject_verdict,
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

    execution_policy = data.get("execution_policy")
    if execution_policy is None:
        raise WorkflowValidationError("'execution_policy' is required")
    if execution_policy not in VALID_EXECUTION_POLICIES:
        raise WorkflowValidationError(
            f"execution_policy must be one of {sorted(VALID_EXECUTION_POLICIES)}, "
            f"got '{execution_policy}'"
        )

    raw_default_timeout = data.get("default_timeout")
    if raw_default_timeout is not None:
        if not isinstance(raw_default_timeout, int) or isinstance(raw_default_timeout, bool):
            raise WorkflowValidationError(
                f"'default_timeout' must be an integer, got {type(raw_default_timeout).__name__}"
            )
        if raw_default_timeout <= 0:
            raise WorkflowValidationError(
                f"'default_timeout' must be a positive integer, got {raw_default_timeout}"
            )

    raw_workdir = data.get("workdir")
    if raw_workdir is not None:
        if not isinstance(raw_workdir, str):
            raise WorkflowValidationError(
                f"'workdir' must be a string, got {type(raw_workdir).__name__}"
            )
        if not raw_workdir:
            raise WorkflowValidationError("'workdir' must not be empty")
        try:
            expanded_workdir = Path(raw_workdir).expanduser()
        except RuntimeError as e:
            raise WorkflowValidationError(f"'workdir' expansion failed: {e}") from e
        if not expanded_workdir.is_absolute():
            raise WorkflowValidationError(
                f"'workdir' must be an absolute path, got '{raw_workdir}'"
            )
        raw_workdir = str(expanded_workdir)

    return Workflow(
        name=data.get("name", ""),
        description=data.get("description", ""),
        execution_policy=execution_policy,
        steps=steps,
        cycles=cycles,
        default_timeout=raw_default_timeout,
        workdir=raw_workdir,
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
    # on が不正な step id を収集。cycle 遷移チェック（.on.get() 呼び出し）から除外するために使用する
    invalid_on_step_ids: set[str] = set()

    # ---- スキーマレベルのバリデーション ----
    # default_timeout の検証（_parse_workflow() を経由しない場合も担保）
    if workflow.default_timeout is not None:
        if (
            not isinstance(workflow.default_timeout, int)
            or isinstance(workflow.default_timeout, bool)
            or workflow.default_timeout <= 0
        ):
            errors.append(
                f"'default_timeout' must be a positive integer, got {workflow.default_timeout!r}"
            )

    # execution_policy の enum 検証（_parse_workflow() を経由しない場合も担保）
    if workflow.execution_policy not in VALID_EXECUTION_POLICIES:
        errors.append(
            f"execution_policy must be one of {sorted(VALID_EXECUTION_POLICIES)}, "
            f"got '{workflow.execution_policy}'"
        )

    # workdir の検証（_parse_workflow() を経由しない場合も担保）
    if workflow.workdir is not None:
        if not isinstance(workflow.workdir, str) or not workflow.workdir:
            errors.append(f"'workdir' must be a non-empty string, got {workflow.workdir!r}")
        elif not Path(workflow.workdir).is_absolute():
            errors.append(f"'workdir' must be an absolute path, got '{workflow.workdir}'")

    # ワークフローレベルの検証
    if not workflow.steps:
        errors.append("Workflow must have at least one step")

    # ステップレベルの検証
    for step in workflow.steps:
        # スキーマ: step.timeout の検証（_parse_workflow() を経由しない場合も担保）
        if step.timeout is not None:
            if (
                not isinstance(step.timeout, int)
                or isinstance(step.timeout, bool)
                or step.timeout <= 0
            ):
                errors.append(
                    f"Step '{step.id}' 'timeout' must be a positive integer, got {step.timeout!r}"
                )

        # スキーマ: step.workdir の検証（_parse_workflow() を経由しない場合も担保）
        if step.workdir is not None:
            if not isinstance(step.workdir, str) or not step.workdir:
                errors.append(
                    f"Step '{step.id}' 'workdir' must be a non-empty string, got {step.workdir!r}"
                )
            elif not Path(step.workdir).is_absolute():
                errors.append(
                    f"Step '{step.id}' 'workdir' must be an absolute path, got '{step.workdir}'"
                )

        # スキーマ: step.on は非空の dict であること
        if not isinstance(step.on, dict) or not step.on:
            errors.append(f"Step '{step.id}' 'on' must be a non-empty mapping")
            invalid_on_step_ids.add(step.id)
            # on が不正な場合、以降の遷移検証はスキップ
            if step.resume:
                target = workflow.find_step(step.resume)
                if not target:
                    errors.append(f"Step '{step.id}' resumes unknown step '{step.resume}'")
                elif target.agent != step.agent:
                    errors.append(
                        f"Step '{step.id}' resumes '{step.resume}' but agents differ "
                        f"({step.agent} != {target.agent})"
                    )
            continue

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
        # スキーマ: cycle.loop は list であること
        if not isinstance(cycle.loop, list):
            errors.append(
                f"Cycle '{cycle.name}' 'loop' must be a list, got {type(cycle.loop).__name__}"
            )
            continue

        # スキーマ: cycle.max_iterations は正の整数であること
        if (
            not isinstance(cycle.max_iterations, int)
            or isinstance(cycle.max_iterations, bool)
            or cycle.max_iterations < 1
        ):
            errors.append(
                f"Cycle '{cycle.name}' 'max_iterations' must be an integer >= 1, "
                f"got {cycle.max_iterations!r}"
            )

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
        # step.on が不正な場合は .get() を呼ばず、この検証をスキップする
        tail_step = workflow.find_step(cycle.loop[-1])
        if (
            tail_step
            and tail_step.id not in invalid_on_step_ids
            and tail_step.on.get("RETRY") != cycle.loop[0]
        ):
            errors.append(
                f"Cycle '{cycle.name}' loop tail '{cycle.loop[-1]}' RETRY should "
                f"transition to loop head '{cycle.loop[0]}'"
            )

        # 8. entry/loop 内ステップが PASS 時にサイクル外へ遷移すること
        # step.on が不正なステップは exit 判定から除外する
        all_cycle_steps = {cycle.entry} | set(cycle.loop)
        has_exit = False
        for cycle_step_id in all_cycle_steps:
            if cycle_step_id in invalid_on_step_ids:
                continue
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
