"""Tests for the terminal-backend independent helpers of the interactive terminal runner.

Covers the shared helpers in ``kaji_harness.interactive_terminal_common`` that both the tmux
and Herdr backends use (Issue #474): abnormal-exit / Codex session resolution (Issue #403),
the ``wrapper.sh`` shell contract, and terminal transcript diagnostics (provider error /
sensitive marker, Issue #296), plus the import boundary that keeps these helpers free of tmux.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from kaji_harness import interactive_terminal_common
from kaji_harness.cli import _TRANSIENT_PATTERNS, is_transient_error_text
from kaji_harness.errors import SessionResolution
from kaji_harness.interactive_terminal_common import (
    _build_wrapper_command,
    _extract_codex_session_id,
    _resolve_abnormal_exit_session,
    _terminal_exit_detail,
    _unique_codex_session_id_from_store,
    _wrapper_skill_invocation,
    extract_terminal_diagnostic,
    read_terminal_diagnostic,
)
from kaji_harness.models import Step
from kaji_harness.recovery.handler import _sensitive_failure_text

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


def _completed(
    stdout: str = "", stderr: str = "", returncode: int = 0
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.mark.small
class TestResolveAbnormalExitSessionPureBranches:
    """Issue #403: 異常終了経路の session 解決のうち、store I/O を伴わない分岐。"""

    def _resolve(
        self,
        agent: str,
        *,
        resume_session_id: str | None = None,
        launch_session_id: str = "",
        pane_alive: bool = True,
    ) -> SessionResolution:
        return _resolve_abnormal_exit_session(
            agent,
            prompt_path=Path("/tmp/attempt-001/prompt.txt"),
            verdict_path=Path("/tmp/attempt-001/verdict.yaml"),
            resume_session_id=resume_session_id,
            launch_session_id=launch_session_id,
            pane_alive=pane_alive,
        )

    def test_claude_resume_returns_resume_input(self) -> None:
        # `claude --resume <id>` は同一 session を継続するため、resume 入力が当該 attempt の ID。
        assert self._resolve("claude", resume_session_id="parent-sess") == SessionResolution(
            "parent-sess"
        )
        assert self._resolve(
            "claude", resume_session_id="parent-sess", pane_alive=False
        ) == SessionResolution("parent-sess")

    def test_claude_fresh_timeout_returns_launch_session_id(self) -> None:
        assert self._resolve(
            "claude", launch_session_id="launch-uuid", pane_alive=True
        ) == SessionResolution("launch-uuid")

    def test_claude_fresh_pane_dead_returns_none(self) -> None:
        # pane 死亡は起動失敗を含むため、採番 UUID に対応する session が実在しない。
        assert self._resolve(
            "claude", launch_session_id="launch-uuid", pane_alive=False
        ) == SessionResolution(None)

    def test_antigravity_always_returns_none(self) -> None:
        assert self._resolve("antigravity", launch_session_id="x") == SessionResolution(None)

    def test_resolution_none_is_distinguishable_from_untried(self) -> None:
        # 「解決を試みて null」は確定値であり、「未試行（例外に載せない）」とは別物。
        resolved = self._resolve("antigravity")
        assert resolved is not None
        assert resolved.session_id is None


@pytest.mark.small
class TestWrapperSkillInvocationArgument:
    """Issue #408: wrapper の第 10 位置引数 ``skill_invocation``（codex のときだけ非空）。"""

    def _command_argv(self, **overrides: str) -> list[str]:
        import shlex

        kwargs = {
            "agent": "codex",
            "prompt_path": Path("/p/prompt.txt"),
            "verdict_path": Path("/p/verdict.yaml"),
            "workdir": Path("/w"),
            "resume_session_id": "",
            "launch_session_id": "",
            "model": "m",
            "effort": "e",
            "execution_policy": "auto",
        }
        kwargs.update(overrides)
        return shlex.split(_build_wrapper_command(Path("/pkg/wrapper.sh"), **kwargs))  # type: ignore[arg-type]

    def test_tenth_argument_is_invocation_line_for_codex(self) -> None:
        argv = self._command_argv(skill_invocation="$review を実行してください。")

        assert len(argv) == 11
        assert argv[10] == "$review を実行してください。"

    def test_tenth_argument_defaults_to_empty_string(self) -> None:
        argv = self._command_argv(agent="claude")

        assert len(argv) == 11
        assert argv[10] == ""

    def test_wrapper_skill_invocation_is_codex_only(self) -> None:
        assert _wrapper_skill_invocation(_step("codex")) == "$issue-design を実行してください。"
        assert _wrapper_skill_invocation(_step("claude")) == ""
        assert _wrapper_skill_invocation(_step("antigravity")) == ""


