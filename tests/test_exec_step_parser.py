"""Small tests: exec step parsing / validation (Issue #205).

workflow.yaml の ``exec:`` step フィールドについて、正規化（str→argv / list 保持）・
skill との排他・agent 専用フィールドの拒否・型/空検証・``validate_workflow`` ミラー検証・
``ScriptExecutionError`` 文言一般化を、外部依存なしの純ロジックとして検証する。
"""

from __future__ import annotations

import pytest

from kaji_harness.errors import ScriptExecutionError, WorkflowValidationError
from kaji_harness.models import Step, Workflow
from kaji_harness.workflow import load_workflow_from_str, validate_workflow

_DEFAULT_ON = ["on:", "  PASS: end", "  ABORT: end"]


def _wf_yaml(step_lines: list[str]) -> str:
    """1 step を持つ最小 workflow YAML を column-0 から組み立てる。

    ``step_lines`` は step の連続キー（``id`` 直下に 4-space で並ぶ）を表す文字列群。
    dedent の common-indent 計算に依存しないため、改行を含む値でも壊れない。
    """
    body = "\n".join(f"    {ln}" for ln in step_lines)
    return f"name: t\ndescription: t\nexecution_policy: auto\nsteps:\n  - id: s\n{body}\n"


@pytest.mark.small
class TestExecNormalize:
    def test_str_form_is_shlex_split(self) -> None:
        wf = load_workflow_from_str(
            _wf_yaml(["exec: python -m kaji_harness.scripts.foo", *_DEFAULT_ON])
        )
        step = wf.steps[0]
        assert step.exec == ["python", "-m", "kaji_harness.scripts.foo"]
        assert step.skill is None

    def test_str_form_respects_quotes(self) -> None:
        wf = load_workflow_from_str(_wf_yaml(['exec: echo "a b" c', *_DEFAULT_ON]))
        assert wf.steps[0].exec == ["echo", "a b", "c"]

    def test_list_form_is_preserved(self) -> None:
        wf = load_workflow_from_str(
            _wf_yaml(
                [
                    'exec: ["python", "-m", "kaji_harness.scripts.foo", "--issue", "205"]',
                    *_DEFAULT_ON,
                ]
            )
        )
        assert wf.steps[0].exec == [
            "python",
            "-m",
            "kaji_harness.scripts.foo",
            "--issue",
            "205",
        ]


@pytest.mark.small
class TestExecExclusivity:
    def test_neither_skill_nor_exec_errors(self) -> None:
        with pytest.raises(WorkflowValidationError, match="exactly one of 'skill' or 'exec'"):
            load_workflow_from_str(_wf_yaml(_DEFAULT_ON))

    def test_both_skill_and_exec_errors(self) -> None:
        with pytest.raises(WorkflowValidationError, match="exactly one of 'skill' or 'exec'"):
            load_workflow_from_str(_wf_yaml(["exec: python -m foo", "skill: design", *_DEFAULT_ON]))

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("agent", "claude"),
            ("model", "sonnet"),
            ("effort", "high"),
            ("resume", "s"),
            ("inject_verdict", "true"),
            ("max_budget_usd", "5.0"),
        ],
    )
    def test_exec_with_forbidden_agent_field_errors(self, field: str, value: str) -> None:
        with pytest.raises(WorkflowValidationError, match=f"must not set '{field}'"):
            load_workflow_from_str(
                _wf_yaml(["exec: python -m foo", f"{field}: {value}", *_DEFAULT_ON])
            )


@pytest.mark.small
class TestExecTypeEmpty:
    def test_empty_string_errors(self) -> None:
        with pytest.raises(WorkflowValidationError, match="empty command"):
            load_workflow_from_str(_wf_yaml(['exec: ""', *_DEFAULT_ON]))

    def test_empty_list_errors(self) -> None:
        with pytest.raises(WorkflowValidationError, match="empty list"):
            load_workflow_from_str(_wf_yaml(["exec: []", *_DEFAULT_ON]))

    def test_non_str_list_element_errors(self) -> None:
        with pytest.raises(WorkflowValidationError, match="non-empty strings"):
            load_workflow_from_str(_wf_yaml(["exec: [123]", *_DEFAULT_ON]))

    def test_empty_str_list_element_errors(self) -> None:
        with pytest.raises(WorkflowValidationError, match="non-empty strings"):
            load_workflow_from_str(_wf_yaml(['exec: ["python", ""]', *_DEFAULT_ON]))

    def test_non_str_or_list_type_errors(self) -> None:
        with pytest.raises(WorkflowValidationError, match="must be a string or a list"):
            load_workflow_from_str(_wf_yaml(["exec: 42", *_DEFAULT_ON]))


