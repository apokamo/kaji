"""Tests for the interactive terminal runner (Issue #224).

Covers the runner argv builder, kitty fail-fast, session-id resolution
(Claude launch UUID / Codex terminal.log + session store fallback), verdict
polling, close-on-verdict cleanup, timeout, and the wrapper shell contract
(arg order, cwd, agent command lines, transcript branching).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from kaji_harness.errors import CLIExecutionError, CLINotFoundError, StepTimeoutError
from kaji_harness.interactive_terminal import (
    _build_kitty_argv,
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


def _step(
    agent: str, *, step_id: str = "design", model: str | None = None, effort: str | None = None
) -> Step:
    return Step(id=step_id, skill="issue-design", agent=agent, model=model, effort=effort)


@pytest.mark.small
class TestBuildKittyArgv:
    """`_build_kitty_argv` produces the exact argv expected by the wrapper."""

    def test_title_hold_wrapper_and_nine_args_in_order(self, tmp_path: Path) -> None:
        prompt = tmp_path / "prompt.txt"
        verdict = tmp_path / "verdict.yaml"
        terminal_log = tmp_path / "terminal.log"
        argv = _build_kitty_argv(
            "/usr/bin/kitty",
            WRAPPER,
            agent="claude",
            step_id="design",
            prompt_path=prompt,
            verdict_path=verdict,
            terminal_log=terminal_log,
            workdir=tmp_path,
            resume_session_id="",
            launch_session_id="11111111-1111-4111-8111-111111111111",
            model="haiku",
            effort="low",
        )

        assert argv[:5] == [
            "/usr/bin/kitty",
            "--title",
            "kaji-claude-design",
            "--hold",
            str(WRAPPER),
        ]
        # The 9 wrapper args follow in Wrapper 契約 order.
        assert argv[5:] == [
            "claude",
            str(prompt),
            str(verdict),
            str(terminal_log),
            str(tmp_path),
            "",
            "11111111-1111-4111-8111-111111111111",
            "haiku",
            "low",
        ]

    def test_title_uses_agent_and_step_id(self, tmp_path: Path) -> None:
        argv = _build_kitty_argv(
            "kitty",
            WRAPPER,
            agent="codex",
            step_id="review",
            prompt_path=tmp_path / "prompt.txt",
            verdict_path=tmp_path / "verdict.yaml",
            terminal_log=tmp_path / "terminal.log",
            workdir=tmp_path,
            resume_session_id="",
            launch_session_id="",
            model="",
            effort="",
        )
        assert argv[1:3] == ["--title", "kaji-codex-review"]


@pytest.mark.small
class TestRunnerEntryValidation:
    """Fail-fast / launch-session-id rules at the runner entry."""

    def test_missing_kitty_fails_loud(self, tmp_path: Path) -> None:
        prompt = tmp_path / "prompt.txt"
        prompt.write_text("prompt", encoding="utf-8")
        with patch("kaji_harness.interactive_terminal.shutil.which", return_value=None):
            with pytest.raises(CLINotFoundError, match="kitty"):
                execute_interactive_terminal(
                    step=_step("claude"),
                    prompt_path=prompt,
                    verdict_path=tmp_path / "verdict.yaml",
                    workdir=tmp_path,
                    timeout=5,
                )

    def test_rejects_unsupported_agent(self, tmp_path: Path) -> None:
        prompt = tmp_path / "prompt.txt"
        prompt.write_text("prompt", encoding="utf-8")
        with patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/kitty"):
            with pytest.raises(ValueError, match="does not support agent"):
                execute_interactive_terminal(
                    step=Step(id="s", skill="x", agent="gemini"),
                    prompt_path=prompt,
                    verdict_path=tmp_path / "verdict.yaml",
                    workdir=tmp_path,
                    timeout=5,
                )

    def test_missing_prompt_fails_loud(self, tmp_path: Path) -> None:
        with patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/kitty"):
            with pytest.raises(FileNotFoundError, match="prompt.txt"):
                execute_interactive_terminal(
                    step=_step("claude"),
                    prompt_path=tmp_path / "prompt.txt",
                    verdict_path=tmp_path / "verdict.yaml",
                    workdir=tmp_path,
                    timeout=5,
                )

    def test_claude_fresh_generates_launch_session_id(self, tmp_path: Path) -> None:
        prompt = tmp_path / "prompt.txt"
        verdict = tmp_path / "verdict.yaml"
        prompt.write_text("prompt", encoding="utf-8")
        calls: list[list[str]] = []

        class FakePopen:
            pid = 12345

            def __init__(self, argv: list[str], cwd: Path, start_new_session: bool) -> None:
                calls.append(argv)
                assert cwd == tmp_path
                assert start_new_session is True
                verdict.write_text(_PASS_VERDICT, encoding="utf-8")

            def poll(self) -> int:
                return 0

        with (
            patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/kitty"),
            patch(
                "kaji_harness.interactive_terminal.uuid.uuid4",
                return_value=uuid.UUID("11111111-1111-4111-8111-111111111111"),
            ),
            patch.object(subprocess, "Popen", FakePopen),
            patch("kaji_harness.interactive_terminal._close_terminal") as close,
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
        close.assert_called_once()
        # The 9 wrapper args are the kitty argv tail. Last 4 = resume, launch,
        # model, effort: Claude fresh leaves resume empty and gets the UUID.
        assert calls[0][-4:] == ["", "11111111-1111-4111-8111-111111111111", "haiku", "low"]
        assert calls[0][-9:-5] == [
            "claude",
            str(prompt),
            str(verdict),
            str(tmp_path / "terminal.log"),
        ]

    def test_resume_passes_session_id_and_no_launch_uuid(self, tmp_path: Path) -> None:
        prompt = tmp_path / "prompt.txt"
        verdict = tmp_path / "verdict.yaml"
        prompt.write_text("prompt", encoding="utf-8")
        calls: list[list[str]] = []

        class FakePopen:
            pid = 12345

            def __init__(self, argv: list[str], cwd: Path, start_new_session: bool) -> None:
                calls.append(argv)
                verdict.write_text(_PASS_VERDICT, encoding="utf-8")

            def poll(self) -> int:
                return 0

        with (
            patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/kitty"),
            patch.object(subprocess, "Popen", FakePopen),
            patch("kaji_harness.interactive_terminal._close_terminal"),
        ):
            result = execute_interactive_terminal(
                step=_step("claude", step_id="fix"),
                prompt_path=prompt,
                verdict_path=verdict,
                workdir=tmp_path,
                timeout=5,
                session_id="resume-session",
            )

        assert result.session_id == "resume-session"
        # resume id (7th), launch id (8th, empty), model (empty), effort (empty)
        assert calls[0][-4:] == ["resume-session", "", "", ""]

    def test_codex_fresh_does_not_generate_launch_uuid(self, tmp_path: Path) -> None:
        prompt = tmp_path / "prompt.txt"
        verdict = tmp_path / "verdict.yaml"
        prompt.write_text("prompt", encoding="utf-8")
        calls: list[list[str]] = []

        class FakePopen:
            pid = 12345

            def __init__(self, argv: list[str], cwd: Path, start_new_session: bool) -> None:
                calls.append(argv)
                verdict.write_text(_PASS_VERDICT, encoding="utf-8")

            def poll(self) -> int:
                return 0

        with (
            patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/kitty"),
            patch.object(subprocess, "Popen", FakePopen),
            patch("kaji_harness.interactive_terminal._close_terminal"),
        ):
            execute_interactive_terminal(
                step=_step("codex"),
                prompt_path=prompt,
                verdict_path=verdict,
                workdir=tmp_path,
                timeout=5,
            )

        # Codex never gets a runner-minted launch UUID: 7th (resume) and 8th
        # (launch) wrapper args are both empty.
        assert calls[0][-4:] == ["", "", "", ""]


@pytest.mark.small
class TestCodexSessionIdExtraction:
    """Codex session id from terminal.log, with session-store fallback."""

    def _run(self, tmp_path: Path, popen_cls: type) -> str | None:
        prompt = tmp_path / "prompt.txt"
        verdict = tmp_path / "verdict.yaml"
        prompt.write_text("prompt", encoding="utf-8")
        with (
            patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/kitty"),
            patch.object(subprocess, "Popen", popen_cls),
            patch("kaji_harness.interactive_terminal._close_terminal"),
        ):
            result = execute_interactive_terminal(
                step=_step("codex", step_id="review"),
                prompt_path=prompt,
                verdict_path=verdict,
                workdir=tmp_path,
                timeout=5,
            )
        return result.session_id

    def test_extracts_from_terminal_log(self, tmp_path: Path) -> None:
        verdict = tmp_path / "verdict.yaml"
        terminal_log = tmp_path / "terminal.log"

        class FakePopen:
            pid = 1

            def __init__(self, argv: list[str], cwd: Path, start_new_session: bool) -> None:
                verdict.write_text(_PASS_VERDICT, encoding="utf-8")
                terminal_log.write_text(
                    "To continue, run codex resume 22222222-2222-4222-8222-222222222222\n",
                    encoding="utf-8",
                )

            def poll(self) -> int:
                return 0

        assert self._run(tmp_path, FakePopen) == "22222222-2222-4222-8222-222222222222"

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

        class FakePopen:
            pid = 1

            def __init__(self, argv: list[str], cwd: Path, start_new_session: bool) -> None:
                verdict.write_text(_PASS_VERDICT, encoding="utf-8")

            def poll(self) -> int:
                return 0

        assert self._run(tmp_path, FakePopen) == "44444444-4444-4444-8444-444444444444"

    def test_session_store_fallback_ignores_unrelated_rollout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        verdict = tmp_path / "verdict.yaml"
        codex_home = tmp_path / "codex-home"
        sessions_dir = codex_home / "sessions" / "2026" / "06" / "05"
        sessions_dir.mkdir(parents=True)
        # A rollout that does NOT reference this attempt's prompt/verdict path.
        other = (
            sessions_dir / "rollout-2026-06-05T00-00-00-99999999-9999-4999-8999-999999999999.jsonl"
        )
        other.write_text('{"type":"user","text":"unrelated session"}\n', encoding="utf-8")
        monkeypatch.setenv("CODEX_HOME", str(codex_home))

        class FakePopen:
            pid = 1

            def __init__(self, argv: list[str], cwd: Path, start_new_session: bool) -> None:
                verdict.write_text(_PASS_VERDICT, encoding="utf-8")

            def poll(self) -> int:
                return 0

        assert self._run(tmp_path, FakePopen) is None


@pytest.mark.medium
class TestRunnerVerdictAndCleanup:
    """Verdict polling, close-on-verdict cleanup, and timeout cleanup."""

    def test_verdict_appearance_returns_cli_result(self, tmp_path: Path) -> None:
        prompt = tmp_path / "prompt.txt"
        verdict = tmp_path / "verdict.yaml"
        prompt.write_text("prompt", encoding="utf-8")

        class FakePopen:
            pid = 1

            def __init__(self, argv: list[str], cwd: Path, start_new_session: bool) -> None:
                verdict.write_text(_PASS_VERDICT, encoding="utf-8")

            def poll(self) -> int:
                return 0

        with (
            patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/kitty"),
            patch.object(subprocess, "Popen", FakePopen),
            patch("kaji_harness.interactive_terminal._close_terminal"),
        ):
            result = execute_interactive_terminal(
                step=_step("claude"),
                prompt_path=prompt,
                verdict_path=verdict,
                workdir=tmp_path,
                timeout=5,
            )
        assert result.full_output == ""
        assert verdict.exists()

    def test_close_on_verdict_true_calls_cleanup(self, tmp_path: Path) -> None:
        prompt = tmp_path / "prompt.txt"
        verdict = tmp_path / "verdict.yaml"
        prompt.write_text("prompt", encoding="utf-8")

        class FakePopen:
            pid = 1

            def __init__(self, argv: list[str], cwd: Path, start_new_session: bool) -> None:
                verdict.write_text(_PASS_VERDICT, encoding="utf-8")

            def poll(self) -> int:
                return 0

        with (
            patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/kitty"),
            patch.object(subprocess, "Popen", FakePopen),
            patch("kaji_harness.interactive_terminal._close_terminal") as close,
        ):
            execute_interactive_terminal(
                step=_step("claude"),
                prompt_path=prompt,
                verdict_path=verdict,
                workdir=tmp_path,
                timeout=5,
                close_on_verdict=True,
            )
        close.assert_called_once()

    def test_close_on_verdict_false_skips_cleanup(self, tmp_path: Path) -> None:
        prompt = tmp_path / "prompt.txt"
        verdict = tmp_path / "verdict.yaml"
        prompt.write_text("prompt", encoding="utf-8")

        class FakePopen:
            pid = 1

            def __init__(self, argv: list[str], cwd: Path, start_new_session: bool) -> None:
                verdict.write_text(_PASS_VERDICT, encoding="utf-8")

            def poll(self) -> None:
                return None

        with (
            patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/kitty"),
            patch.object(subprocess, "Popen", FakePopen),
            patch("kaji_harness.interactive_terminal._close_terminal") as close,
        ):
            execute_interactive_terminal(
                step=_step("claude"),
                prompt_path=prompt,
                verdict_path=verdict,
                workdir=tmp_path,
                timeout=5,
                close_on_verdict=False,
            )
        close.assert_not_called()

    def test_timeout_raises_and_cleans_up(self, tmp_path: Path) -> None:
        prompt = tmp_path / "prompt.txt"
        prompt.write_text("prompt", encoding="utf-8")

        class FakePopen:
            pid = 1

            def __init__(self, argv: list[str], cwd: Path, start_new_session: bool) -> None:
                pass  # never writes verdict

            def poll(self) -> None:
                return None  # still alive: drives the loop to the deadline

        with (
            patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/kitty"),
            patch.object(subprocess, "Popen", FakePopen),
            patch("kaji_harness.interactive_terminal.time.sleep", return_value=None),
            patch(
                "kaji_harness.interactive_terminal.time.monotonic",
                side_effect=[0.0, 0.5, 2.0],
            ),
            patch("kaji_harness.interactive_terminal._close_terminal") as close,
        ):
            with pytest.raises(StepTimeoutError):
                execute_interactive_terminal(
                    step=_step("claude"),
                    prompt_path=prompt,
                    verdict_path=tmp_path / "verdict.yaml",
                    workdir=tmp_path,
                    timeout=1,
                )
        close.assert_called_once()

    def test_early_terminal_exit_fails_loud_before_timeout(self, tmp_path: Path) -> None:
        prompt = tmp_path / "prompt.txt"
        prompt.write_text("prompt", encoding="utf-8")
        terminal_log = prompt.parent / "terminal.log"
        terminal_log.write_text("kitty: cannot open display\n", encoding="utf-8")

        class FakePopen:
            pid = 1

            def __init__(self, argv: list[str], cwd: Path, start_new_session: bool) -> None:
                pass  # never writes verdict; exits immediately

            def poll(self) -> int:
                return 1  # already exited non-zero before any verdict

        with (
            patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/kitty"),
            patch.object(subprocess, "Popen", FakePopen),
            patch("kaji_harness.interactive_terminal.time.sleep", return_value=None),
        ):
            with pytest.raises(CLIExecutionError) as excinfo:
                execute_interactive_terminal(
                    step=_step("claude"),
                    prompt_path=prompt,
                    verdict_path=tmp_path / "verdict.yaml",
                    workdir=tmp_path,
                    timeout=600,
                )
        assert excinfo.value.returncode == 1
        assert "cannot open display" in excinfo.value.stderr


def _fake_bin_with(tmp_path: Path, *tools: str) -> Path:
    """Create a bin dir symlinking real tools (so we can control PATH)."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    for tool in tools:
        real = shutil.which(tool)
        assert real is not None, f"required tool not found on host: {tool}"
        (fake_bin / tool).symlink_to(real)
    return fake_bin


