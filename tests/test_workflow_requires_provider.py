"""Phase 4 commit 2: ``Workflow.requires_provider`` field の文法検証。

Small テスト:

- ``_parse_workflow`` が ``requires_provider`` を enum として検証する
- 未指定時の default が ``"any"``
- ``validate_workflow`` も Workflow を直接構築した場合の enum 違反を検知する
"""

from __future__ import annotations

import pytest

from kaji_harness.errors import WorkflowValidationError
from kaji_harness.models import Step, Workflow
from kaji_harness.workflow import load_workflow_from_str, validate_workflow

_BASE_YAML = """
name: w
description: ""
execution_policy: auto
steps:
  - id: s1
    skill: noop
    agent: claude
    on:
      PASS: end
"""


def _yaml_with_requires_provider(value: str | None) -> str:
    if value is None:
        return _BASE_YAML
    return _BASE_YAML.rstrip() + f"\nrequires_provider: {value}\n"


@pytest.mark.small
def test_requires_provider_defaults_to_any() -> None:
    wf = load_workflow_from_str(_BASE_YAML)
    assert wf.requires_provider == "any"


@pytest.mark.small
@pytest.mark.parametrize("value", ["github", "local", "any"])
def test_requires_provider_accepts_valid_enum(value: str) -> None:
    wf = load_workflow_from_str(_yaml_with_requires_provider(value))
    assert wf.requires_provider == value


@pytest.mark.small
def test_requires_provider_rejects_unknown_string() -> None:
    with pytest.raises(WorkflowValidationError, match="requires_provider"):
        load_workflow_from_str(_yaml_with_requires_provider("bitbucket"))


@pytest.mark.small
def test_requires_provider_rejects_non_string() -> None:
    yaml_text = _BASE_YAML.rstrip() + "\nrequires_provider: 42\n"
    with pytest.raises(WorkflowValidationError, match="must be a string"):
        load_workflow_from_str(yaml_text)


@pytest.mark.small
def test_validate_workflow_catches_enum_violation_from_direct_construction() -> None:
    """``Workflow`` を YAML 経由でなく直接生成した場合も validate で検知する。"""
    step = Step(id="s1", skill="noop", agent="claude", on={"PASS": "end"})
    wf = Workflow(
        name="w",
        description="",
        execution_policy="auto",
        steps=[step],
    )
    # Bypass type checker to simulate stale construction
    wf.requires_provider = "forge"  # type: ignore[assignment]
    with pytest.raises(WorkflowValidationError) as ei:
        validate_workflow(wf)
    assert "requires_provider" in str(ei.value)


@pytest.mark.small
def test_validate_workflow_passes_with_default() -> None:
    step = Step(id="s1", skill="noop", agent="claude", on={"PASS": "end"})
    wf = Workflow(
        name="w",
        description="",
        execution_policy="auto",
        steps=[step],
    )
    # Should not raise
    validate_workflow(wf)
    assert wf.requires_provider == "any"
