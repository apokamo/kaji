"""Shared L1/L2/L3 workflow preflight validation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .errors import (
    SecurityError,
    SkillFrontmatterError,
    SkillNotFound,
    WorkflowValidationError,
)
from .models import Workflow
from .skill import SkillMetadata, load_skill_metadata, validate_skill_exists
from .workflow import load_workflow, validate_workflow

SkillExistsValidator = Callable[[str, Path, str], Path]
SkillMetadataLoader = Callable[[str, Path, str], SkillMetadata]

# #381: inject_verdict は次回 minor release で削除予定。#383 で本定数と
# _deprecated_field_warnings() を inject_verdict / inject_verdict_declared と共に削除する。
_INJECT_VERDICT_DEPRECATION = (
    "WARNING: 'inject_verdict' is deprecated and will be removed in the next minor "
    "release (apokamo/kaji#310); step(s) {steps}: remove the field and use "
    "'resume: <step-id>' for same-agent session continuation, or read the prior "
    "verdict with 'kaji issue resolve-verdict <issue-id> --step <step-id>'."
)


def _deprecated_field_warnings(workflow: Workflow) -> list[str]:
    """廃止予定 field の警告を workflow 単位で 1 件に集約する。"""
    step_ids = [step.id for step in workflow.steps if step.inject_verdict_declared]
    if not step_ids:
        return []
    # step ID は利用者入力。repr() で 1 行にエスケープしてから埋め込む。
    return [_INJECT_VERDICT_DEPRECATION.format(steps=", ".join(repr(sid) for sid in step_ids))]


@dataclass(frozen=True)
class WorkflowPreflightResult:
    """Store the structured result of one workflow preflight.

    Attributes:
        workflow: Loaded workflow, or None when L1 validation failed.
        skill_metadata: Metadata resolved for each executable step.
        errors: Aggregated L1, L2, and L3 definition errors.
        warnings: Non-fatal compatibility warnings.
    """

    workflow: Workflow | None
    skill_metadata: dict[str, SkillMetadata | None]
    errors: list[str]
    warnings: list[str]


def preflight_workflow(
    workflow: Workflow,
    *,
    project_root: Path,
    skill_dir: str,
    skill_exists_validator: SkillExistsValidator | None = None,
    skill_metadata_loader: SkillMetadataLoader | None = None,
) -> WorkflowPreflightResult:
    """Apply L2 and L3 validation to a loaded workflow.

    The injectable skill functions preserve the runner's established test seam;
    production callers use the shared skill implementation by default.

    Args:
        workflow: Workflow that already passed L1 parsing.
        project_root: Root used to resolve skill files.
        skill_dir: Skill directory relative to the project root.
        skill_exists_validator: Optional test seam for skill path validation.
        skill_metadata_loader: Optional test seam for metadata loading.

    Returns:
        Structured validation result containing all definition errors.

    Raises:
        OSError: A workflow skill file could not be read.
    """
    errors: list[str] = []
    warnings: list[str] = _deprecated_field_warnings(workflow)
    skill_metadata: dict[str, SkillMetadata | None] = {}
    validate_exists = skill_exists_validator or validate_skill_exists
    load_metadata = skill_metadata_loader or load_skill_metadata

    try:
        validate_workflow(workflow)
    except WorkflowValidationError as exc:
        errors.extend(exc.errors)

    for step in workflow.steps:
        if step.exec is not None:
            skill_metadata[step.id] = None
            continue
        if step.skill is None:
            continue
        try:
            validate_exists(step.skill, project_root, skill_dir)
            metadata = load_metadata(step.skill, project_root, skill_dir)
        except (SkillNotFound, SecurityError, SkillFrontmatterError) as exc:
            errors.append(str(exc))
            continue
        skill_metadata[step.id] = metadata
        if step.agent is None and metadata.exec_script is None:
            errors.append(
                f"Step '{step.id}' omits 'agent' but skill '{step.skill}' does "
                "not declare 'exec_script' in its frontmatter; either set "
                "'agent' on the step or add 'exec_script' to the skill"
            )
        if metadata.exec_script is not None and (
            step.agent is not None or step.model is not None or step.effort is not None
        ):
            warnings.append(
                f"WARNING: Step '{step.id}' uses exec_script skill "
                f"'{step.skill}'; 'agent' / 'model' / 'effort' are ignored."
            )

    return WorkflowPreflightResult(
        workflow=workflow,
        skill_metadata=skill_metadata,
        errors=errors,
        warnings=warnings,
    )


def preflight_workflow_path(
    path: Path, *, project_root: Path, skill_dir: str
) -> WorkflowPreflightResult:
    """Load a workflow and apply L1, L2, and L3 validation.

    Args:
        path: Workflow YAML path.
        project_root: Root used to resolve skill files.
        skill_dir: Skill directory relative to the project root.

    Returns:
        Structured validation result. L1 failures have ``workflow=None``.

    Raises:
        OSError: The workflow or a referenced skill file could not be read.
    """
    try:
        workflow = load_workflow(path)
    except WorkflowValidationError as exc:
        return WorkflowPreflightResult(
            workflow=None,
            skill_metadata={},
            errors=exc.errors,
            warnings=[],
        )
    return preflight_workflow(workflow, project_root=project_root, skill_dir=skill_dir)
