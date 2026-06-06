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

from kaji_harness.errors import CLIExecutionError, CLINotFoundError, StepTimeoutError
from kaji_harness.interactive_terminal import (
    _build_tmux_split_argv,
    _pane_dead,
    execute_interactive_terminal,
)
from kaji_harness.models import Step
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
    on_split: object = None,
    calls: list[list[str]] | None = None,
):
    """Build a ``subprocess.run`` replacement that fakes a tmux server.

    ``on_split`` (a no-arg callable) fires when ``split-window`` is seen so the
    test can write ``verdict.yaml`` / ``terminal.log`` at pane-launch time.
    """

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if calls is not None:
            calls.append(argv)
        if argv == [tmux, "-V"]:
            return _completed(stdout=version)
        if argv[:2] == [tmux, "split-window"]:
            if on_split is not None:
                on_split()  # type: ignore[operator]
            return _completed(stdout=f"{pane_id}\n")
        if argv[:2] == [tmux, "pipe-pane"]:
            return _completed()
        if argv[:2] == [tmux, "set-option"]:
            return _completed()
        if argv[:2] == [tmux, "display-message"]:
            if argv[-1] == "#{pane_dead}":
                return _completed(stdout=f"{pane_dead}\n")
            return _completed(
                stdout=_PANE_METADATA.replace("pane_dead=0", f"pane_dead={pane_dead}")
            )
        if argv[:2] == [tmux, "kill-pane"]:
            return _completed()
        raise AssertionError(f"unexpected tmux call: {argv}")

    return fake_run


@pytest.mark.small
class TestBuildTmuxSplitArgv:
    """tmux ``split-window`` command construction (MF2: right split via ``-h``)."""

    def test_split_window_prefix_includes_dash_h_and_pane_id_format(self, tmp_path: Path) -> None:
        prompt = tmp_path / "prompt.txt"
        verdict = tmp_path / "verdict.yaml"
        argv = _build_tmux_split_argv(
            "/usr/bin/tmux",
            WRAPPER,
            target_pane="%7",
            agent="claude",
            prompt_path=prompt,
            verdict_path=verdict,
            workdir=tmp_path,
            resume_session_id="",
            launch_session_id="11111111-1111-4111-8111-111111111111",
            model="haiku",
            effort="low",
        )

        # Exact prefix: `-h` after `-d` puts the new pane to the user's right.
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
        assert str(prompt) in command
        assert str(verdict) in command
        assert "11111111-1111-4111-8111-111111111111" in command
        assert "haiku" in command
        assert "low" in command


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

    def test_tmux_version_below_minimum_fails_loud(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prompt = tmp_path / "prompt.txt"
        prompt.write_text("prompt", encoding="utf-8")
        monkeypatch.setenv("TMUX", "/tmp/tmux-sock,1,0")
        monkeypatch.setenv("TMUX_PANE", "%7")

        def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            assert argv == ["/usr/bin/tmux", "-V"]
            return _completed(stdout="tmux 2.9\n")

        with (
            patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/tmux"),
            patch.object(subprocess, "run", side_effect=fake_run),
        ):
            with pytest.raises(CLINotFoundError, match="tmux >= 3.0"):
                execute_interactive_terminal(
                    step=_step("claude"),
                    prompt_path=prompt,
                    verdict_path=tmp_path / "verdict.yaml",
                    workdir=tmp_path,
                    timeout=5,
                )

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


def _tmux_at_least_3() -> bool:
    tmux = shutil.which("tmux")
    if tmux is None:
        return False
    proc = subprocess.run([tmux, "-V"], capture_output=True, text=True, check=False)
    import re

    match = re.search(r"tmux\s+(\d+)\.(\d+)", proc.stdout)
    if match is None:
        return False
    return (int(match.group(1)), int(match.group(2))) >= (3, 0)


@pytest.mark.large
@pytest.mark.large_local
@pytest.mark.skipif(not _tmux_at_least_3(), reason="requires real tmux >= 3.0 on PATH")
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
