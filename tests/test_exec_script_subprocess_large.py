"""Large tests: real subprocess execution of exec_script (Issue #204 MF-4).

実 Python subprocess を起動して execute_script の E2E 挙動を検証する。
testing-size-guide.md:24-30,68-76 に従い `large` + `large_local` マーカーを付与
（外部ネットワーク疎通なし、純粋にローカル subprocess のみ）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kaji_harness.errors import ScriptExecutionError, VerdictNotFound
from kaji_harness.models import Step
from kaji_harness.script_exec import execute_script
from kaji_harness.verdict import parse_verdict

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "scripts"


def _step() -> Step:
    return Step(id="exec", skill="dummy", agent=None, on={"PASS": "end", "ABORT": "end"})


def _module_for(file_stem: str) -> str:
    return f"tests.fixtures.scripts.{file_stem}"


@pytest.mark.large
@pytest.mark.large_local
class TestRealSubprocess:
    def test_pass_verdict_return_zero(self, tmp_path: Path) -> None:
        result = execute_script(
            step=_step(),
            module=_module_for("dummy_pass"),
            env={"KAJI_ISSUE_ID": "204"},
            workdir=Path.cwd(),
            log_dir=tmp_path / "log",
            timeout=30,
            verbose=False,
        )
        verdict = parse_verdict(
            result.full_output,
            valid_statuses={"PASS", "ABORT"},
            ai_formatter=None,
        )
        assert verdict.status == "PASS"
        assert "KAJI_ISSUE_ID=204" in verdict.evidence

    def test_abort_verdict_return_zero_recorded_as_abort(self, tmp_path: Path) -> None:
        result = execute_script(
            step=_step(),
            module=_module_for("dummy_abort_zero"),
            env={},
            workdir=Path.cwd(),
            log_dir=tmp_path / "log",
            timeout=30,
            verbose=False,
        )
        verdict = parse_verdict(
            result.full_output,
            valid_statuses={"PASS", "ABORT"},
            ai_formatter=None,
        )
        assert verdict.status == "ABORT"

    def test_nonzero_exit_raises_even_with_verdict(self, tmp_path: Path) -> None:
        with pytest.raises(ScriptExecutionError):
            execute_script(
                step=_step(),
                module=_module_for("dummy_nonzero"),
                env={},
                workdir=Path.cwd(),
                log_dir=tmp_path / "log",
                timeout=30,
                verbose=False,
            )

    def test_no_verdict_returns_clean_then_verdict_not_found(self, tmp_path: Path) -> None:
        result = execute_script(
            step=_step(),
            module=_module_for("dummy_no_verdict"),
            env={},
            workdir=Path.cwd(),
            log_dir=tmp_path / "log",
            timeout=30,
            verbose=False,
        )
        with pytest.raises(VerdictNotFound):
            parse_verdict(
                result.full_output,
                valid_statuses={"PASS", "ABORT"},
                ai_formatter=None,
            )

    def test_large_stderr_does_not_block_stdout_verdict(self, tmp_path: Path) -> None:
        """Regression: stderr pipe を並行に drain しないと verdict 前で child が
        block して StepTimeoutError になる (Issue #204 MF-1)。
        """
        log_dir = tmp_path / "log"
        result = execute_script(
            step=_step(),
            module=_module_for("dummy_stderr_volume"),
            env={},
            workdir=Path.cwd(),
            log_dir=log_dir,
            timeout=30,
            verbose=False,
        )
        verdict = parse_verdict(
            result.full_output,
            valid_statuses={"PASS", "ABORT"},
            ai_formatter=None,
        )
        assert verdict.status == "PASS"
        # stderr が確実に drain され、ログとして残されている
        stderr_log = log_dir / "stderr.log"
        assert stderr_log.exists()
        assert stderr_log.stat().st_size >= 1024 * 1024

    def test_env_propagates_to_subprocess(self, tmp_path: Path) -> None:
        result = execute_script(
            step=_step(),
            module=_module_for("dummy_pass"),
            env={"KAJI_ISSUE_ID": "999-from-env"},
            workdir=Path.cwd(),
            log_dir=tmp_path / "log",
            timeout=30,
            verbose=False,
        )
        assert "999-from-env" in result.full_output