@pytest.mark.small
class TestStepDataclass:
    def test_exec_default_is_none(self) -> None:
        step = Step(id="s", skill="design", agent="claude", on={"PASS": "end"})
        assert step.exec is None

    def test_skill_only_step_constructs(self) -> None:
        step = Step(id="s", skill="design", agent="claude", on={"PASS": "end"})
        assert step.skill == "design"
        assert step.exec is None

    def test_exec_only_step_constructs(self) -> None:
        step = Step(id="s", exec=["python", "-m", "foo"], on={"PASS": "end"})
        assert step.skill is None
        assert step.exec == ["python", "-m", "foo"]


@pytest.mark.small
class TestValidateWorkflowMirror:
    """parser を経由せず手組みした Workflow でも排他検証が効く（defense-in-depth）。"""

    def _wf(self, step: Step) -> Workflow:
        return Workflow(name="t", description="t", execution_policy="auto", steps=[step])

    def test_both_skill_and_exec_errors(self) -> None:
        step = Step(id="s", skill="design", exec=["python", "-m", "foo"], on={"PASS": "end"})
        with pytest.raises(WorkflowValidationError, match="exactly one of 'skill' or 'exec'"):
            validate_workflow(self._wf(step))

    def test_neither_skill_nor_exec_errors(self) -> None:
        step = Step(id="s", on={"PASS": "end"})
        with pytest.raises(WorkflowValidationError, match="exactly one of 'skill' or 'exec'"):
            validate_workflow(self._wf(step))

    def test_exec_with_agent_errors(self) -> None:
        step = Step(id="s", exec=["python", "-m", "foo"], agent="claude", on={"PASS": "end"})
        with pytest.raises(WorkflowValidationError, match="must not set 'agent'"):
            validate_workflow(self._wf(step))

    def test_exec_with_inject_verdict_errors(self) -> None:
        step = Step(id="s", exec=["python", "-m", "foo"], inject_verdict=True, on={"PASS": "end"})
        with pytest.raises(WorkflowValidationError, match="must not set 'inject_verdict'"):
            validate_workflow(self._wf(step))

    def test_exec_empty_list_errors(self) -> None:
        step = Step(id="s", exec=[], on={"PASS": "end"})
        with pytest.raises(WorkflowValidationError, match="non-empty list"):
            validate_workflow(self._wf(step))

    def test_valid_exec_step_passes(self) -> None:
        step = Step(id="s", exec=["python", "-m", "foo"], on={"PASS": "end"})
        # 例外を送出しないこと（skill-step でなくても通る）。
        validate_workflow(self._wf(step))


@pytest.mark.small
class TestScriptExecutionErrorGeneralization:
    """ScriptExecutionError は exec / exec_script 共通の中立表現に一般化されている。"""

    def test_command_label_reflected_in_message(self) -> None:
        err = ScriptExecutionError("s1", "python -m kaji_harness.scripts.foo", 2, "boom")
        assert "python -m kaji_harness.scripts.foo" in str(err)
        assert err.command_label == "python -m kaji_harness.scripts.foo"

    def test_message_has_no_exec_script_specific_wording(self) -> None:
        err = ScriptExecutionError("s1", "review_poll.entry", 1, "stderr")
        assert "exec_script" not in str(err)
        assert "deterministic command" in str(err)

    def test_returncode_and_stderr_preserved(self) -> None:
        err = ScriptExecutionError("s1", "lbl", 3, "the stderr tail")
        assert err.returncode == 3
        assert err.stderr == "the stderr tail"
        assert err.step_id == "s1"
        assert "the stderr tail" in str(err)