@pytest.mark.medium
class TestInteractiveTerminalWrapper:
    """Wrapper shell contract: cwd, arg order, and agent command lines (10 positions)."""

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
        # 9-position contract prefix: agent prompt verdict workdir ...
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

    @pytest.mark.parametrize(
        ("policy", "expected_flag"),
        [
            ("auto", "--dangerously-skip-permissions"),
            ("sandbox", "--sandbox"),
            ("interactive", None),
        ],
    )
    def test_antigravity_policy_command_matches_contract(
        self,
        tmp_path: Path,
        policy: str,
        expected_flag: str | None,
    ) -> None:
        """AGY interactive argv に 3 policy を同じ意味で写像する。"""
        args_path, _ = self._fake_agent_recording_argv(tmp_path, "agy")
        fake_bin = tmp_path / "bin"
        wrapper_args = self._base_args(tmp_path, "antigravity") + [
            "",
            "",
            "gemini-3-pro",
            "high",
            policy,
        ]

        result = self._run_wrapper(
            tmp_path,
            "agy",
            wrapper_args,
            path_prefix=fake_bin,
        )

        assert result.returncode == 0, result.stderr
        args = args_path.read_text(encoding="utf-8").splitlines()
        prompt_index = args.index("-i")
        assert args[prompt_index + 1].startswith("Read the full task prompt from:")
        assert "--model" in args
        assert "gemini-3-pro" in args
        assert "--effort" in args
        assert "high" in args
        for policy_flag in ("--dangerously-skip-permissions", "--sandbox"):
            assert (policy_flag in args) is (policy_flag == expected_flag)

    def test_antigravity_resume_is_rejected_by_wrapper(self, tmp_path: Path) -> None:
        """validation を迂回した AGY resume を wrapper でも拒否する。"""
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir(exist_ok=True)
        wrapper_args = self._base_args(tmp_path, "antigravity") + [
            "unsupported-session",
            "",
            "",
            "",
            "auto",
        ]

        result = self._run_wrapper(
            tmp_path,
            "agy",
            wrapper_args,
            path_prefix=fake_bin,
        )

        assert result.returncode == 2
        assert "does not support resume" in result.stderr


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


def _ansi_noisy_capacity_and_401_line() -> str:
    """One physical transcript line combining a transient capacity phrase with a
    genuine auth marker (PR #300 review: providers can emit ``rate limit ... 401``
    / ``try again ... invalid token`` style lines where transient and sensitive
    markers share one line).
    """
    raw = (
        "⚠ Selected model is at capacity. Please try a different model. "
        "› upstream returned 401 status"
    )
    chars = []
    for i, ch in enumerate(raw):
        r, g, b = (i * 7) % 256, (i * 13) % 256, (i * 19) % 256
        chars.append(f"\x1b[38;2;{r};{g};{b};49m{ch}")
    chars.append("\x1b[39m\n")
    return "".join(chars)


def _buried_capacity_and_401_transcript() -> str:
    header = "assistant is thinking...\n" * 60
    line = _ansi_noisy_capacity_and_401_line()
    footer = ("\x1b[2K\x1b[1A" * 400) + "\n"
    return header + line + footer


@pytest.mark.small
class TestSensitiveMarkerCoexistsWithTransient:
    """PR #300 review (P2): a transcript carrying both a transient literal and a
    high-confidence auth marker (401/403/credential/permission denied/unauthorized/
    authentication failed) must let the sensitive marker reach the message that
    becomes ``result.json.error``, so ``_safety_gates`` can still trip
    ``sensitive_failure_pattern`` instead of being structurally bypassed.
    """

    def test_diagnostic_records_high_confidence_sensitive_marker(self) -> None:
        diagnostic = extract_terminal_diagnostic(_buried_capacity_and_401_transcript())
        assert diagnostic.kind == "provider_error"
        assert diagnostic.matched_pattern == "at capacity"
        assert diagnostic.sensitive_marker == "401"

    def test_exit_detail_message_trips_sensitive_gate(self, tmp_path: Path) -> None:
        terminal_log = tmp_path / "terminal.log"
        terminal_log.write_text(_buried_capacity_and_401_transcript(), encoding="utf-8")

        message = _terminal_exit_detail(terminal_log)

        assert "at capacity" in message
        assert is_transient_error_text(message) is True
        assert _sensitive_failure_text(message) is True

    def test_exit_detail_message_trips_sensitive_gate_for_invalid_token(
        self, tmp_path: Path
    ) -> None:
        # PR #300 review: reproduces the reported gap directly — a transcript
        # whose only sensitive marker is a compound "invalid token" phrase (no
        # 401/403/credential/etc alongside it) must still trip the gate, even
        # though bare `token` is excluded to avoid "Token usage" telemetry noise.
        terminal_log = tmp_path / "terminal.log"
        terminal_log.write_text("Please try again: invalid token\n", encoding="utf-8")

        message = _terminal_exit_detail(terminal_log)

        assert "try again" in message
        assert is_transient_error_text(message) is True
        assert _sensitive_failure_text(message) is True


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


