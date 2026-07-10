"""Medium tests: workflow YAML schema for optional agent (Issue #204)."""

from __future__ import annotations

import pytest

from kaji_harness.errors import WorkflowValidationError
from kaji_harness.workflow import load_workflow_from_str, validate_workflow


@pytest.mark.medium
class TestAgentOptionalSchema:
    def test_yaml_parses_step_without_agent(self) -> None:
        yaml_str = """
name: t
description: t
execution_policy: auto
steps:
  - id: poll
    skill: review-poll
    on:
      PASS: end
"""
        wf = load_workflow_from_str(yaml_str)
        assert len(wf.steps) == 1
        assert wf.steps[0].agent is None

    def test_yaml_explicit_null_agent(self) -> None:
        yaml_str = """
name: t
description: t
execution_policy: auto
steps:
  - id: poll
    skill: review-poll
    agent: null
    on:
      PASS: end
"""
        wf = load_workflow_from_str(yaml_str)
        assert wf.steps[0].agent is None

    def test_validate_workflow_allows_agent_none(self) -> None:
        yaml_str = """
name: t
description: t
execution_policy: auto
steps:
  - id: poll
    skill: review-poll
    on:
      PASS: end
"""
        wf = load_workflow_from_str(yaml_str)
        # skill-metadata 非依存の検証は L1/L2 の責務分離のため成功する
        validate_workflow(wf)

    def test_yaml_rejects_non_string_agent(self) -> None:
        yaml_str = """
name: t
description: t
execution_policy: auto
steps:
  - id: poll
    skill: review-poll
    agent: 42
    on:
      PASS: end
"""
        with pytest.raises(WorkflowValidationError):
            load_workflow_from_str(yaml_str)

    def test_dev_yaml_still_loads(self) -> None:
        """dev.yaml の review-poll step が agent 無しで parse できる。"""
        from pathlib import Path

        from kaji_harness.workflow import load_workflow

        path = Path(__file__).resolve().parents[1] / ".kaji" / "wf" / "dev.yaml"
        wf = load_workflow(path)
        poll = next(s for s in wf.steps if s.id == "review-poll")
        assert poll.agent is None