@pytest.mark.medium
class TestInteractiveTerminalWrapper:
    """Wrapper shell contract: cwd, arg order, agent commands, transcript branch."""

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
        path_suffix: bool = True,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        verdict = tmp_path / "verdict.yaml"
        env = dict(os.environ)
        path = str(path_prefix)
        if path_suffix:
            path = f"{path_prefix}:{os.environ['PATH']}"
        env["PATH"] = path
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
        return [
            agent,
            str(prompt),
            str(tmp_path / "verdict.yaml"),
            str(tmp_path / "terminal.log"),
            str(workdir),
        ]

    def test_wrapper_cds_into_workdir_before_agent(self, tmp_path: Path) -> None:
        workdir = tmp_path / "work"
        workdir.mkdir()
        _, cwd_path = self._fake_agent_recording_argv(tmp_path, "claude")
        fake_bin = tmp_path / "bin"
        (tmp_path / "prompt.txt").write_text("prompt", encoding="utf-8")
        result = self._run_wrapper(
            tmp_path,
            "claude",
            [
                "claude",
                str(tmp_path / "prompt.txt"),
                str(tmp_path / "verdict.yaml"),
                str(tmp_path / "terminal.log"),
                str(workdir),
            ],
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
        wrapper_args = self._base_args(tmp_path, "claude")
        result = self._run_wrapper(
            tmp_path,
            "claude",
            wrapper_args,
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
        wrapper_args = self._base_args(tmp_path, "claude") + [
            "",  # resume
            "launch-uuid",  # launch
            "haiku",  # model
            "low",  # effort
        ]
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
        wrapper_args = self._base_args(tmp_path, "claude") + [
            "resume-uuid",  # resume
            "",  # launch
            "haiku",
            "low",
        ]
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
        workdir = tmp_path / "work"
        workdir.mkdir(exist_ok=True)
        wrapper_args = [
            "codex",
            str(tmp_path / "prompt.txt"),
            str(tmp_path / "verdict.yaml"),
            str(tmp_path / "terminal.log"),
            str(workdir),
            "",  # resume
            "",  # launch
            "gpt-5.4-mini",
            "low",
        ]
        (tmp_path / "prompt.txt").write_text("prompt", encoding="utf-8")
        result = self._run_wrapper(tmp_path, "codex", wrapper_args, path_prefix=fake_bin)
        assert result.returncode == 0, result.stderr
        args = args_path.read_text(encoding="utf-8").splitlines()
        # The prompt (last positional) is multi-line; assert the fixed prefix only.
        assert args[:7] == [
            "--cd",
            str(workdir),
            "--dangerously-bypass-approvals-and-sandbox",
            "--model",
            "gpt-5.4-mini",
            "--config",
            'model_reasoning_effort="low"',
        ]

    def test_codex_resume_command_matches_contract(self, tmp_path: Path) -> None:
        args_path, _ = self._fake_agent_recording_argv(tmp_path, "codex")
        fake_bin = tmp_path / "bin"
        workdir = tmp_path / "work"
        workdir.mkdir(exist_ok=True)
        wrapper_args = [
            "codex",
            str(tmp_path / "prompt.txt"),
            str(tmp_path / "verdict.yaml"),
            str(tmp_path / "terminal.log"),
            str(workdir),
            "33333333-3333-4333-8333-333333333333",  # resume
            "",  # launch
            "gpt-5.4-mini",
            "low",
        ]
        (tmp_path / "prompt.txt").write_text("prompt", encoding="utf-8")
        result = self._run_wrapper(tmp_path, "codex", wrapper_args, path_prefix=fake_bin)
        assert result.returncode == 0, result.stderr
        args = args_path.read_text(encoding="utf-8").splitlines()
        assert args[:4] == [
            "resume",
            "--cd",
            str(workdir),
            "--dangerously-bypass-approvals-and-sandbox",
        ]
        assert args[4:9] == [
            "--model",
            "gpt-5.4-mini",
            "--config",
            'model_reasoning_effort="low"',
            "33333333-3333-4333-8333-333333333333",
        ]

    def test_transcript_recorded_with_util_linux_script(self, tmp_path: Path) -> None:
        """util-linux script present → terminal.log path is used."""
        if shutil.which("script") is None:
            pytest.skip("script(1) not available on host")
        version = subprocess.run(
            ["script", "--version"], capture_output=True, text=True, check=False
        )
        if "util-linux" not in version.stdout.lower():
            pytest.skip("host script is not util-linux")

        args_path, _ = self._fake_agent_recording_argv(tmp_path, "claude")
        fake_bin = tmp_path / "bin"
        terminal_log = tmp_path / "terminal.log"
        (tmp_path / "prompt.txt").write_text("prompt", encoding="utf-8")
        wrapper_args = [
            "claude",
            str(tmp_path / "prompt.txt"),
            str(tmp_path / "verdict.yaml"),
            str(terminal_log),
            str(tmp_path),
        ]
        # real script must be reachable: keep system PATH after fake_bin.
        result = self._run_wrapper(tmp_path, "claude", wrapper_args, path_prefix=fake_bin)
        assert result.returncode == 0, result.stderr
        assert terminal_log.exists()

    def test_transcript_unavailable_when_script_absent(self, tmp_path: Path) -> None:
        """script absent → agent runs directly with a transcript-unavailable warning."""
        fake_bin = _fake_bin_with(tmp_path, "bash")
        args_path, _ = self._fake_agent_recording_argv(tmp_path, "claude")
        terminal_log = tmp_path / "terminal.log"
        (tmp_path / "prompt.txt").write_text("prompt", encoding="utf-8")
        wrapper_args = [
            "claude",
            str(tmp_path / "prompt.txt"),
            str(tmp_path / "verdict.yaml"),
            str(terminal_log),
            str(tmp_path),
        ]
        # PATH = fake_bin only (bash + fake claude), so script(1) is absent.
        result = self._run_wrapper(
            tmp_path, "claude", wrapper_args, path_prefix=fake_bin, path_suffix=False
        )
        assert result.returncode == 0, result.stderr
        assert "util-linux script(1) not found" in result.stderr
        assert args_path.exists()  # agent ran directly
        assert not terminal_log.exists()

    def test_transcript_fail_soft_when_script_not_util_linux(self, tmp_path: Path) -> None:
        """script present but non-util-linux → fail-soft direct launch (no long-option crash)."""
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        sentinel = tmp_path / "script-invoked-with-command"
        # Fake BSD-style script: --version lacks 'util-linux'; long options abort.
        (fake_bin / "script").write_text(
            "#!/usr/bin/env bash\n"
            'if [[ "$1" == "--version" ]]; then echo "script (BSD)"; exit 0; fi\n'
            f'touch "{sentinel}"\n'
            "exit 3\n",
            encoding="utf-8",
        )
        (fake_bin / "script").chmod(0o755)
        args_path, _ = self._fake_agent_recording_argv(tmp_path, "claude")
        terminal_log = tmp_path / "terminal.log"
        (tmp_path / "prompt.txt").write_text("prompt", encoding="utf-8")
        wrapper_args = [
            "claude",
            str(tmp_path / "prompt.txt"),
            str(tmp_path / "verdict.yaml"),
            str(terminal_log),
            str(tmp_path),
        ]
        result = self._run_wrapper(tmp_path, "claude", wrapper_args, path_prefix=fake_bin)
        assert result.returncode == 0, result.stderr
        # The non-util-linux script must NOT be invoked with --command.
        assert not sentinel.exists()
        assert args_path.exists()  # agent ran directly
        assert not terminal_log.exists()


@pytest.mark.large
@pytest.mark.large_local
class TestInteractiveTerminalEndToEnd:
    """E2E: fake kitty → real wrapper.sh → fake agent → verdict.yaml → runner resolves."""

    def test_fake_kitty_drives_wrapper_to_write_verdict(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()

        # Fake kitty: strip its own options (--title VALUE / --hold), then exec
        # the wrapper command with the 9 args in the exact Wrapper 契約 order.
        fake_kitty = fake_bin / "kitty"
        fake_kitty.write_text(
            "#!/usr/bin/env bash\n"
            "while [[ $# -gt 0 ]]; do\n"
            '  case "$1" in\n'
            "    --title) shift 2 ;;\n"
            "    --hold) shift ;;\n"
            "    *) break ;;\n"
            "  esac\n"
            "done\n"
            'exec "$@"\n',
            encoding="utf-8",
        )
        fake_kitty.chmod(0o755)

        # Fake claude: write a pure-YAML verdict to the path runner expects.
        fake_claude = fake_bin / "claude"
        fake_claude.write_text(
            "#!/usr/bin/env bash\n"
            'printf "status: PASS\\nreason: ok\\nevidence: e2e\\nsuggestion: \x27\x27\\n"'
            ' > "$FAKE_VERDICT_PATH"\n',
            encoding="utf-8",
        )
        fake_claude.chmod(0o755)

        workdir = tmp_path / "work"
        workdir.mkdir()
        attempt = tmp_path / "attempt"
        attempt.mkdir()
        prompt = attempt / "prompt.txt"
        verdict = attempt / "verdict.yaml"
        prompt.write_text("the full task prompt", encoding="utf-8")

        monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
        monkeypatch.setenv("FAKE_VERDICT_PATH", str(verdict))

        with patch("kaji_harness.interactive_terminal.shutil.which", return_value=str(fake_kitty)):
            result = execute_interactive_terminal(
                step=_step("claude", model="haiku", effort="low"),
                prompt_path=prompt,
                verdict_path=verdict,
                workdir=workdir,
                timeout=30,
            )

        assert result.full_output == ""
        assert verdict.exists()
        # The runner's artifact-primary path can resolve the agent-written verdict.
        resolved = load_verdict_yaml(verdict, {"PASS", "RETRY", "BACK", "ABORT"})
        assert resolved.status == "PASS"