_UUID_A = "11111111-1111-4111-8111-111111111111"
_UUID_B = "22222222-2222-4222-8222-222222222222"


def _write_rollout(
    sessions: Path, session_id: str, body: str, *, mtime: float | None = None
) -> Path:
    day = sessions / "2026" / "10" / "17"
    day.mkdir(parents=True, exist_ok=True)
    rollout = day / f"rollout-2026-10-17T00-00-00-{session_id}.jsonl"
    rollout.write_text(body, encoding="utf-8")
    if mtime is not None:
        os.utime(rollout, (mtime, mtime))
    return rollout


@pytest.fixture()
def codex_attempt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    """CODEX_HOME を tmp_path に向け、(sessions dir, prompt.txt, verdict.yaml) を返す。"""
    codex_home = tmp_path / "codex-home"
    sessions = codex_home / "sessions"
    sessions.mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    attempt = tmp_path / "attempt-001"
    attempt.mkdir()
    prompt = attempt / "prompt.txt"
    prompt.write_text("do the step", encoding="utf-8")
    return sessions, prompt, attempt / "verdict.yaml"


@pytest.mark.medium
class TestResolveAbnormalExitSessionCodexStore:
    """Issue #474: Codex session store の一意性規則（#403）を helper 直接で検証する。"""

    def _resolve(self, prompt: Path, verdict: Path) -> SessionResolution:
        return _resolve_abnormal_exit_session(
            "codex",
            prompt_path=prompt,
            verdict_path=verdict,
            resume_session_id="parent-session",
            launch_session_id="",
            pane_alive=True,
        )

    def test_single_marker_match_is_adopted(self, codex_attempt: tuple[Path, Path, Path]) -> None:
        sessions, prompt, verdict = codex_attempt
        _write_rollout(sessions, _UUID_A, f"read {prompt}\n")

        assert self._resolve(prompt, verdict).session_id == _UUID_A
        assert _unique_codex_session_id_from_store(prompt_path=prompt, verdict_path=verdict) == (
            _UUID_A
        )

    def test_two_marker_matches_resolve_to_none(
        self, codex_attempt: tuple[Path, Path, Path]
    ) -> None:
        sessions, prompt, verdict = codex_attempt
        _write_rollout(sessions, _UUID_A, f"read {prompt}\n")
        _write_rollout(sessions, _UUID_B, f"write {verdict}\n")

        assert self._resolve(prompt, verdict).session_id is None

    def test_no_marker_match_resolves_to_none_without_parent_fallback(
        self, codex_attempt: tuple[Path, Path, Path]
    ) -> None:
        sessions, prompt, verdict = codex_attempt
        _write_rollout(sessions, _UUID_A, "unrelated\n")

        assert self._resolve(prompt, verdict).session_id is None

    def test_missing_sessions_dir_resolves_to_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_HOME", str(tmp_path / "absent"))
        prompt = tmp_path / "prompt.txt"
        prompt.write_text("x", encoding="utf-8")

        assert self._resolve(prompt, tmp_path / "verdict.yaml").session_id is None

    def test_rollout_older_than_prompt_is_excluded(
        self, codex_attempt: tuple[Path, Path, Path]
    ) -> None:
        sessions, prompt, verdict = codex_attempt
        prompt_mtime = prompt.stat().st_mtime
        _write_rollout(sessions, _UUID_A, f"read {prompt}\n", mtime=prompt_mtime - 100)

        assert self._resolve(prompt, verdict).session_id is None


@pytest.mark.medium
class TestExtractCodexSessionIdDirect:
    """Issue #474: ``terminal.log`` / session store からの Codex session 抽出を helper 直接で検証する。"""

    def test_last_resume_line_in_terminal_log_wins(
        self, tmp_path: Path, codex_attempt: tuple[Path, Path, Path]
    ) -> None:
        _, prompt, verdict = codex_attempt
        log = tmp_path / "terminal.log"
        log.write_text(f"codex resume {_UUID_A}\nlater\ncodex resume {_UUID_B}\n", encoding="utf-8")

        assert _extract_codex_session_id(log, prompt_path=prompt, verdict_path=verdict) == _UUID_B

    def test_store_is_used_when_log_has_no_resume_line(
        self, tmp_path: Path, codex_attempt: tuple[Path, Path, Path]
    ) -> None:
        sessions, prompt, verdict = codex_attempt
        log = tmp_path / "terminal.log"
        log.write_text("no resume line here\n", encoding="utf-8")
        _write_rollout(sessions, _UUID_A, f"read {prompt}\n")

        assert _extract_codex_session_id(log, prompt_path=prompt, verdict_path=verdict) == _UUID_A

    def test_store_is_used_when_terminal_log_is_absent(
        self, tmp_path: Path, codex_attempt: tuple[Path, Path, Path]
    ) -> None:
        sessions, prompt, verdict = codex_attempt
        _write_rollout(sessions, _UUID_B, f"write {verdict}\n")

        assert (
            _extract_codex_session_id(
                tmp_path / "missing.log", prompt_path=prompt, verdict_path=verdict
            )
            == _UUID_B
        )

    def test_no_markers_and_no_resume_line_returns_none(
        self, tmp_path: Path, codex_attempt: tuple[Path, Path, Path]
    ) -> None:
        assert _extract_codex_session_id(tmp_path / "missing.log") is None


