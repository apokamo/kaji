"""Issue #408: interactive wrapper の初期メッセージに codex の skill invocation を付ける。

実 bash で ``wrapper.sh`` を起動し、PATH 上の fake agent が受け取った argv を記録する
（subprocess あり・外部ネットワークなしのため ``large_local``）。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.large, pytest.mark.large_local]

WRAPPER = (
    Path(__file__).resolve().parent.parent
    / "kaji_harness"
    / "assets"
    / "interactive-terminal"
    / "wrapper.sh"
)
INVOCATION = "$review を実行してください。"
LEGACY_HEAD = "Read the full task prompt from:"
RESUME_ID = "33333333-3333-4333-8333-333333333333"


def _install_fake_agent(tmp_path: Path, name: str) -> Path:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    agent = fake_bin / name
    agent.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\0" "$@" > "$ARGS_PATH"\n'
        'printf "status: PASS\\nreason: ok\\nevidence: e\\nsuggestion: \x27\x27\\n"'
        ' > "$FAKE_VERDICT_PATH"\n',
        encoding="utf-8",
    )
    agent.chmod(0o755)
    return fake_bin


def _run_wrapper(
    tmp_path: Path, agent_binary: str, wrapper_args: list[str]
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    fake_bin = _install_fake_agent(tmp_path, agent_binary)
    args_path = tmp_path / "args.bin"
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}:{os.environ['PATH']}"
    env["ARGS_PATH"] = str(args_path)
    env["FAKE_VERDICT_PATH"] = str(tmp_path / "verdict.yaml")
    result = subprocess.run(
        [str(WRAPPER), *wrapper_args],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    argv = args_path.read_bytes().decode("utf-8").split("\0")[:-1] if args_path.exists() else []
    return result, argv


def _base_args(tmp_path: Path, agent: str) -> list[str]:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("prompt", encoding="utf-8")
    workdir = tmp_path / "work"
    workdir.mkdir(exist_ok=True)
    return [agent, str(prompt), str(tmp_path / "verdict.yaml"), str(workdir)]


class TestCodexInitialMessage:
    def test_fresh_initial_message_starts_with_invocation(self, tmp_path: Path) -> None:
        wrapper_args = _base_args(tmp_path, "codex") + ["", "", "m", "low", "auto", INVOCATION]

        result, argv = _run_wrapper(tmp_path, "codex", wrapper_args)

        assert result.returncode == 0, result.stderr
        initial = argv[-1]
        assert initial.startswith(f"{INVOCATION}\n\n{LEGACY_HEAD}")

    def test_resume_initial_message_starts_with_invocation(self, tmp_path: Path) -> None:
        wrapper_args = _base_args(tmp_path, "codex") + [
            RESUME_ID,
            "",
            "m",
            "low",
            "auto",
            INVOCATION,
        ]

        result, argv = _run_wrapper(tmp_path, "codex", wrapper_args)

        assert result.returncode == 0, result.stderr
        assert argv[0] == "resume"
        assert argv[-2] == RESUME_ID
        assert argv[-1].startswith(f"{INVOCATION}\n\n{LEGACY_HEAD}")

    def test_empty_tenth_argument_keeps_legacy_message(self, tmp_path: Path) -> None:
        wrapper_args = _base_args(tmp_path, "codex") + ["", "", "m", "low", "auto", ""]

        result, argv = _run_wrapper(tmp_path, "codex", wrapper_args)

        assert result.returncode == 0, result.stderr
        assert argv[-1].startswith(LEGACY_HEAD)


class TestNonCodexInitialMessageUnchanged:
    def test_claude_initial_message_starts_with_legacy_text(self, tmp_path: Path) -> None:
        wrapper_args = _base_args(tmp_path, "claude") + ["", "uuid", "m", "low", "auto", ""]

        result, argv = _run_wrapper(tmp_path, "claude", wrapper_args)

        assert result.returncode == 0, result.stderr
        assert argv[-1].startswith(LEGACY_HEAD)

    def test_antigravity_initial_message_starts_with_legacy_text(self, tmp_path: Path) -> None:
        wrapper_args = _base_args(tmp_path, "antigravity") + ["", "", "m", "low", "auto", ""]

        result, argv = _run_wrapper(tmp_path, "agy", wrapper_args)

        assert result.returncode == 0, result.stderr
        assert argv[-1].startswith(LEGACY_HEAD)
