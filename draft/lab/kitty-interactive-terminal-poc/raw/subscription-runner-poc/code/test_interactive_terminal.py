"""Tests for the interactive terminal runner PoC."""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from kaji_harness.errors import CLINotFoundError, StepTimeoutError
from kaji_harness.interactive_terminal import execute_interactive_terminal
from kaji_harness.models import Step


@pytest.mark.small
class TestExecuteInteractiveTerminal:
    def test_launches_kitty_and_waits_for_verdict(self, tmp_path: Path) -> None:
        prompt_path = tmp_path / "prompt.txt"
        verdict_path = tmp_path / "verdict.yaml"
        prompt_path.write_text("prompt", encoding="utf-8")
        calls: list[list[str]] = []
        killed_groups: list[tuple[int, int]] = []

        class FakePopen:
            pid = 12345

            def __init__(self, argv: list[str], cwd: Path, start_new_session: bool) -> None:
                calls.append(argv)
                assert cwd == tmp_path
                assert start_new_session is True
                verdict_path.write_text(
                    "status: PASS\nreason: ok\nevidence: e\nsuggestion: ''\n",
                    encoding="utf-8",
                )

            def poll(self) -> None:
                return None

            def wait(self, timeout: int | None = None) -> int:
                return 0

        with (
            patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/kitty"),
            patch(
                "kaji_harness.interactive_terminal.uuid.uuid4",
                return_value=uuid.UUID("11111111-1111-4111-8111-111111111111"),
            ),
            patch.object(subprocess, "Popen", FakePopen),
            patch(
                "kaji_harness.interactive_terminal.os.killpg",
                side_effect=lambda pid, sig: killed_groups.append((pid, sig)),
            ),
        ):
            result = execute_interactive_terminal(
                step=Step(
                    id="design",
                    skill="issue-design",
                    agent="claude",
                    model="haiku",
                    effort="low",
                ),
                prompt_path=prompt_path,
                verdict_path=verdict_path,
                workdir=tmp_path,
                timeout=5,
            )

        assert result.full_output == ""
        assert result.session_id == "11111111-1111-4111-8111-111111111111"
        assert verdict_path.exists()
        assert killed_groups == [(FakePopen.pid, 15)]
        assert calls
        assert calls[0][:2] == ["/usr/bin/kitty", "--hold"]
        assert calls[0][-9:] == [
            "claude",
            str(prompt_path),
            str(verdict_path),
            str(tmp_path / "terminal.log"),
            str(tmp_path),
            "",
            "11111111-1111-4111-8111-111111111111",
            "haiku",
            "low",
        ]

    def test_passes_resume_session_id_to_wrapper(self, tmp_path: Path) -> None:
        prompt_path = tmp_path / "prompt.txt"
        verdict_path = tmp_path / "verdict.yaml"
        prompt_path.write_text("prompt", encoding="utf-8")
        calls: list[list[str]] = []

        class FakePopen:
            pid = 12345

            def __init__(self, argv: list[str], cwd: Path, start_new_session: bool) -> None:
                calls.append(argv)
                verdict_path.write_text(
                    "status: PASS\nreason: ok\nevidence: e\nsuggestion: ''\n",
                    encoding="utf-8",
                )

            def poll(self) -> int:
                return 0

        with (
            patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/kitty"),
            patch.object(subprocess, "Popen", FakePopen),
        ):
            result = execute_interactive_terminal(
                step=Step(id="fix", skill="issue-fix-design", agent="claude"),
                prompt_path=prompt_path,
                verdict_path=verdict_path,
                workdir=tmp_path,
                timeout=5,
                session_id="resume-session",
            )

        assert result.session_id == "resume-session"
        assert calls[0][-4:] == ["resume-session", "", "", ""]

    def test_extracts_codex_session_id_from_terminal_log(self, tmp_path: Path) -> None:
        prompt_path = tmp_path / "prompt.txt"
        verdict_path = tmp_path / "verdict.yaml"
        terminal_log = tmp_path / "terminal.log"
        prompt_path.write_text("prompt", encoding="utf-8")

        class FakePopen:
            pid = 12345

            def __init__(self, argv: list[str], cwd: Path, start_new_session: bool) -> None:
                verdict_path.write_text(
                    "status: PASS\nreason: ok\nevidence: e\nsuggestion: ''\n",
                    encoding="utf-8",
                )
                terminal_log.write_text(
                    "To continue this session, run codex resume "
                    "22222222-2222-4222-8222-222222222222\n",
                    encoding="utf-8",
                )

            def poll(self) -> int:
                return 0

        with (
            patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/kitty"),
            patch.object(subprocess, "Popen", FakePopen),
        ):
            result = execute_interactive_terminal(
                step=Step(id="review", skill="issue-review-design", agent="codex"),
                prompt_path=prompt_path,
                verdict_path=verdict_path,
                workdir=tmp_path,
                timeout=5,
            )

        assert result.session_id == "22222222-2222-4222-8222-222222222222"

    def test_extracts_codex_session_id_from_session_store(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prompt_path = tmp_path / "prompt.txt"
        verdict_path = tmp_path / "verdict.yaml"
        prompt_path.write_text("prompt", encoding="utf-8")
        codex_home = tmp_path / "codex-home"
        sessions_dir = codex_home / "sessions" / "2026" / "06" / "05"
        sessions_dir.mkdir(parents=True)
        session_file = (
            sessions_dir
            / "rollout-2026-06-05T01-11-46-44444444-4444-4444-8444-444444444444.jsonl"
        )
        session_file.write_text(
            f'{{"type":"user","text":"read {prompt_path} and write {verdict_path}"}}\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("CODEX_HOME", str(codex_home))

        class FakePopen:
            pid = 12345

            def __init__(self, argv: list[str], cwd: Path, start_new_session: bool) -> None:
                verdict_path.write_text(
                    "status: PASS\nreason: ok\nevidence: e\nsuggestion: ''\n",
                    encoding="utf-8",
                )

            def poll(self) -> int:
                return 0

        with (
            patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/kitty"),
            patch.object(subprocess, "Popen", FakePopen),
        ):
            result = execute_interactive_terminal(
                step=Step(id="review", skill="issue-review-design", agent="codex"),
                prompt_path=prompt_path,
                verdict_path=verdict_path,
                workdir=tmp_path,
                timeout=5,
            )

        assert result.session_id == "44444444-4444-4444-8444-444444444444"

    def test_can_keep_terminal_open_after_verdict_for_debugging(self, tmp_path: Path) -> None:
        prompt_path = tmp_path / "prompt.txt"
        verdict_path = tmp_path / "verdict.yaml"
        prompt_path.write_text("prompt", encoding="utf-8")
        killed_groups: list[tuple[int, int]] = []

        class FakePopen:
            pid = 12345

            def __init__(self, argv: list[str], cwd: Path, start_new_session: bool) -> None:
                verdict_path.write_text(
                    "status: PASS\nreason: ok\nevidence: e\nsuggestion: ''\n",
                    encoding="utf-8",
                )

            def poll(self) -> None:
                return None

        with (
            patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/kitty"),
            patch.object(subprocess, "Popen", FakePopen),
            patch(
                "kaji_harness.interactive_terminal.os.killpg",
                side_effect=lambda pid, sig: killed_groups.append((pid, sig)),
            ),
        ):
            execute_interactive_terminal(
                step=Step(id="design", skill="issue-design", agent="claude"),
                prompt_path=prompt_path,
                verdict_path=verdict_path,
                workdir=tmp_path,
                timeout=5,
                close_on_verdict=False,
            )

        assert killed_groups == []

    def test_missing_kitty_fails_loud(self, tmp_path: Path) -> None:
        prompt_path = tmp_path / "prompt.txt"
        prompt_path.write_text("prompt", encoding="utf-8")

        with patch("kaji_harness.interactive_terminal.shutil.which", return_value=None):
            with pytest.raises(CLINotFoundError, match="kitty"):
                execute_interactive_terminal(
                    step=Step(id="design", skill="issue-design", agent="claude"),
                    prompt_path=prompt_path,
                    verdict_path=tmp_path / "verdict.yaml",
                    workdir=tmp_path,
                    timeout=5,
                )

    def test_timeout_when_verdict_is_not_written(self, tmp_path: Path) -> None:
        prompt_path = tmp_path / "prompt.txt"
        prompt_path.write_text("prompt", encoding="utf-8")

        class FakePopen:
            def __init__(self, argv: list[str], cwd: Path, start_new_session: bool) -> None:
                pass

        with (
            patch("kaji_harness.interactive_terminal.shutil.which", return_value="/usr/bin/kitty"),
            patch.object(subprocess, "Popen", FakePopen),
            patch("kaji_harness.interactive_terminal.time.sleep", return_value=None),
            patch("kaji_harness.interactive_terminal.time.monotonic", side_effect=[0.0, 1.0]),
        ):
            with pytest.raises(StepTimeoutError):
                execute_interactive_terminal(
                    step=Step(id="design", skill="issue-design", agent="claude"),
                    prompt_path=prompt_path,
                    verdict_path=tmp_path / "verdict.yaml",
                    workdir=tmp_path,
                    timeout=1,
                )


@pytest.mark.medium
class TestInteractiveTerminalWrapper:
    def test_wrapper_records_terminal_transcript_with_script(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        fake_claude = fake_bin / "claude"
        fake_claude.write_text(
            "#!/usr/bin/env bash\n"
            "echo fake-claude-start\n"
            "echo prompt-arg:$1\n"
            "echo status: PASS > \"$FAKE_VERDICT_PATH\"\n"
            "echo reason: ok >> \"$FAKE_VERDICT_PATH\"\n"
            "echo evidence: transcript >> \"$FAKE_VERDICT_PATH\"\n"
            "echo suggestion: '' >> \"$FAKE_VERDICT_PATH\"\n",
            encoding="utf-8",
        )
        fake_claude.chmod(0o755)
        monkeypatch.setenv("PATH", f"{fake_bin}:{__import__('os').environ['PATH']}")

        prompt_path = tmp_path / "prompt.txt"
        verdict_path = tmp_path / "verdict.yaml"
        terminal_log = tmp_path / "terminal.log"
        prompt_path.write_text("prompt", encoding="utf-8")
        monkeypatch.setenv("FAKE_VERDICT_PATH", str(verdict_path))

        wrapper = (
            Path(__file__).resolve().parent.parent
            / "assets"
            / "interactive-terminal"
            / "wrapper.sh"
        )
        result = subprocess.run(
            [
                str(wrapper),
                "claude",
                str(prompt_path),
                str(verdict_path),
                str(terminal_log),
                str(tmp_path),
            ],
            cwd=tmp_path,
            text=True,
            capture_output=True,
            check=False,
        )

        assert result.returncode == 0
        assert verdict_path.exists()
        transcript = terminal_log.read_text(encoding="utf-8", errors="replace")
        assert "fake-claude-start" in transcript
        assert "prompt-arg:" in transcript

    def test_wrapper_uses_resume_command_when_session_id_is_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        fake_codex = fake_bin / "codex"
        args_path = tmp_path / "codex-args.txt"
        fake_codex.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' \"$@\" > \"$ARGS_PATH\"\n",
            encoding="utf-8",
        )
        fake_codex.chmod(0o755)
        monkeypatch.setenv("PATH", f"{fake_bin}:{__import__('os').environ['PATH']}")
        monkeypatch.setenv("ARGS_PATH", str(args_path))

        prompt_path = tmp_path / "prompt.txt"
        verdict_path = tmp_path / "verdict.yaml"
        terminal_log = tmp_path / "terminal.log"
        prompt_path.write_text("prompt", encoding="utf-8")

        wrapper = (
            Path(__file__).resolve().parent.parent
            / "assets"
            / "interactive-terminal"
            / "wrapper.sh"
        )
        result = subprocess.run(
            [
                str(wrapper),
                "codex",
                str(prompt_path),
                str(verdict_path),
                str(terminal_log),
                str(tmp_path),
                "33333333-3333-4333-8333-333333333333",
                "",
                "gpt-5.4-mini",
                "minimal",
            ],
            cwd=tmp_path,
            text=True,
            capture_output=True,
            check=False,
        )

        assert result.returncode == 0
        args = args_path.read_text(encoding="utf-8").splitlines()
        assert args[:4] == [
            "resume",
            "--cd",
            str(tmp_path),
            "--dangerously-bypass-approvals-and-sandbox",
        ]
        assert args[4:9] == [
            "--model",
            "gpt-5.4-mini",
            "--config",
            'model_reasoning_effort="minimal"',
            "33333333-3333-4333-8333-333333333333",
        ]
