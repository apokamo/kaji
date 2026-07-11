"""Tests for the tmux interactive terminal runner (Issue #230).

Covers the tmux split-window argv builder (``-h`` right split), fail-fast
validation (``tmux`` / ``$TMUX`` / ``$TMUX_PANE`` / ``tmux -V``), ``#{pane_dead}``
liveness mapping, verdict polling with ``kill-pane`` / ``remain-on-exit``
cleanup, pane metadata snapshots, Codex session-id resolution, the wrapper
shell contract, and a real-tmux end-to-end pass.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from kaji_harness.cli import _TRANSIENT_PATTERNS, is_transient_error_text
from kaji_harness.errors import CLIExecutionError, CLINotFoundError, StepTimeoutError
from kaji_harness.interactive_terminal import (
    KajiAgentPane,
    _build_tmux_split_argv,
    _list_kaji_agent_panes,
    _pane_dead,
    _parse_kaji_pane_marker,
    _prune_kaji_agent_panes,
    _terminal_exit_detail,
    execute_interactive_terminal,
    extract_terminal_diagnostic,
    read_terminal_diagnostic,
)
from kaji_harness.models import Step
from kaji_harness.recovery.handler import _sensitive_failure_text
from kaji_harness.verdict import load_verdict_yaml

WRAPPER = (
    Path(__file__).resolve().parent.parent
    / "kaji_harness"
    / "assets"
    / "interactive-terminal"
    / "wrapper.sh"
)

_PASS_VERDICT = "status: PASS\nreason: ok\nevidence: e\nsuggestion: ''\n"
# display-message format response for `_write_pane_metadata` (tab-joined).
_PANE_METADATA = (
    "pane_id=%99\tpane_pid=123\tpane_current_command=bash\t"
    "pane_dead=0\tpane_dead_status=\tpane_dead_signal=\n"
)


def _step(
    agent: str, *, step_id: str = "design", model: str | None = None, effort: str | None = None
) -> Step:
    return Step(id=step_id, skill="issue-design", agent=agent, model=model, effort=effort)


def _completed(
    stdout: str = "", stderr: str = "", returncode: int = 0
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _make_fake_tmux(
    *,
    tmux: str = "/usr/bin/tmux",
    version: str = "tmux 3.4\n",
    pane_id: str = "%99",
    pane_dead: str = "0",
    pane_dead_status: str = "",
    pipe_returncode: int = 0,
    list_panes_output: str = "",
    list_panes_returncode: int = 0,
    set_option_returncode: int = 0,
    on_split: object = None,
    calls: list[list[str]] | None = None,
):
    """Build a ``subprocess.run`` replacement that fakes a tmux server.

    ``on_split`` (a no-arg callable) fires when ``split-window`` is seen so the
    test can write ``verdict.yaml`` / ``terminal.log`` at pane-launch time.
    ``list_panes_output`` is the stdout for the kaji-pane discovery call (empty =
    no existing managed panes); ``set_option_returncode`` lets a test simulate a
    failed kaji marker write. ``pane_dead`` and ``pane_dead_status`` are set
    independently (Issue #296) so a test can distinguish a clean pane exit
    (status 0) from a crashed one (status non-zero) in ``pane-metadata.json``.
    """

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if calls is not None:
            calls.append(argv)
        if argv == [tmux, "-V"]:
            return _completed(stdout=version)
        if argv[:2] == [tmux, "list-panes"]:
            return _completed(stdout=list_panes_output, returncode=list_panes_returncode)
        if argv[:2] == [tmux, "split-window"]:
            if on_split is not None:
                on_split()  # type: ignore[operator]
            return _completed(stdout=f"{pane_id}\n")
        if argv[:2] == [tmux, "pipe-pane"]:
            return _completed(returncode=pipe_returncode, stderr="can't find pane")
        if argv[:2] == [tmux, "set-option"]:
            return _completed(returncode=set_option_returncode, stderr="set-option failed")
        if argv[:2] == [tmux, "kill-pane"]:
            return _completed()
        if argv[:2] == [tmux, "display-message"]:
            if argv[-1] == "#{pane_dead}":
                return _completed(stdout=f"{pane_dead}\n")
            metadata = _PANE_METADATA.replace("pane_dead=0", f"pane_dead={pane_dead}")
            metadata = metadata.replace("pane_dead_status=", f"pane_dead_status={pane_dead_status}")
            return _completed(stdout=metadata)
        raise AssertionError(f"unexpected tmux call: {argv}")

    return fake_run


@pytest.mark.small
class TestBuildTmuxSplitArgv:
    """tmux ``split-window`` command construction (Issue #238: -h / -v split)."""

    def _argv(self, tmp_path: Path, *, split_target_pane: str, split_flag: str) -> list[str]:
        prompt = tmp_path / "prompt.txt"
        verdict = tmp_path / "verdict.yaml"
        return _build_tmux_split_argv(
            "/usr/bin/tmux",
            WRAPPER,
            split_target_pane=split_target_pane,
            split_flag=split_flag,
            agent="claude",
            prompt_path=prompt,
            verdict_path=verdict,
            workdir=tmp_path,
            resume_session_id="",
            launch_session_id="11111111-1111-4111-8111-111111111111",
            model="haiku",
            effort="low",
        )

    def test_horizontal_split_prefix_and_pane_id_format(self, tmp_path: Path) -> None:
        argv = self._argv(tmp_path, split_target_pane="%7", split_flag="-h")

        # Exact prefix: `-h` after `-d` puts the first agent pane to the right.
        assert argv[:9] == [
            "/usr/bin/tmux",
            "split-window",
            "-d",
            "-h",
            "-P",
            "-F",
            "#{pane_id}",
            "-t",
            "%7",
        ]
        # The single trailing argument is the shlex-quoted wrapper command.
        assert len(argv) == 10
        command = argv[9]
        assert str(WRAPPER) in command
        assert "claude" in command
        assert str(tmp_path / "prompt.txt") in command
        assert str(tmp_path / "verdict.yaml") in command
        assert "11111111-1111-4111-8111-111111111111" in command
        assert "haiku" in command
        assert "low" in command

    def test_vertical_split_targets_existing_agent_pane(self, tmp_path: Path) -> None:
        argv = self._argv(tmp_path, split_target_pane="%12", split_flag="-v")
        # `-v` splits the right column's bottom pane; target is the agent pane.
        assert argv[:9] == [
            "/usr/bin/tmux",
            "split-window",
            "-d",
            "-v",
            "-P",
            "-F",
            "#{pane_id}",
            "-t",
            "%12",
        ]

    def test_invalid_split_flag_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="split_flag"):
            self._argv(tmp_path, split_target_pane="%7", split_flag="-x")


@pytest.mark.small
class TestRunnerEntryValidation:
    """Fail-fast validation before any tmux pane is launched."""

    def test_missing_tmux_fails_loud(self, tmp_path: Path) -> None:
        prompt = tmp_path / "prompt.txt"
        prompt.write_text("prompt", encoding="utf-8")
        with patch("kaji_harness.interactive_terminal.shutil.which", return_value=None):
            with pytest.raises(CLINotFoundError, match="tmux"):
                execute_interactive_terminal(
                    step=_step("claude"),
                    prompt_path=prompt,
                    verdict_path=tmp_path / "verdict.yaml",
                    workdir=tmp_path,
                    timeout=5,
                )

    def test_requires_running_inside_tmux(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prompt = tmp_path / "prompt.txt"
        prompt.write_text("prompt", encoding="utf-8")
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.delenv("TMUX_PANE", raising=False)
        with patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/tmux"):
            with pytest.raises(CLINotFoundError, match="inside tmux"):
                execute_interactive_terminal(
                    step=_step("claude"),
                    prompt_path=prompt,
                    verdict_path=tmp_path / "verdict.yaml",
                    workdir=tmp_path,
                    timeout=5,
                )

    def test_requires_tmux_pane(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        prompt = tmp_path / "prompt.txt"
        prompt.write_text("prompt", encoding="utf-8")
        monkeypatch.setenv("TMUX", "/tmp/tmux-sock,1,0")
        monkeypatch.delenv("TMUX_PANE", raising=False)
        with patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/tmux"):
            with pytest.raises(CLINotFoundError, match="TMUX_PANE"):
                execute_interactive_terminal(
                    step=_step("claude"),
                    prompt_path=prompt,
                    verdict_path=tmp_path / "verdict.yaml",
                    workdir=tmp_path,
                    timeout=5,
                )

    @pytest.mark.parametrize("version", ["tmux 2.9\n", "tmux 3.0\n"])
    def test_tmux_version_below_minimum_fails_loud(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str
    ) -> None:
        # Issue #238: pane options require tmux 3.1, so 3.0 now also fails fast.
        prompt = tmp_path / "prompt.txt"
        prompt.write_text("prompt", encoding="utf-8")
        monkeypatch.setenv("TMUX", "/tmp/tmux-sock,1,0")
        monkeypatch.setenv("TMUX_PANE", "%7")

        def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            assert argv == ["/usr/bin/tmux", "-V"]
            return _completed(stdout=version)

        with (
            patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/tmux"),
            patch.object(subprocess, "run", side_effect=fake_run),
        ):
            with pytest.raises(CLINotFoundError, match="tmux >= 3.1"):
                execute_interactive_terminal(
                    step=_step("claude"),
                    prompt_path=prompt,
                    verdict_path=tmp_path / "verdict.yaml",
                    workdir=tmp_path,
                    timeout=5,
                )

    def test_tmux_3_1_passes_version_check(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # tmux 3.1 is the minimum and must clear the version gate.
        prompt = tmp_path / "prompt.txt"
        verdict = tmp_path / "verdict.yaml"
        prompt.write_text("prompt", encoding="utf-8")
        monkeypatch.setenv("TMUX", "/tmp/tmux-sock,1,0")
        monkeypatch.setenv("TMUX_PANE", "%7")
        fake_run = _make_fake_tmux(
            version="tmux 3.1\n",
            on_split=lambda: verdict.write_text(_PASS_VERDICT, encoding="utf-8"),
        )
        with (
            patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/tmux"),
            patch.object(subprocess, "run", side_effect=fake_run),
        ):
            result = execute_interactive_terminal(
                step=_step("claude"),
                prompt_path=prompt,
                verdict_path=verdict,
                workdir=tmp_path,
                timeout=5,
            )
        assert result.full_output == ""

    def test_rejects_unsupported_agent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prompt = tmp_path / "prompt.txt"
        prompt.write_text("prompt", encoding="utf-8")
        monkeypatch.setenv("TMUX", "/tmp/tmux-sock,1,0")
        monkeypatch.setenv("TMUX_PANE", "%7")
        with patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/tmux"):
            with pytest.raises(ValueError, match="does not support agent"):
                execute_interactive_terminal(
                    step=Step(id="s", skill="x", agent="gemini"),
                    prompt_path=prompt,
                    verdict_path=tmp_path / "verdict.yaml",
                    workdir=tmp_path,
                    timeout=5,
                )

    def test_missing_prompt_fails_loud(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TMUX", "/tmp/tmux-sock,1,0")
        monkeypatch.setenv("TMUX_PANE", "%7")
        with patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/tmux"):
            with pytest.raises(FileNotFoundError, match="prompt.txt"):
                execute_interactive_terminal(
                    step=_step("claude"),
                    prompt_path=tmp_path / "prompt.txt",
                    verdict_path=tmp_path / "verdict.yaml",
                    workdir=tmp_path,
                    timeout=5,
                )


@pytest.mark.small
class TestPaneDeadMapping:
    """`_pane_dead` maps tmux ``#{pane_dead}`` (and lookup failure) to a bool."""

    def test_pane_dead_true_when_value_is_one(self) -> None:
        with patch.object(subprocess, "run", return_value=_completed(stdout="1\n")):
            assert _pane_dead("/usr/bin/tmux", "%99") is True

    def test_pane_dead_false_when_value_is_zero(self) -> None:
        with patch.object(subprocess, "run", return_value=_completed(stdout="0\n")):
            assert _pane_dead("/usr/bin/tmux", "%99") is False

    def test_pane_lookup_failure_is_treated_as_dead(self) -> None:
        with patch.object(
            subprocess, "run", return_value=_completed(stderr="can't find pane", returncode=1)
        ):
            assert _pane_dead("/usr/bin/tmux", "%99") is True


@pytest.mark.medium
class TestSessionIdLaunch:
    """Resume / launch session-id rules threaded into the wrapper command."""

    def _run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, step: Step, **kwargs: object
    ) -> tuple[object, list[list[str]]]:
        prompt = tmp_path / "prompt.txt"
        verdict = tmp_path / "verdict.yaml"
        prompt.write_text("prompt", encoding="utf-8")
        monkeypatch.setenv("TMUX", "/tmp/tmux-sock,1,0")
        monkeypatch.setenv("TMUX_PANE", "%7")
        calls: list[list[str]] = []
        fake_run = _make_fake_tmux(
            calls=calls, on_split=lambda: verdict.write_text(_PASS_VERDICT, encoding="utf-8")
        )
        with (
            patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/tmux"),
            patch.object(subprocess, "run", side_effect=fake_run),
        ):
            result = execute_interactive_terminal(
                step=step,
                prompt_path=prompt,
                verdict_path=verdict,
                workdir=tmp_path,
                timeout=5,
                **kwargs,  # type: ignore[arg-type]
            )
        return result, calls

    def _split_command(self, calls: list[list[str]]) -> str:
        for call in calls:
            if call[:2] == ["/usr/bin/tmux", "split-window"]:
                return call[-1]
        raise AssertionError("split-window was never called")

    def test_claude_fresh_generates_launch_session_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with patch(
            "kaji_harness.interactive_terminal.uuid.uuid4",
            return_value=uuid.UUID("11111111-1111-4111-8111-111111111111"),
        ):
            result, calls = self._run(tmp_path, monkeypatch, _step("claude", model="haiku"))
        assert result.full_output == ""
        assert result.session_id == "11111111-1111-4111-8111-111111111111"
        assert "11111111-1111-4111-8111-111111111111" in self._split_command(calls)

    def test_resume_passes_session_id_without_launch_uuid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result, calls = self._run(
            tmp_path, monkeypatch, _step("claude", step_id="fix"), session_id="resume-session"
        )
        assert result.session_id == "resume-session"
        assert "resume-session" in self._split_command(calls)

    def test_codex_fresh_does_not_generate_launch_uuid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result, calls = self._run(tmp_path, monkeypatch, _step("codex"))
        # Codex mints its own id; the runner resolves it post-verdict (here the
        # fake never prints one, so the resolved id is None).
        assert result.session_id is None


@pytest.mark.medium
class TestRunnerPaneLifecycle:
    """Pane launch, transcript pipe, verdict polling, and cleanup."""

    def test_verdict_kills_pane_writes_metadata_and_returns_result(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prompt = tmp_path / "prompt.txt"
        verdict = tmp_path / "verdict.yaml"
        prompt.write_text("prompt", encoding="utf-8")
        monkeypatch.setenv("TMUX", "/tmp/tmux-sock,1,0")
        monkeypatch.setenv("TMUX_PANE", "%7")
        calls: list[list[str]] = []
        fake_run = _make_fake_tmux(
            calls=calls, on_split=lambda: verdict.write_text(_PASS_VERDICT, encoding="utf-8")
        )

        with (
            patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/tmux"),
            patch(
                "kaji_harness.interactive_terminal.uuid.uuid4",
                return_value=uuid.UUID("11111111-1111-4111-8111-111111111111"),
            ),
            patch.object(subprocess, "run", side_effect=fake_run),
        ):
            result = execute_interactive_terminal(
                step=_step("claude", model="haiku", effort="low"),
                prompt_path=prompt,
                verdict_path=verdict,
                workdir=tmp_path,
                timeout=5,
            )

        assert result.full_output == ""
        assert result.session_id == "11111111-1111-4111-8111-111111111111"
        assert any(call[:2] == ["/usr/bin/tmux", "pipe-pane"] for call in calls)
        assert ["/usr/bin/tmux", "kill-pane", "-t", "%99"] in calls
        metadata = json.loads((tmp_path / "pane-metadata.json").read_text(encoding="utf-8"))
        assert metadata["pane_id"] == "%99"
        # Verdict-trigger contract: the agent CLI is still alive at verdict time.
        assert metadata["pane_dead"] == "0"

    def test_pane_launched_progress_includes_step_agent_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Issue #232: 親コンソール向け `pane launched` INFO progress に
        # step / agent / pane / timeout / verdict path が全て載ることを固定する。
        prompt = tmp_path / "prompt.txt"
        verdict = tmp_path / "verdict.yaml"
        prompt.write_text("prompt", encoding="utf-8")
        monkeypatch.setenv("TMUX", "/tmp/tmux-sock,1,0")
        monkeypatch.setenv("TMUX_PANE", "%7")
        fake_run = _make_fake_tmux(
            on_split=lambda: verdict.write_text(_PASS_VERDICT, encoding="utf-8")
        )

        with (
            patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/tmux"),
            patch.object(subprocess, "run", side_effect=fake_run),
            caplog.at_level("INFO", logger="kaji.interactive_terminal"),
        ):
            execute_interactive_terminal(
                step=_step("claude", step_id="design"),
                prompt_path=prompt,
                verdict_path=verdict,
                workdir=tmp_path,
                timeout=1800,
            )

        launched = [
            rec.getMessage()
            for rec in caplog.records
            if rec.getMessage().startswith("pane launched:")
        ]
        assert len(launched) == 1
        message = launched[0]
        assert message == (
            f"pane launched: step=design agent=claude pane=%99 timeout=1800s verdict={verdict}"
        )

    def test_close_on_verdict_false_sets_remain_on_exit_and_skips_kill(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prompt = tmp_path / "prompt.txt"
        verdict = tmp_path / "verdict.yaml"
        prompt.write_text("prompt", encoding="utf-8")
        monkeypatch.setenv("TMUX", "/tmp/tmux-sock,1,0")
        monkeypatch.setenv("TMUX_PANE", "%7")
        calls: list[list[str]] = []
        fake_run = _make_fake_tmux(
            calls=calls, on_split=lambda: verdict.write_text(_PASS_VERDICT, encoding="utf-8")
        )

        with (
            patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/tmux"),
            patch.object(subprocess, "run", side_effect=fake_run),
        ):
            execute_interactive_terminal(
                step=_step("claude"),
                prompt_path=prompt,
                verdict_path=verdict,
                workdir=tmp_path,
                timeout=5,
                close_on_verdict=False,
            )

        assert ["/usr/bin/tmux", "set-option", "-p", "-t", "%99", "remain-on-exit", "on"] in calls
        assert not any(call[:2] == ["/usr/bin/tmux", "kill-pane"] for call in calls)
        # metadata records the actual #{pane_dead} value at verdict detection
        # (0 under the verdict-trigger contract), not the eventual [dead] state.
        metadata = json.loads((tmp_path / "pane-metadata.json").read_text(encoding="utf-8"))
        assert metadata["pane_dead"] == "0"
        assert metadata["close_on_verdict"] is False

    def test_pipe_pane_records_transcript(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prompt = tmp_path / "prompt.txt"
        verdict = tmp_path / "verdict.yaml"
        prompt.write_text("prompt", encoding="utf-8")
        monkeypatch.setenv("TMUX", "/tmp/tmux-sock,1,0")
        monkeypatch.setenv("TMUX_PANE", "%7")
        calls: list[list[str]] = []
        fake_run = _make_fake_tmux(
            calls=calls, on_split=lambda: verdict.write_text(_PASS_VERDICT, encoding="utf-8")
        )

        with (
            patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/tmux"),
            patch.object(subprocess, "run", side_effect=fake_run),
        ):
            execute_interactive_terminal(
                step=_step("claude"),
                prompt_path=prompt,
                verdict_path=verdict,
                workdir=tmp_path,
                timeout=5,
            )

        pipe_calls = [call for call in calls if call[:2] == ["/usr/bin/tmux", "pipe-pane"]]
        assert len(pipe_calls) == 1
        assert pipe_calls[0][:5] == ["/usr/bin/tmux", "pipe-pane", "-o", "-t", "%99"]
        assert str(tmp_path / "terminal.log") in pipe_calls[0][-1]

    def test_pipe_pane_failure_with_verdict_succeeds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A short-running agent can write verdict.yaml and exit before the pipe
        # attaches; tmux then rejects pipe-pane. A present verdict must still be
        # reported as success rather than masked by the transcript setup failure.
        prompt = tmp_path / "prompt.txt"
        verdict = tmp_path / "verdict.yaml"
        prompt.write_text("prompt", encoding="utf-8")
        monkeypatch.setenv("TMUX", "/tmp/tmux-sock,1,0")
        monkeypatch.setenv("TMUX_PANE", "%7")
        calls: list[list[str]] = []
        fake_run = _make_fake_tmux(
            calls=calls,
            pipe_returncode=1,
            on_split=lambda: verdict.write_text(_PASS_VERDICT, encoding="utf-8"),
        )

        with (
            patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/tmux"),
            patch(
                "kaji_harness.interactive_terminal.uuid.uuid4",
                return_value=uuid.UUID("11111111-1111-4111-8111-111111111111"),
            ),
            patch.object(subprocess, "run", side_effect=fake_run),
        ):
            result = execute_interactive_terminal(
                step=_step("claude"),
                prompt_path=prompt,
                verdict_path=verdict,
                workdir=tmp_path,
                timeout=5,
            )

        assert result.session_id == "11111111-1111-4111-8111-111111111111"

    def test_pipe_pane_failure_without_verdict_fails_loud(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # pipe-pane rejected and no verdict present → genuine launch failure.
        prompt = tmp_path / "prompt.txt"
        prompt.write_text("prompt", encoding="utf-8")
        (tmp_path / "terminal.log").write_text("agent failed at launch\n", encoding="utf-8")
        monkeypatch.setenv("TMUX", "/tmp/tmux-sock,1,0")
        monkeypatch.setenv("TMUX_PANE", "%7")
        calls: list[list[str]] = []
        fake_run = _make_fake_tmux(calls=calls, pipe_returncode=1)

        with (
            patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/tmux"),
            patch.object(subprocess, "run", side_effect=fake_run),
        ):
            with pytest.raises(CLIExecutionError) as excinfo:
                execute_interactive_terminal(
                    step=_step("claude"),
                    prompt_path=prompt,
                    verdict_path=tmp_path / "verdict.yaml",
                    workdir=tmp_path,
                    timeout=5,
                )

        assert "agent failed at launch" in excinfo.value.stderr
        # The orphaned pane is cleaned up before failing loud.
        assert ["/usr/bin/tmux", "kill-pane", "-t", "%99"] in calls

    def test_early_pane_exit_fails_loud(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prompt = tmp_path / "prompt.txt"
        prompt.write_text("prompt", encoding="utf-8")
        (tmp_path / "terminal.log").write_text("agent failed at launch\n", encoding="utf-8")
        monkeypatch.setenv("TMUX", "/tmp/tmux-sock,1,0")
        monkeypatch.setenv("TMUX_PANE", "%7")
        fake_run = _make_fake_tmux(pane_dead="1")  # pane dead, no verdict ever

        with (
            patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/tmux"),
            patch.object(subprocess, "run", side_effect=fake_run),
        ):
            with pytest.raises(CLIExecutionError) as excinfo:
                execute_interactive_terminal(
                    step=_step("claude"),
                    prompt_path=prompt,
                    verdict_path=tmp_path / "verdict.yaml",
                    workdir=tmp_path,
                    timeout=600,
                )
        assert "agent failed at launch" in excinfo.value.stderr

    def test_timeout_raises_and_kills_pane(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prompt = tmp_path / "prompt.txt"
        prompt.write_text("prompt", encoding="utf-8")
        monkeypatch.setenv("TMUX", "/tmp/tmux-sock,1,0")
        monkeypatch.setenv("TMUX_PANE", "%7")
        calls: list[list[str]] = []
        fake_run = _make_fake_tmux(calls=calls)  # never writes verdict; pane alive

        with (
            patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/tmux"),
            patch.object(subprocess, "run", side_effect=fake_run),
            patch("kaji_harness.interactive_terminal.time.sleep", return_value=None),
            patch(
                "kaji_harness.interactive_terminal.time.monotonic",
                side_effect=[0.0, 0.5, 2.0],
            ),
        ):
            with pytest.raises(StepTimeoutError):
                execute_interactive_terminal(
                    step=_step("claude"),
                    prompt_path=prompt,
                    verdict_path=tmp_path / "verdict.yaml",
                    workdir=tmp_path,
                    timeout=1,
                )
        assert ["/usr/bin/tmux", "kill-pane", "-t", "%99"] in calls


@pytest.mark.medium
class TestCodexSessionIdExtraction:
    """Codex session id from terminal.log, with session-store fallback."""

    def _run(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        on_split: object,
    ) -> str | None:
        prompt = tmp_path / "prompt.txt"
        verdict = tmp_path / "verdict.yaml"
        prompt.write_text("prompt", encoding="utf-8")
        monkeypatch.setenv("TMUX", "/tmp/tmux-sock,1,0")
        monkeypatch.setenv("TMUX_PANE", "%7")
        # pane reported dead so the codex session-id grace loop returns at once.
        fake_run = _make_fake_tmux(pane_dead="1", on_split=on_split)
        with (
            patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/tmux"),
            patch.object(subprocess, "run", side_effect=fake_run),
        ):
            result = execute_interactive_terminal(
                step=_step("codex", step_id="review"),
                prompt_path=prompt,
                verdict_path=verdict,
                workdir=tmp_path,
                timeout=5,
            )
        return result.session_id

    def test_extracts_from_terminal_log(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        verdict = tmp_path / "verdict.yaml"
        terminal_log = tmp_path / "terminal.log"

        def on_split() -> None:
            verdict.write_text(_PASS_VERDICT, encoding="utf-8")
            terminal_log.write_text(
                "To continue, run codex resume 22222222-2222-4222-8222-222222222222\n",
                encoding="utf-8",
            )

        assert (
            self._run(tmp_path, monkeypatch, on_split=on_split)
            == "22222222-2222-4222-8222-222222222222"
        )

    def test_session_store_fallback_when_marker_matches(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prompt = tmp_path / "prompt.txt"
        verdict = tmp_path / "verdict.yaml"
        codex_home = tmp_path / "codex-home"
        sessions_dir = codex_home / "sessions" / "2026" / "06" / "05"
        sessions_dir.mkdir(parents=True)
        session_file = (
            sessions_dir / "rollout-2026-06-05T01-11-46-44444444-4444-4444-8444-444444444444.jsonl"
        )
        session_file.write_text(
            f'{{"type":"user","text":"read {prompt} and write {verdict}"}}\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("CODEX_HOME", str(codex_home))

        assert (
            self._run(
                tmp_path,
                monkeypatch,
                on_split=lambda: verdict.write_text(_PASS_VERDICT, encoding="utf-8"),
            )
            == "44444444-4444-4444-8444-444444444444"
        )

    def test_session_store_fallback_ignores_unrelated_rollout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        verdict = tmp_path / "verdict.yaml"
        codex_home = tmp_path / "codex-home"
        sessions_dir = codex_home / "sessions" / "2026" / "06" / "05"
        sessions_dir.mkdir(parents=True)
        other = (
            sessions_dir / "rollout-2026-06-05T00-00-00-99999999-9999-4999-8999-999999999999.jsonl"
        )
        other.write_text('{"type":"user","text":"unrelated session"}\n', encoding="utf-8")
        monkeypatch.setenv("CODEX_HOME", str(codex_home))

        assert (
            self._run(
                tmp_path,
                monkeypatch,
                on_split=lambda: verdict.write_text(_PASS_VERDICT, encoding="utf-8"),
            )
            is None
        )


@pytest.mark.medium
class TestInteractiveTerminalWrapper:
    """Wrapper shell contract: cwd, arg order, and agent command lines (8 args)."""

    def test_wrapper_syntax_is_valid(self) -> None:
        result = subprocess.run(
            ["bash", "-n", str(WRAPPER)], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, result.stderr

    def _fake_agent_recording_argv(self, tmp_path: Path, name: str) -> tuple[Path, Path]:
        """Create a fake agent on PATH that records its argv and cwd."""
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir(exist_ok=True)
        args_path = tmp_path / f"{name}-args.txt"
        cwd_path = tmp_path / f"{name}-cwd.txt"
        agent = fake_bin / name
        agent.write_text(
            "#!/usr/bin/env bash\n"
            'pwd > "$CWD_PATH"\n'
            'printf "%s\\n" "$@" > "$ARGS_PATH"\n'
            'printf "status: PASS\\nreason: ok\\nevidence: e\\nsuggestion: \x27\x27\\n" > "$FAKE_VERDICT_PATH"\n',
            encoding="utf-8",
        )
        agent.chmod(0o755)
        return args_path, cwd_path

    def _run_wrapper(
        self,
        tmp_path: Path,
        agent: str,
        wrapper_args: list[str],
        *,
        path_prefix: Path,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        verdict = tmp_path / "verdict.yaml"
        env = dict(os.environ)
        env["PATH"] = f"{path_prefix}:{os.environ['PATH']}"
        env["FAKE_VERDICT_PATH"] = str(verdict)
        env["ARGS_PATH"] = str(tmp_path / f"{agent}-args.txt")
        env["CWD_PATH"] = str(tmp_path / f"{agent}-cwd.txt")
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [str(WRAPPER), *wrapper_args],
            cwd=tmp_path,
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )

    def _base_args(self, tmp_path: Path, agent: str) -> list[str]:
        prompt = tmp_path / "prompt.txt"
        prompt.write_text("prompt", encoding="utf-8")
        workdir = tmp_path / "work"
        workdir.mkdir(exist_ok=True)
        # 8-arg contract: agent prompt verdict workdir ...
        return [agent, str(prompt), str(tmp_path / "verdict.yaml"), str(workdir)]

    def test_wrapper_cds_into_workdir_before_agent(self, tmp_path: Path) -> None:
        workdir = tmp_path / "work"
        workdir.mkdir()
        _, cwd_path = self._fake_agent_recording_argv(tmp_path, "claude")
        fake_bin = tmp_path / "bin"
        (tmp_path / "prompt.txt").write_text("prompt", encoding="utf-8")
        result = self._run_wrapper(
            tmp_path,
            "claude",
            ["claude", str(tmp_path / "prompt.txt"), str(tmp_path / "verdict.yaml"), str(workdir)],
            path_prefix=fake_bin,
        )
        assert result.returncode == 0, result.stderr
        recorded_cwd = Path(cwd_path.read_text(encoding="utf-8").strip())
        assert recorded_cwd.resolve() == workdir.resolve()

    def test_wrapper_enables_truecolor_for_agent(self, tmp_path: Path) -> None:
        env_path = tmp_path / "claude-env.txt"
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir(exist_ok=True)
        fake_claude = fake_bin / "claude"
        fake_claude.write_text(
            "#!/usr/bin/env bash\n"
            'printf "NO_COLOR=%s\\n" "${NO_COLOR-<unset>}" > "$ENV_PATH"\n'
            'printf "COLORTERM=%s\\n" "${COLORTERM-<unset>}" >> "$ENV_PATH"\n'
            'printf "status: PASS\\nreason: ok\\nevidence: e\\nsuggestion: \x27\x27\\n" > "$FAKE_VERDICT_PATH"\n',
            encoding="utf-8",
        )
        fake_claude.chmod(0o755)
        result = self._run_wrapper(
            tmp_path,
            "claude",
            self._base_args(tmp_path, "claude"),
            path_prefix=fake_bin,
            extra_env={"NO_COLOR": "1", "COLORTERM": "falsecolor", "ENV_PATH": str(env_path)},
        )
        assert result.returncode == 0, result.stderr
        assert env_path.read_text(encoding="utf-8").splitlines() == [
            "NO_COLOR=<unset>",
            "COLORTERM=truecolor",
        ]

    def test_claude_fresh_command_matches_contract(self, tmp_path: Path) -> None:
        args_path, _ = self._fake_agent_recording_argv(tmp_path, "claude")
        fake_bin = tmp_path / "bin"
        wrapper_args = self._base_args(tmp_path, "claude") + ["", "launch-uuid", "haiku", "low"]
        result = self._run_wrapper(tmp_path, "claude", wrapper_args, path_prefix=fake_bin)
        assert result.returncode == 0, result.stderr
        args = args_path.read_text(encoding="utf-8").splitlines()
        assert args[:6] == [
            "--dangerously-skip-permissions",
            "--model",
            "haiku",
            "--effort",
            "low",
            "--session-id",
        ]
        assert args[6] == "launch-uuid"

    def test_claude_resume_command_matches_contract(self, tmp_path: Path) -> None:
        args_path, _ = self._fake_agent_recording_argv(tmp_path, "claude")
        fake_bin = tmp_path / "bin"
        wrapper_args = self._base_args(tmp_path, "claude") + ["resume-uuid", "", "haiku", "low"]
        result = self._run_wrapper(tmp_path, "claude", wrapper_args, path_prefix=fake_bin)
        assert result.returncode == 0, result.stderr
        args = args_path.read_text(encoding="utf-8").splitlines()
        assert args[:6] == [
            "--dangerously-skip-permissions",
            "--model",
            "haiku",
            "--effort",
            "low",
            "--resume",
        ]
        assert args[6] == "resume-uuid"

    def test_codex_fresh_command_matches_contract(self, tmp_path: Path) -> None:
        args_path, _ = self._fake_agent_recording_argv(tmp_path, "codex")
        fake_bin = tmp_path / "bin"
        wrapper_args = self._base_args(tmp_path, "codex") + ["", "", "gpt-5.4-mini", "low"]
        result = self._run_wrapper(tmp_path, "codex", wrapper_args, path_prefix=fake_bin)
        assert result.returncode == 0, result.stderr
        args = args_path.read_text(encoding="utf-8").splitlines()
        assert args[:7] == [
            "--cd",
            str(tmp_path / "work"),
            "--dangerously-bypass-approvals-and-sandbox",
            "--model",
            "gpt-5.4-mini",
            "--config",
            'model_reasoning_effort="low"',
        ]

    def test_codex_resume_command_matches_contract(self, tmp_path: Path) -> None:
        args_path, _ = self._fake_agent_recording_argv(tmp_path, "codex")
        fake_bin = tmp_path / "bin"
        wrapper_args = self._base_args(tmp_path, "codex") + [
            "33333333-3333-4333-8333-333333333333",
            "",
            "gpt-5.4-mini",
            "low",
        ]
        result = self._run_wrapper(tmp_path, "codex", wrapper_args, path_prefix=fake_bin)
        assert result.returncode == 0, result.stderr
        args = args_path.read_text(encoding="utf-8").splitlines()
        assert args[:4] == [
            "resume",
            "--cd",
            str(tmp_path / "work"),
            "--dangerously-bypass-approvals-and-sandbox",
        ]
        assert args[4:9] == [
            "--model",
            "gpt-5.4-mini",
            "--config",
            'model_reasoning_effort="low"',
            "33333333-3333-4333-8333-333333333333",
        ]


@pytest.mark.small
class TestKajiPaneMarker:
    """`_parse_kaji_pane_marker` tolerates unknown / empty / malformed values."""

    def test_parses_origin_field(self) -> None:
        assert _parse_kaji_pane_marker("origin=%7") == {"origin": "%7"}

    def test_empty_value_is_empty_dict(self) -> None:
        assert _parse_kaji_pane_marker("") == {}

    def test_malformed_token_without_equals_is_dropped(self) -> None:
        assert _parse_kaji_pane_marker("garbage") == {}

    def test_unknown_fields_kept_origin_extracted(self) -> None:
        parsed = _parse_kaji_pane_marker("origin=%7 extra=foo")
        assert parsed["origin"] == "%7"
        assert parsed["extra"] == "foo"

    def test_empty_origin_value_kept_as_empty_string(self) -> None:
        assert _parse_kaji_pane_marker("origin=") == {"origin": ""}


def _list_panes_lines(*rows: tuple[str, int, int, int, str]) -> str:
    """Render fake ``list-panes -F`` output rows (pane_id, top, left, width, marker)."""
    return "".join(
        f"{pid}\t{top}\t{left}\t{width}\t{marker}\n" for pid, top, left, width, marker in rows
    )


@pytest.mark.medium
class TestListKajiAgentPanes:
    """`_list_kaji_agent_panes` filtering, ordering, and fail-loud behaviour."""

    def _list(self, output: str, *, returncode: int = 0) -> list[KajiAgentPane]:
        fake_run = _make_fake_tmux(list_panes_output=output, list_panes_returncode=returncode)
        with patch.object(subprocess, "run", side_effect=fake_run):
            return _list_kaji_agent_panes("/usr/bin/tmux", "%7")

    def test_returns_managed_panes_sorted_by_pane_top(self) -> None:
        output = _list_panes_lines(
            ("%7", 0, 0, 60, ""),  # origin pane, excluded
            ("%2", 18, 61, 60, "origin=%7"),  # newer (lower) pane
            ("%1", 0, 61, 60, "origin=%7"),  # older (upper) pane
        )
        panes = self._list(output)
        assert [pane.pane_id for pane in panes] == ["%1", "%2"]
        assert panes[0].pane_top == 0
        assert panes[1].pane_top == 18
        assert panes[1].pane_left == 61
        assert panes[1].pane_width == 60

    def test_ignores_origin_and_unmarked_and_foreign_origin(self) -> None:
        output = _list_panes_lines(
            ("%7", 0, 0, 60, ""),  # origin
            ("%3", 0, 61, 60, ""),  # unmarked manual pane
            ("%4", 5, 61, 60, "origin=%99"),  # marked for a different origin
            ("%5", 9, 61, 60, "origin=%7"),  # our managed pane
        )
        panes = self._list(output)
        assert [pane.pane_id for pane in panes] == ["%5"]

    def test_list_panes_failure_fails_loud(self) -> None:
        with pytest.raises(CLIExecutionError):
            self._list("", returncode=1)


@pytest.mark.small
class TestPruneKajiAgentPanes:
    """`_prune_kaji_agent_panes` keeps the newest N panes, killing the oldest."""

    def test_keeps_all_when_under_limit(self) -> None:
        panes = [KajiAgentPane("%1", 0, 61, 60)]
        calls: list[list[str]] = []
        fake_run = _make_fake_tmux(calls=calls)
        with patch.object(subprocess, "run", side_effect=fake_run):
            survivors = _prune_kaji_agent_panes("/usr/bin/tmux", panes, keep=1)
        assert [p.pane_id for p in survivors] == ["%1"]
        assert not any(call[:2] == ["/usr/bin/tmux", "kill-pane"] for call in calls)

    def test_kills_oldest_top_pane_when_over_limit(self) -> None:
        panes = [
            KajiAgentPane("%2", 18, 61, 60),
            KajiAgentPane("%1", 0, 61, 60),
        ]
        calls: list[list[str]] = []
        fake_run = _make_fake_tmux(calls=calls)
        with patch.object(subprocess, "run", side_effect=fake_run):
            survivors = _prune_kaji_agent_panes("/usr/bin/tmux", panes, keep=1)
        # %1 (pane_top 0, top of column) is the oldest and is killed.
        assert [p.pane_id for p in survivors] == ["%2"]
        assert ["/usr/bin/tmux", "kill-pane", "-t", "%1"] in calls
        assert ["/usr/bin/tmux", "kill-pane", "-t", "%2"] not in calls


@pytest.mark.medium
class TestRightColumnPanePlacement:
    """Issue #238: first pane splits right; later panes split the right column."""

    def _run(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        list_panes_output: str = "",
        set_option_returncode: int = 0,
        list_panes_returncode: int = 0,
        write_verdict: bool = True,
    ) -> list[list[str]]:
        prompt = tmp_path / "prompt.txt"
        verdict = tmp_path / "verdict.yaml"
        prompt.write_text("prompt", encoding="utf-8")
        monkeypatch.setenv("TMUX", "/tmp/tmux-sock,1,0")
        monkeypatch.setenv("TMUX_PANE", "%7")
        calls: list[list[str]] = []
        on_split = (
            (lambda: verdict.write_text(_PASS_VERDICT, encoding="utf-8")) if write_verdict else None
        )
        fake_run = _make_fake_tmux(
            calls=calls,
            list_panes_output=list_panes_output,
            list_panes_returncode=list_panes_returncode,
            set_option_returncode=set_option_returncode,
            on_split=on_split,
        )
        with (
            patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/tmux"),
            patch.object(subprocess, "run", side_effect=fake_run),
        ):
            execute_interactive_terminal(
                step=_step("claude"),
                prompt_path=prompt,
                verdict_path=verdict,
                workdir=tmp_path,
                timeout=5,
            )
        return calls

    def _split_call(self, calls: list[list[str]]) -> list[str]:
        for call in calls:
            if call[:2] == ["/usr/bin/tmux", "split-window"]:
                return call
        raise AssertionError("split-window was never called")

    def test_zero_panes_splits_origin_horizontally(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._run(
            tmp_path, monkeypatch, list_panes_output=_list_panes_lines(("%7", 0, 0, 60, ""))
        )
        split = self._split_call(calls)
        assert split[2:4] == ["-d", "-h"]
        assert split[7:9] == ["-t", "%7"]
        # The new pane is tagged with the kaji marker.
        assert [
            "/usr/bin/tmux",
            "set-option",
            "-p",
            "-t",
            "%99",
            "@kaji_interactive_terminal",
            "origin=%7",
        ] in calls

    def test_one_pane_splits_existing_agent_vertically(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        output = _list_panes_lines(
            ("%7", 0, 0, 60, ""),
            ("%1", 0, 61, 60, "origin=%7"),
        )
        calls = self._run(tmp_path, monkeypatch, list_panes_output=output)
        split = self._split_call(calls)
        assert split[2:4] == ["-d", "-v"]
        assert split[7:9] == ["-t", "%1"]
        # The existing managed pane is reused (split), never pruned. (The created
        # pane %99 is still killed by the close_on_verdict cleanup.)
        assert ["/usr/bin/tmux", "kill-pane", "-t", "%1"] not in calls

    def test_two_panes_kills_oldest_then_splits_newest_vertically(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        output = _list_panes_lines(
            ("%7", 0, 0, 60, ""),
            ("%1", 0, 61, 60, "origin=%7"),  # oldest (top)
            ("%2", 18, 61, 60, "origin=%7"),  # newest (bottom)
        )
        calls = self._run(tmp_path, monkeypatch, list_panes_output=output)
        # The oldest managed pane is pruned before the split.
        assert ["/usr/bin/tmux", "kill-pane", "-t", "%1"] in calls
        split = self._split_call(calls)
        assert split[2:4] == ["-d", "-v"]
        assert split[7:9] == ["-t", "%2"]

    def test_foreign_origin_pane_is_not_pruned(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A pane marked for a different origin must never be killed and must not
        # count toward the limit (so we split the origin horizontally).
        output = _list_panes_lines(
            ("%7", 0, 0, 60, ""),
            ("%8", 0, 61, 60, "origin=%99"),
        )
        calls = self._run(tmp_path, monkeypatch, list_panes_output=output)
        # The foreign-origin pane %8 is never pruned.
        assert ["/usr/bin/tmux", "kill-pane", "-t", "%8"] not in calls
        split = self._split_call(calls)
        assert split[2:4] == ["-d", "-h"]
        assert split[7:9] == ["-t", "%7"]

    def test_list_panes_failure_fails_loud(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with pytest.raises(CLIExecutionError):
            self._run(tmp_path, monkeypatch, list_panes_returncode=1, write_verdict=False)

    def test_marker_set_failure_kills_pane_and_fails_loud(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[list[str]] = []
        prompt = tmp_path / "prompt.txt"
        prompt.write_text("prompt", encoding="utf-8")
        monkeypatch.setenv("TMUX", "/tmp/tmux-sock,1,0")
        monkeypatch.setenv("TMUX_PANE", "%7")
        fake_run = _make_fake_tmux(
            calls=calls,
            list_panes_output=_list_panes_lines(("%7", 0, 0, 60, "")),
            set_option_returncode=1,
        )
        with (
            patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/tmux"),
            patch.object(subprocess, "run", side_effect=fake_run),
        ):
            with pytest.raises(CLIExecutionError):
                execute_interactive_terminal(
                    step=_step("claude"),
                    prompt_path=prompt,
                    verdict_path=tmp_path / "verdict.yaml",
                    workdir=tmp_path,
                    timeout=5,
                )
        # The orphaned (unmarked) pane is best-effort killed before failing loud.
        assert ["/usr/bin/tmux", "kill-pane", "-t", "%99"] in calls

    def test_metadata_records_layout(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        output = _list_panes_lines(
            ("%7", 0, 0, 60, ""),
            ("%1", 0, 61, 60, "origin=%7"),
        )
        self._run(tmp_path, monkeypatch, list_panes_output=output)
        metadata = json.loads((tmp_path / "pane-metadata.json").read_text(encoding="utf-8"))
        assert metadata["split_target_pane"] == "%1"
        assert metadata["split_direction"] == "vertical"
        assert metadata["kaji_agent_panes_before"] == ["%1"]
        assert metadata["kaji_agent_panes_pruned"] == []


def _ansi_noisy_capacity_line() -> str:
    """One physical transcript line with capacity + shutdown + Token usage, each
    character wrapped in a truecolor ANSI escape (Issue #296 real-artifact shape:
    the OB terminal.log has these three phrases connected on a single physical
    line via TUI redraw cursor-movement/color codes).
    """
    raw = (
        "⚠ Selected model is at capacity. Please try a different model. "
        "› Shutting down... Token usage: total=32,386 input=31,159 output=1,227"
    )
    chars = []
    for i, ch in enumerate(raw):
        r, g, b = (i * 7) % 256, (i * 13) % 256, (i * 19) % 256
        chars.append(f"\x1b[38;2;{r};{g};{b};49m{ch}")
    chars.append("\x1b[39m\n")
    return "".join(chars)


def _buried_capacity_transcript() -> str:
    """A large TUI transcript with the capacity line buried well before the
    ``_TERMINAL_LOG_TAIL_CHARS`` (2000-char) tail window, matching the real
    298KB OB artifact where the capacity line was near the head and the tail
    was pure ANSI redraw noise.
    """
    header = "assistant is thinking...\n" * 60
    capacity_line = _ansi_noisy_capacity_line()
    footer = ("\x1b[2K\x1b[1A" * 400) + "\n"
    return header + capacity_line + footer


@pytest.mark.small
class TestExtractTerminalDiagnostic:
    """`extract_terminal_diagnostic` (Issue #296): full-transcript transient scan."""

    def test_detects_capacity_line_buried_before_tail_window(self) -> None:
        text = _buried_capacity_transcript()
        # Regression demonstration: the pre-#296 tail-only extraction
        # (`text[-2000:]`) does not contain the capacity phrase at all.
        assert "at capacity" not in text[-2000:].lower()

        diagnostic = extract_terminal_diagnostic(text)
        assert diagnostic.kind == "provider_error"
        assert diagnostic.matched_pattern == "at capacity"

    def test_kind_no_pattern_for_transient_free_transcript(self) -> None:
        diagnostic = extract_terminal_diagnostic("let me proceed with the next step\n")
        assert diagnostic.kind == "no_pattern"
        assert diagnostic.matched_pattern is None

    def test_kind_empty_for_blank_text(self) -> None:
        diagnostic = extract_terminal_diagnostic("   \n\t  ")
        assert diagnostic.kind == "empty"
        assert diagnostic.clean_tail == ""

    @pytest.mark.parametrize("phrase", ["rate limit", "overloaded"])
    def test_reuses_cli_transient_patterns_consistently(self, phrase: str) -> None:
        diagnostic = extract_terminal_diagnostic(f"provider said: {phrase} exceeded\n")
        assert diagnostic.kind == "provider_error"
        assert diagnostic.matched_pattern == phrase

    def test_ansi_escape_fragments_are_removed_from_excerpt_and_tail(self) -> None:
        diagnostic = extract_terminal_diagnostic(_buried_capacity_transcript())
        assert diagnostic.clean_excerpt is not None
        assert "\x1b" not in diagnostic.clean_excerpt
        assert "\x1b" not in diagnostic.clean_tail
        assert "at capacity" in diagnostic.clean_excerpt.lower()


@pytest.mark.small
class TestSensitiveSafeFocusedMessage:
    """Canonical-only focalization keeps ``Token usage`` out of the gate input."""

    def test_capacity_message_is_transient_but_not_sensitive(self, tmp_path: Path) -> None:
        # This is the core regression guard for review-design 指摘 1: the same
        # physical line carries both "at capacity" and "Token usage", but the
        # CLIExecutionError message built by `_terminal_exit_detail` must only
        # ever surface the canonical pattern literal.
        terminal_log = tmp_path / "terminal.log"
        terminal_log.write_text(_buried_capacity_transcript(), encoding="utf-8")

        message = _terminal_exit_detail(terminal_log)

        assert "at capacity" in message
        assert "Token usage" not in message
        assert is_transient_error_text(message) is True
        assert _sensitive_failure_text(message) is False

    def test_no_pattern_message_keeps_raw_tail_but_is_not_a_candidate(self, tmp_path: Path) -> None:
        terminal_log = tmp_path / "terminal.log"
        terminal_log.write_text("agent failed at launch\n", encoding="utf-8")

        message = _terminal_exit_detail(terminal_log)

        assert "agent failed at launch" in message
        assert is_transient_error_text(message) is False

    def test_no_transient_pattern_is_ever_sensitive(self) -> None:
        # Canonical-only healthiness (review-design 指摘 1): every literal
        # `_TRANSIENT_PATTERNS` can produce must never trip the sensitive gate,
        # independent of any single sample. Breaks loudly if a future pattern
        # addition collides with `_SENSITIVE_FAILURE_PATTERNS`.
        for pattern in _TRANSIENT_PATTERNS:
            message = f"tmux pane exited before writing verdict.yaml; transient provider error detected (pattern: '{pattern}')"
            assert _sensitive_failure_text(message) is False, pattern


@pytest.mark.small
class TestFalsePositiveBoundary:
    """review-design 指摘 3: generic pattern の transcript 全体走査の境界。"""

    def test_benign_transcript_without_transient_words_is_no_pattern(self) -> None:
        # negative: prose with no transient vocabulary must not be misclassified
        # as a candidate, even though the scan covers the entire transcript.
        benign = "let me proceed with the next step and finish up the review.\n" * 5
        diagnostic = extract_terminal_diagnostic(benign)
        assert diagnostic.kind == "no_pattern"
        message = f"tmux pane exited before writing verdict.yaml; no known provider error pattern in transcript; log tail:\n{diagnostic.clean_tail}"
        assert is_transient_error_text(message) is False

    def test_generic_try_again_phrase_is_characterized_as_provider_error(self) -> None:
        # characterization: `try again` is a generic transient pattern shared
        # with headless (cli.py single source of truth). This scan only runs on
        # the verdict-not-written abnormal pane-death path, and the blast radius
        # of a false positive is bounded by RECOVERY_BUDGET=1 (one extra resume).
        diagnostic = extract_terminal_diagnostic("connection reset, please try again\n")
        assert diagnostic.kind == "provider_error"
        assert diagnostic.matched_pattern == "try again"


@pytest.mark.small
class TestReadTerminalDiagnostic:
    """`read_terminal_diagnostic` extraction-failure kinds (no_log / empty)."""

    def test_missing_file_is_no_log(self, tmp_path: Path) -> None:
        diagnostic = read_terminal_diagnostic(tmp_path / "missing-terminal.log")
        assert diagnostic.kind == "no_log"

    def test_empty_file_is_empty(self, tmp_path: Path) -> None:
        terminal_log = tmp_path / "terminal.log"
        terminal_log.write_text("", encoding="utf-8")
        assert read_terminal_diagnostic(terminal_log).kind == "empty"

    def test_missing_file_message_is_diagnostic_unavailable(self, tmp_path: Path) -> None:
        message = _terminal_exit_detail(tmp_path / "missing-terminal.log")
        assert "diagnostic unavailable" in message
        assert is_transient_error_text(message) is False


@pytest.mark.medium
class TestPaneExitStatusDistinguishable:
    """完了条件: pane exit 0 / 非 0 / verdict 有りが観測可能に区別できること。"""

    def _run_pane_dead(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, pane_dead_status: str
    ) -> None:
        prompt = tmp_path / "prompt.txt"
        prompt.write_text("prompt", encoding="utf-8")
        (tmp_path / "terminal.log").write_text("agent failed at launch\n", encoding="utf-8")
        monkeypatch.setenv("TMUX", "/tmp/tmux-sock,1,0")
        monkeypatch.setenv("TMUX_PANE", "%7")
        fake_run = _make_fake_tmux(pane_dead="1", pane_dead_status=pane_dead_status)

        with (
            patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/tmux"),
            patch.object(subprocess, "run", side_effect=fake_run),
        ):
            with pytest.raises(CLIExecutionError):
                execute_interactive_terminal(
                    step=_step("claude"),
                    prompt_path=prompt,
                    verdict_path=tmp_path / "verdict.yaml",
                    workdir=tmp_path,
                    timeout=600,
                )

    def test_pane_dead_status_zero_is_recorded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._run_pane_dead(tmp_path, monkeypatch, pane_dead_status="0")
        metadata = json.loads((tmp_path / "pane-metadata.json").read_text(encoding="utf-8"))
        assert metadata["pane_dead_status"] == "0"
        assert metadata["terminal_diagnostic"]["kind"] == "no_pattern"

    def test_pane_dead_status_non_zero_is_recorded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._run_pane_dead(tmp_path, monkeypatch, pane_dead_status="137")
        metadata = json.loads((tmp_path / "pane-metadata.json").read_text(encoding="utf-8"))
        assert metadata["pane_dead_status"] == "137"
        assert metadata["terminal_diagnostic"]["kind"] == "no_pattern"

    def test_status_zero_and_non_zero_are_distinguishable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._run_pane_dead(tmp_path, monkeypatch, pane_dead_status="0")
        metadata_a = json.loads((tmp_path / "pane-metadata.json").read_text(encoding="utf-8"))

        self._run_pane_dead(tmp_path, monkeypatch, pane_dead_status="137")
        metadata_b = json.loads((tmp_path / "pane-metadata.json").read_text(encoding="utf-8"))

        assert metadata_a["pane_dead_status"] != metadata_b["pane_dead_status"]

    def test_verdict_present_is_normal_exit_without_diagnostic(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prompt = tmp_path / "prompt.txt"
        verdict = tmp_path / "verdict.yaml"
        prompt.write_text("prompt", encoding="utf-8")
        monkeypatch.setenv("TMUX", "/tmp/tmux-sock,1,0")
        monkeypatch.setenv("TMUX_PANE", "%7")
        fake_run = _make_fake_tmux(
            on_split=lambda: verdict.write_text(_PASS_VERDICT, encoding="utf-8")
        )

        with (
            patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/tmux"),
            patch.object(subprocess, "run", side_effect=fake_run),
        ):
            execute_interactive_terminal(
                step=_step("claude"),
                prompt_path=prompt,
                verdict_path=verdict,
                workdir=tmp_path,
                timeout=5,
            )

        metadata = json.loads((tmp_path / "pane-metadata.json").read_text(encoding="utf-8"))
        # Verdict-trigger contract: no CLIExecutionError, no terminal_diagnostic
        # attached (that key is only written on the pane-death fail-loud path).
        assert "terminal_diagnostic" not in metadata


def _tmux_at_least_3_1() -> bool:
    tmux = shutil.which("tmux")
    if tmux is None:
        return False
    proc = subprocess.run([tmux, "-V"], capture_output=True, text=True, check=False)
    import re

    match = re.search(r"tmux\s+(\d+)\.(\d+)", proc.stdout)
    if match is None:
        return False
    return (int(match.group(1)), int(match.group(2))) >= (3, 1)


@pytest.mark.large
@pytest.mark.large_local
@pytest.mark.skipif(not _tmux_at_least_3_1(), reason="requires real tmux >= 3.1 on PATH")
class TestInteractiveTerminalEndToEnd:
    """E2E: real tmux split-window → wrapper.sh → fake agent → verdict → kill-pane."""

    def _start_server(self, socket: str) -> tuple[str, str]:
        """Start a private tmux server and return (TMUX env value, pane id)."""
        tmux = shutil.which("tmux")
        assert tmux is not None
        subprocess.run(
            [tmux, "-L", socket, "new-session", "-d", "-s", "main", "-x", "200", "-y", "50"],
            check=True,
            capture_output=True,
            text=True,
        )
        info = subprocess.run(
            [
                tmux,
                "-L",
                socket,
                "display-message",
                "-p",
                "-t",
                "main",
                "#{socket_path}\t#{pid}\t#{session_id}\t#{pane_id}",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        socket_path, server_pid, session_id, pane_id = info.split("\t")
        tmux_env = f"{socket_path},{server_pid},{session_id.lstrip('$')}"
        return tmux_env, pane_id

    def _list_panes(self, socket: str) -> list[str]:
        tmux = shutil.which("tmux")
        assert tmux is not None
        proc = subprocess.run(
            [tmux, "-L", socket, "list-panes", "-a", "-F", "#{pane_id}"],
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.stdout.split()

    def _kaji_pane_geometry(self, socket: str, origin_pane: str) -> list[tuple[str, int, int]]:
        """Return (pane_id, pane_left, pane_width) for kaji-managed agent panes.

        Filters to panes whose ``@kaji_interactive_terminal`` marker resolves to
        ``origin=<origin_pane>``, ordered by ``pane_top`` ascending.
        """
        tmux = shutil.which("tmux")
        assert tmux is not None
        fmt = "\t".join(
            [
                "#{pane_id}",
                "#{pane_top}",
                "#{pane_left}",
                "#{pane_width}",
                "#{@kaji_interactive_terminal}",
            ]
        )
        proc = subprocess.run(
            [tmux, "-L", socket, "list-panes", "-t", origin_pane, "-F", fmt],
            capture_output=True,
            text=True,
            check=False,
        )
        rows: list[tuple[int, str, int, int]] = []
        for line in proc.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 5:
                continue
            pane_id, top, left, width, marker = parts[:5]
            if pane_id == origin_pane or marker != f"origin={origin_pane}":
                continue
            rows.append((int(top), pane_id, int(left), int(width)))
        rows.sort()
        return [(pid, left, width) for _, pid, left, width in rows]

    def _write_fake_claude(self, fake_bin: Path) -> None:
        fake_claude = fake_bin / "claude"
        # Write the verdict path embedded in the prompt, then stay alive so the
        # pane survives under close_on_verdict=False.
        fake_claude.write_text(
            "#!/usr/bin/env bash\n"
            'for arg in "$@"; do prompt="$arg"; done\n'
            "verdict_path=$(printf '%s\\n' \"$prompt\" | "
            "awk '/write only a pure YAML verdict file to this exact path:/{getline; print; exit}')\n"
            'printf "status: PASS\\nreason: ok\\nevidence: e2e\\nsuggestion: \x27\x27\\n"'
            ' > "$verdict_path"\n'
            "sleep 30\n",
            encoding="utf-8",
        )
        fake_claude.chmod(0o755)

    def test_real_tmux_keeps_right_column_at_two_panes_with_stable_width(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Issue #238: launch agent panes repeatedly with close_on_verdict=False
        # and confirm the right column never exceeds two kaji panes and that the
        # origin/agent widths do not shrink with each additional launch.
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        self._write_fake_claude(fake_bin)
        workdir = tmp_path / "work"
        workdir.mkdir()

        socket = f"kaji-e2e-{uuid.uuid4().hex[:8]}"
        monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
        tmux_env, origin_pane = self._start_server(socket)
        tmux = shutil.which("tmux")
        assert tmux is not None
        try:
            monkeypatch.setenv("TMUX", tmux_env)
            monkeypatch.setenv("TMUX_PANE", origin_pane)

            geometries: list[list[tuple[str, int, int]]] = []
            for index in range(4):
                attempt = tmp_path / f"attempt-{index}"
                attempt.mkdir()
                prompt = attempt / "prompt.txt"
                prompt.write_text("the full task prompt", encoding="utf-8")
                execute_interactive_terminal(
                    step=_step("claude", model="haiku", effort="low"),
                    prompt_path=prompt,
                    verdict_path=attempt / "verdict.yaml",
                    workdir=workdir,
                    timeout=30,
                    close_on_verdict=False,
                )
                geometries.append(self._kaji_pane_geometry(socket, origin_pane))

            # First launch: exactly one managed agent pane in the right column.
            assert len(geometries[0]) == 1
            # Subsequent launches stabilise at the two-pane cap.
            assert len(geometries[1]) == 2
            assert len(geometries[2]) == 2
            assert len(geometries[3]) == 2

            # The right column stays in a single vertical column: every managed
            # pane shares the same pane_left, and that left does not drift right
            # with additional launches (no horizontal re-splitting).
            agent_lefts = {left for geo in geometries for _, left, _ in geo}
            assert len(agent_lefts) == 1
            # Agent pane width is preserved across launches (no shrinking).
            agent_widths = {width for geo in geometries for _, _, width in geo}
            assert len(agent_widths) == 1

            # The origin pane is still present after repeated launches.
            assert origin_pane in self._list_panes(socket)
        finally:
            subprocess.run(
                [tmux, "-L", socket, "kill-server"], capture_output=True, text=True, check=False
            )

    def test_real_tmux_drives_wrapper_to_write_and_resolve_verdict(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        # Fake claude: parse the verdict path the wrapper embeds in the prompt,
        # write a pure-YAML verdict, then stay alive so the runner kills the pane.
        fake_claude = fake_bin / "claude"
        fake_claude.write_text(
            "#!/usr/bin/env bash\n"
            'for arg in "$@"; do prompt="$arg"; done\n'
            "verdict_path=$(printf '%s\\n' \"$prompt\" | "
            "awk '/write only a pure YAML verdict file to this exact path:/{getline; print; exit}')\n"
            'printf "status: PASS\\nreason: ok\\nevidence: e2e\\nsuggestion: \x27\x27\\n"'
            ' > "$verdict_path"\n'
            "sleep 30\n",
            encoding="utf-8",
        )
        fake_claude.chmod(0o755)

        workdir = tmp_path / "work"
        workdir.mkdir()
        attempt = tmp_path / "attempt"
        attempt.mkdir()
        prompt = attempt / "prompt.txt"
        verdict = attempt / "verdict.yaml"
        terminal_log = attempt / "terminal.log"
        prompt.write_text("the full task prompt", encoding="utf-8")

        socket = f"kaji-e2e-{uuid.uuid4().hex[:8]}"
        # Server inherits the modified PATH so the new pane finds fake claude.
        monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
        tmux_env, origin_pane = self._start_server(socket)
        try:
            monkeypatch.setenv("TMUX", tmux_env)
            monkeypatch.setenv("TMUX_PANE", origin_pane)
            result = execute_interactive_terminal(
                step=_step("claude", model="haiku", effort="low"),
                prompt_path=prompt,
                verdict_path=verdict,
                workdir=workdir,
                timeout=30,
            )

            assert result.full_output == ""
            assert verdict.exists()
            resolved = load_verdict_yaml(verdict, {"PASS", "RETRY", "BACK", "ABORT"})
            assert resolved.status == "PASS"
            # pipe-pane recorded the transcript to the attempt directory.
            assert terminal_log.exists()
            # close_on_verdict default True → the agent pane was killed and the
            # origin pane is the only one left.
            panes = self._list_panes(socket)
            assert origin_pane in panes
            assert len(panes) == 1
        finally:
            tmux = shutil.which("tmux")
            assert tmux is not None
            subprocess.run(
                [tmux, "-L", socket, "kill-server"], capture_output=True, text=True, check=False
            )
