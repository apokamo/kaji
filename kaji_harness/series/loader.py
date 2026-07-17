"""Load and validate series YAML files against repository workflows."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from ..config import KajiConfig
from ..errors import SeriesValidationError
from ..preflight import preflight_workflow_path
from .models import SeriesConfig


def _format_pydantic_errors(error: ValidationError) -> list[str]:
    """Render stable field-oriented Pydantic validation messages."""
    rendered: list[str] = []
    for item in error.errors(include_url=False):
        location = ".".join(str(part) for part in item["loc"]) or "series"
        rendered.append(f"{location}: {item['msg']}")
    return rendered


def load_series(path: Path, config: KajiConfig) -> SeriesConfig:
    """Load one series file and validate every referenced workflow.

    Raises:
        SeriesValidationError: YAML, schema, path, or workflow validation fails.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SeriesValidationError(f"could not read {path}: {exc}") from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SeriesValidationError(f"YAML parse error: {exc}") from exc
    try:
        series = SeriesConfig.model_validate(raw)
    except ValidationError as exc:
        raise SeriesValidationError(_format_pydantic_errors(exc)) from exc

    errors: list[str] = []
    repo_root = config.repo_root.resolve()
    for index, member in enumerate(series.members):
        candidate = repo_root / member.workflow
        resolved = candidate.resolve()
        try:
            resolved.relative_to(repo_root)
        except ValueError:
            errors.append(
                f"members.{index}.workflow must resolve inside repo root: {member.workflow}"
            )
            continue
        if not resolved.is_file():
            errors.append(f"members.{index}.workflow not found: {member.workflow}")
            continue
        try:
            result = preflight_workflow_path(
                resolved,
                project_root=repo_root,
                skill_dir=config.paths.skill_dir,
            )
        except OSError as exc:
            errors.append(
                f"members.{index}.workflow could not be loaded ({member.workflow}): {exc}"
            )
            continue
        if result.errors:
            errors.extend(
                f"members.{index}.workflow is invalid ({member.workflow}): {error}"
                for error in result.errors
            )
            continue
        assert result.workflow is not None
        workflow = result.workflow
        if workflow.requires_provider not in {"github", "any"}:
            errors.append(
                f"members.{index}.workflow requires provider "
                f"{workflow.requires_provider!r}, expected 'github' or 'any'"
            )
    if errors:
        raise SeriesValidationError(errors)
    return series
