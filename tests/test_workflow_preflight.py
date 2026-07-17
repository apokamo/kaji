"""Small and medium tests for the shared L1/L2/L3 workflow preflight."""

from __future__ import annotations

from pathlib import Path

import pytest

from kaji_harness.models import Step, Workflow
from kaji_harness.preflight import preflight_workflow, preflight_workflow_path
from kaji_harness.skill import SkillMetadata


def _write_skill(repo: Path, name: str, frontmatter: str = "") -> None:
    skill_dir = repo / ".claude" / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(f"{frontmatter}# {name}\n", encoding="utf-8")


def _write_workflow(repo: Path, body: str) -> Path:
    path = repo / "workflow.yaml"
    path.write_text(
        "name: test\n"
        "description: test\n"
        "requires_provider: any\n"
        "execution_policy: auto\n"
        "steps:\n"
        f"{body}",
        encoding="utf-8",
    )
    return path


@pytest.mark.medium
def test_preflight_path_returns_l1_error_without_workflow(tmp_path: Path) -> None:
    path = tmp_path / "workflow.yaml"
    path.write_text("name: broken\nsteps: not-a-list\n", encoding="utf-8")

    result = preflight_workflow_path(path, project_root=tmp_path, skill_dir=".claude/skills")

    assert result.workflow is None
    assert result.skill_metadata == {}
    assert result.errors
    assert result.warnings == []


@pytest.mark.medium
def test_preflight_path_aggregates_l2_and_l3_errors(tmp_path: Path) -> None:
    path = _write_workflow(
        tmp_path,
        "  - id: broken\n"
        "    skill: missing-skill\n"
        "    agent: claude\n"
        "    on:\n"
        "      PASS: missing-step\n",
    )

    result = preflight_workflow_path(path, project_root=tmp_path, skill_dir=".claude/skills")

    assert result.workflow is not None
    assert any("transitions to unknown step 'missing-step'" in error for error in result.errors)
    assert any("missing-skill/SKILL.md not found" in error for error in result.errors)


@pytest.mark.medium
def test_preflight_path_collects_exec_script_warning(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        "poll",
        "---\nname: poll\ndescription: poll\nexec_script: package.poll\n---\n",
    )
    path = _write_workflow(
        tmp_path,
        "  - id: poll\n"
        "    skill: poll\n"
        "    agent: claude\n"
        "    model: sonnet\n"
        "    effort: medium\n"
        "    on:\n"
        "      PASS: end\n",
    )

    result = preflight_workflow_path(path, project_root=tmp_path, skill_dir=".claude/skills")

    assert result.errors == []
    assert result.skill_metadata["poll"] is not None
    assert result.warnings == [
        "WARNING: Step 'poll' uses exec_script skill 'poll'; "
        "'agent' / 'model' / 'effort' are ignored."
    ]


@pytest.mark.medium
def test_preflight_path_rejects_agent_omission_without_exec_script(tmp_path: Path) -> None:
    _write_skill(tmp_path, "plain")
    path = _write_workflow(
        tmp_path,
        "  - id: plain\n    skill: plain\n    on:\n      PASS: end\n",
    )

    result = preflight_workflow_path(path, project_root=tmp_path, skill_dir=".claude/skills")

    assert result.errors == [
        "Step 'plain' omits 'agent' but skill 'plain' does not declare "
        "'exec_script' in its frontmatter; either set 'agent' on the step or add "
        "'exec_script' to the skill"
    ]


@pytest.mark.medium
def test_preflight_path_propagates_os_error(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        preflight_workflow_path(tmp_path, project_root=tmp_path, skill_dir=".claude/skills")


@pytest.mark.small
def test_preflight_workflow_uses_injected_skill_seams_without_io(tmp_path: Path) -> None:
    workflow = Workflow(
        name="test",
        description="test",
        execution_policy="auto",
        steps=[Step(id="done", skill="test-skill", agent="claude", on={"PASS": "end"})],
    )

    def validate_exists(skill_name: str, project_root: Path, skill_dir: str) -> Path:
        assert (skill_name, project_root, skill_dir) == (
            "test-skill",
            tmp_path,
            ".claude/skills",
        )
        return tmp_path / "SKILL.md"

    def load_metadata(skill_name: str, project_root: Path, skill_dir: str) -> SkillMetadata:
        assert (skill_name, project_root, skill_dir) == (
            "test-skill",
            tmp_path,
            ".claude/skills",
        )
        return SkillMetadata(name=skill_name, description="", exec_script=None)

    result = preflight_workflow(
        workflow,
        project_root=tmp_path,
        skill_dir=".claude/skills",
        skill_exists_validator=validate_exists,
        skill_metadata_loader=load_metadata,
    )

    assert result.errors == []
    assert result.warnings == []
    assert result.skill_metadata["done"] == SkillMetadata(
        name="test-skill", description="", exec_script=None
    )