_COMMON_MODULE = (
    Path(__file__).resolve().parent.parent / "kaji_harness" / "interactive_terminal_common.py"
)
_HERDR_MODULE = (
    Path(__file__).resolve().parent.parent / "kaji_harness" / "interactive_terminal_herdr.py"
)
_RECORDER_MODULE = (
    Path(__file__).resolve().parent.parent / "kaji_harness" / "interactive_terminal_recorder.py"
)


def _runtime_imports(path: Path) -> set[str]:
    """``path`` の runtime import 先を絶対 module 名で返す（関数内 import を含み、TYPE_CHECKING guard 内は除く）。"""
    package = "kaji_harness"
    tree = ast.parse(path.read_text(encoding="utf-8"))

    def is_type_checking(test: ast.expr) -> bool:
        return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
        )

    found: set[str] = set()

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.If) and is_type_checking(node.test):
            for child in node.orelse:
                visit(child)
            return
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package if node.module is None else f"{package}.{node.module}"
                found.add(base)
                if node.module is None:
                    found.update(f"{package}.{alias.name}" for alias in node.names)
            elif node.module:
                found.add(node.module)
                found.update(f"{node.module}.{alias.name}" for alias in node.names)
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return found


_HERDR_SHARED_HELPERS = (
    "_build_wrapper_command",
    "_extract_codex_session_id",
    "_resolve_abnormal_exit_session",
    "_terminal_exit_detail",
    "_wrapper_path",
    "_wrapper_skill_invocation",
    "read_terminal_diagnostic",
)


@pytest.mark.small
class TestInteractiveTerminalImportBoundary:
    """Issue #474: Herdr / 共用 helper が tmux 実装に依存しない import 境界を機械検査する。"""

    def test_herdr_does_not_import_dispatch_or_tmux_modules(self) -> None:
        imports = _runtime_imports(_HERDR_MODULE)

        assert "kaji_harness.interactive_terminal" not in imports
        assert "kaji_harness.interactive_terminal_tmux" not in imports
        assert "kaji_harness.interactive_terminal_common" in imports

    def test_common_does_not_import_other_interactive_terminal_modules(self) -> None:
        imports = _runtime_imports(_COMMON_MODULE)

        assert "kaji_harness.interactive_terminal" not in imports
        assert "kaji_harness.interactive_terminal_tmux" not in imports
        assert "kaji_harness.interactive_terminal_herdr" not in imports

    def test_recorder_imports_only_the_standard_library(self) -> None:
        # Issue #515: recorder は ``-I`` で path 起動されるため kaji の module を import できない
        imports = _runtime_imports(_RECORDER_MODULE)

        assert imports
        assert not any(name.startswith("kaji_harness") for name in imports)
        assert all(name.split(".")[0] in sys.stdlib_module_names for name in imports)

    def test_common_does_not_import_subprocess(self) -> None:
        assert "subprocess" not in _runtime_imports(_COMMON_MODULE)

    @pytest.mark.parametrize("name", _HERDR_SHARED_HELPERS)
    def test_herdr_shared_helpers_are_defined_in_common(self, name: str) -> None:
        assert getattr(interactive_terminal_common, name).__module__ == (
            "kaji_harness.interactive_terminal_common"
        )

    def test_import_scanner_detects_deferred_and_relative_imports(self, tmp_path: Path) -> None:
        sample = tmp_path / "sample.py"
        sample.write_text(
            "from typing import TYPE_CHECKING\n"
            "from . import interactive_terminal_tmux\n"
            "if TYPE_CHECKING:\n"
            "    from .interactive_terminal_herdr import X\n"
            "def f():\n"
            "    from .interactive_terminal import execute_interactive_terminal\n",
            encoding="utf-8",
        )

        imports = _runtime_imports(sample)

        assert "kaji_harness.interactive_terminal_tmux" in imports
        assert "kaji_harness.interactive_terminal" in imports
        assert "kaji_harness.interactive_terminal_herdr" not in imports
