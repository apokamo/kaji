"""Structure and asset invariants for ``.kaji/wf/official/incident.yaml`` (第2層 / Issue #305).

``incident.yaml`` is a declarative definition driven by the existing workflow engine,
so it is guarded as a runtime-behaviour surface (not metadata-only). These invariants
fix the review-convergence structure, the conclusion-vs-verdict axis separation
(#303 決定 D), the default model separation (#303 決定 B), and the existence of the
skill / agent / template assets the workflow references. Prompt *quality* is not
machine-checkable and is covered by the change-specific manual verification instead.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from kaji_harness.workflow import load_workflow, validate_workflow

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WF_PATH = REPO_ROOT / ".kaji" / "wf" / "official" / "incident.yaml"
SKILLS_DIR = REPO_ROOT / ".claude" / "skills"
AGENT_PATH = REPO_ROOT / ".claude" / "agents" / "kaji-incident-reviewer.md"
TEMPLATE_PATH = SKILLS_DIR / "incident-investigate" / "artifact-template.md"
CYCLE_SKILL_PATH = SKILLS_DIR / "incident-cycle" / "SKILL.md"

EXPECTED_STEPS = {"investigate", "review", "fix", "verify", "report"}
CONCLUSION_VALUES = (
    "internal-bug",
    "upstream",
    "environment",
    "transient",
    "duplicate",
    "INCONCLUSIVE",
)
TEMPLATE_REQUIRED_HEADINGS = (
    "メタデータ",
    "可読サマリ",
    "結論",
    "根拠",
    "棄却済み仮説",
    "不足証拠",
)
# risk-accepted は人間専用語彙。ファイル内に現れてよいのは「禁止の説明」文脈のみで、
# エージェント出力語彙（結論値・推奨値・列挙）として現れてはならない（#303 決定 D）。
_PROHIBITION_MARKERS = ("人間専用", "含めない", "human-only")


def _load_frontmatter_model(md_path: Path) -> str:
    """agent markdown の YAML frontmatter から ``model:`` を取り出す。"""
    text = md_path.read_text(encoding="utf-8")
    assert text.startswith("---"), f"{md_path} must start with a YAML frontmatter block"
    _, fm, _ = text.split("---", 2)
    data = yaml.safe_load(fm)
    model = data.get("model")
    assert isinstance(model, str) and model, f"{md_path} frontmatter must declare a model"
    return model


@pytest.mark.medium
class TestIncidentWorkflowStructure:
    def test_loads_and_validates(self) -> None:
        wf = load_workflow(WF_PATH)
        validate_workflow(wf)  # raises on invariant violation
        assert wf.name == "incident"

    def test_workflow_level_invariants(self) -> None:
        wf = load_workflow(WF_PATH)
        assert wf.requires_provider == "github"
        assert wf.execution_policy == "auto"

    def test_step_set(self) -> None:
        wf = load_workflow(WF_PATH)
        assert {s.id for s in wf.steps} == EXPECTED_STEPS

    def test_cycle_definition(self) -> None:
        wf = load_workflow(WF_PATH)
        assert len(wf.cycles) == 1
        cycle = wf.cycles[0]
        assert cycle.name == "incident-review"
        assert cycle.entry == "review"
        assert cycle.loop == ["fix", "verify"]
        assert cycle.max_iterations == 3
        assert cycle.on_exhaust == "ABORT"


@pytest.mark.medium
class TestIncidentWorkflowTransitions:
    def test_loop_tail_retry_targets_loop_head(self) -> None:
        wf = load_workflow(WF_PATH)
        verify = wf.find_step("verify")
        assert verify is not None
        assert verify.on["RETRY"] == "fix"

    def test_review_and_verify_pass_join_at_report(self) -> None:
        wf = load_workflow(WF_PATH)
        review = wf.find_step("review")
        verify = wf.find_step("verify")
        assert review is not None and verify is not None
        assert review.on["PASS"] == "report"
        assert verify.on["PASS"] == "report"

    def test_only_base_verdicts_used(self) -> None:
        """全 on キーが {PASS, RETRY, ABORT} の範囲内（BACK 系なし）。"""
        wf = load_workflow(WF_PATH)
        allowed = {"PASS", "RETRY", "ABORT"}
        for step in wf.steps:
            assert set(step.on) <= allowed, f"step {step.id} uses non-{allowed} verdict"

    def test_fix_resumes_investigate_same_agent(self) -> None:
        wf = load_workflow(WF_PATH)
        fix = wf.find_step("fix")
        investigate = wf.find_step("investigate")
        assert fix is not None and investigate is not None
        assert fix.resume == "investigate"
        assert fix.agent == investigate.agent


@pytest.mark.medium
class TestIncidentModelSeparation:
    def test_investigate_and_reviewer_models_differ(self) -> None:
        """提案役（investigate）と査読役（agent frontmatter）のデフォルトモデルが異なること。"""
        wf = load_workflow(WF_PATH)
        investigate = wf.find_step("investigate")
        assert investigate is not None
        reviewer_model = _load_frontmatter_model(AGENT_PATH)
        assert investigate.model is not None
        assert investigate.model != reviewer_model


@pytest.mark.medium
class TestIncidentAssetsExist:
    @pytest.mark.parametrize(
        "skill_name",
        [
            "incident-investigate",
            "incident-review",
            "incident-fix",
            "incident-verify",
            "incident-report",
        ],
    )
    def test_referenced_skill_exists(self, skill_name: str) -> None:
        wf = load_workflow(WF_PATH)
        referenced = {s.skill for s in wf.steps if s.skill}
        assert skill_name in referenced
        assert (SKILLS_DIR / skill_name / "SKILL.md").is_file()

    def test_agent_definition_exists(self) -> None:
        assert AGENT_PATH.is_file()

    def test_template_exists_with_required_headings(self) -> None:
        assert TEMPLATE_PATH.is_file()
        text = TEMPLATE_PATH.read_text(encoding="utf-8")
        for heading in TEMPLATE_REQUIRED_HEADINGS:
            assert re.search(rf"^#+\s+{re.escape(heading)}", text, re.MULTILINE), (
                f"template missing required heading: {heading}"
            )

    def test_template_lists_all_conclusion_values(self) -> None:
        text = TEMPLATE_PATH.read_text(encoding="utf-8")
        for value in CONCLUSION_VALUES:
            assert value in text, f"template missing conclusion value: {value}"


@pytest.mark.medium
class TestIncidentCycleSlashWrapper:
    """slash wrapper は workflow から参照されないため明示的に検証する。"""

    def test_file_exists(self) -> None:
        assert CYCLE_SKILL_PATH.is_file()

    def test_launches_incident_workflow(self) -> None:
        text = CYCLE_SKILL_PATH.read_text(encoding="utf-8")
        assert ".kaji/wf/official/incident.yaml" in text

    def test_missing_argument_abort_path(self) -> None:
        text = CYCLE_SKILL_PATH.read_text(encoding="utf-8")
        assert "usage: /incident-cycle" in text
        assert "status: ABORT" in text

    def test_exit_code_to_verdict_contract(self) -> None:
        text = CYCLE_SKILL_PATH.read_text(encoding="utf-8")
        # 0 → PASS / 非 0 → ABORT の縮約契約が記述されていること
        assert "status: PASS" in text
        assert "status: ABORT" in text
        assert re.search(r"exit\s*(code)?\s*0", text, re.IGNORECASE)


@pytest.mark.medium
class TestIncidentForbiddenVocabulary:
    """`risk-accepted` は人間専用語彙。出力語彙として現れないこと（#303 決定 D）。"""

    @pytest.mark.parametrize("path", [TEMPLATE_PATH, AGENT_PATH])
    def test_risk_accepted_only_in_prohibition_context(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if "risk-accepted" in line:
                assert any(marker in line for marker in _PROHIBITION_MARKERS), (
                    f"{path.name}: 'risk-accepted' appears outside a prohibition context: {line!r}"
                )

    @pytest.mark.parametrize("value", CONCLUSION_VALUES)
    def test_risk_accepted_not_a_conclusion_value(self, value: str) -> None:
        assert value != "risk-accepted"
