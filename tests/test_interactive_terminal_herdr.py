"""Tests for the Herdr interactive-terminal backend (Issue #396)."""

from __future__ import annotations

import json
import shlex
import stat
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

from kaji_harness.errors import (
    CLIExecutionError,
    CLINotFoundError,
    HerdrSessionRequiredError,
    SessionResolution,
    StepTimeoutError,
)
from kaji_harness.interactive_terminal_herdr import (
    HerdrManagedPane,
    HerdrPaneLaunch,
    _AttemptDeadlineReachedError,
    _build_herdr_marker_argv,
    _build_herdr_split_argv,
    _build_recorded_command,
    _classify_herdr_process_liveness,
    _close_owned_herdr_pane,
    _get_herdr_process_info,
    _herdr_launcher_started_path,
    _launch_herdr_pane,
    _list_managed_herdr_panes,
    _mark_herdr_pane,
    _materialize_herdr_launcher,
    _parse_herdr_version,
    _preflight_herdr,
    _recorded_exit_detail,
    _recorder_path,
    _resolve_herdr,
    _resolve_herdr_origin,
    _run_herdr,
    _run_herdr_json,
    _run_herdr_pane_command,
    _select_herdr_launch_placement,
    _wait_for_herdr_launcher_start,
    _write_herdr_metadata,
    execute_interactive_terminal_herdr,
)
from kaji_harness.models import Step


@pytest.mark.small
class TestHerdrPreflight:
    """Herdr backend fails before pane operations when prerequisites are absent."""

    def test_requires_installed_herdr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HERDR_BIN_PATH", raising=False)
        with patch("kaji_harness.interactive_terminal_herdr.shutil.which", return_value=None):
            with pytest.raises(CLINotFoundError, match="CLI 'herdr' not found") as exc_info:
                _resolve_herdr()
        assert "tmux" not in str(exc_info.value)
        assert "headless" in str(exc_info.value)

    def test_prefers_executable_herdr_bin_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configured = tmp_path / "herdr-custom"
        configured.write_text("#!/bin/sh\n", encoding="utf-8")
        configured.chmod(0o755)
        monkeypatch.setenv("HERDR_BIN_PATH", str(configured))

        with patch("kaji_harness.interactive_terminal_herdr.shutil.which") as which:
            assert _resolve_herdr() == str(configured)

        which.assert_not_called()

    def test_requires_herdr_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HERDR_ENV", raising=False)
        monkeypatch.setenv("HERDR_PANE_ID", "w1:p1")
        with pytest.raises(HerdrSessionRequiredError, match="inside Herdr") as exc_info:
            _resolve_herdr_origin()
        assert "tmux" not in str(exc_info.value)
        assert "Run kaji inside a Herdr pane" in str(exc_info.value)
        assert 'agent_runner = "headless"' in str(exc_info.value)

    def test_requires_explicit_origin_pane(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HERDR_ENV", "1")
        monkeypatch.delenv("HERDR_PANE_ID", raising=False)
        with pytest.raises(HerdrSessionRequiredError, match="HERDR_PANE_ID") as exc_info:
            _resolve_herdr_origin()
        assert "tmux" not in str(exc_info.value)
        assert 'agent_runner = "headless"' in str(exc_info.value)

    def test_preflight_checks_caller_context_before_binary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("HERDR_ENV", raising=False)
        monkeypatch.delenv("HERDR_PANE_ID", raising=False)
        with (
            patch("kaji_harness.interactive_terminal_herdr._resolve_herdr") as resolve,
            pytest.raises(HerdrSessionRequiredError, match="inside Herdr"),
        ):
            _preflight_herdr()

        resolve.assert_not_called()

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("herdr 0.8.2", (0, 8, 2)),
            ("herdr 1.2.3 stable", (1, 2, 3)),
            ("herdr 0.9.0-beta.1", (0, 9, 0)),
        ],
    )
    def test_parse_version(self, text: str, expected: tuple[int, int, int]) -> None:
        assert _parse_herdr_version(text) == expected

    def test_preflight_checks_status_and_exact_current_pane(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HERDR_ENV", "1")
        monkeypatch.setenv("HERDR_PANE_ID", "w1:p1")
        monkeypatch.delenv("HERDR_BIN_PATH", raising=False)
        responses = [
            subprocess.CompletedProcess(["herdr"], 0, stdout="herdr 0.8.2\n", stderr=""),
            subprocess.CompletedProcess(["herdr"], 0, stdout="server compatible\n", stderr=""),
            subprocess.CompletedProcess(
                ["herdr"],
                0,
                stdout=json.dumps(
                    {
                        "result": {
                            "type": "pane_current",
                            "pane": {"pane_id": "w1:p1"},
                        }
                    }
                ),
                stderr="",
            ),
        ]
        with (
            patch("kaji_harness.interactive_terminal_herdr.shutil.which", return_value="/herdr"),
            patch(
                "kaji_harness.interactive_terminal_herdr.subprocess.run",
                side_effect=responses,
            ) as run,
        ):
            assert _preflight_herdr() == ("/herdr", "w1:p1", "herdr 0.8.2")

        assert [call.args[0] for call in run.call_args_list] == [
            ["/herdr", "--version"],
            ["/herdr", "status"],
            ["/herdr", "pane", "current", "--current"],
        ]


@pytest.mark.small
class TestHerdrCommandContract:
    """Command builders use explicit pane IDs and argv tokens."""

    def test_split_argv_preserves_cwd_focus_and_caller_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PATH", "/experiment/fake-bin:/usr/bin")
        assert _build_herdr_split_argv(
            "/usr/bin/herdr",
            split_target_pane="w1:p4",
            direction="down",
            workdir=tmp_path,
        ) == [
            "/usr/bin/herdr",
            "pane",
            "split",
            "w1:p4",
            "--direction",
            "down",
            "--ratio",
            "0.5",
            "--cwd",
            str(tmp_path),
            "--no-focus",
            "--env",
            "PATH=/experiment/fake-bin:/usr/bin",
        ]

    def test_marker_argv_contains_durable_ownership_tokens(self) -> None:
        argv = _build_herdr_marker_argv(
            "/usr/bin/herdr",
            pane_id="w1:p2",
            origin_pane="w1:p1",
            run_id="run-123",
            step_id="design",
        )
        assert argv == [
            "/usr/bin/herdr",
            "pane",
            "report-metadata",
            "w1:p2",
            "--source",
            "kaji",
            "--token",
            "kaji_origin=w1:p1",
            "--token",
            "kaji_run=run-123",
            "--token",
            "kaji_step=design",
        ]
        assert "--ttl-ms" not in argv

    def test_launcher_moves_long_payload_out_of_pane_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PATH", "/a/very/long/path:" + "x" * 1200)
        launcher_path = tmp_path / "attempt with spaces" / "herdr-launcher.sh"
        wrapper_command = "/wrapper codex '/prompt with spaces' /verdict /work '' '' '' '' auto"

        pane_command = _materialize_herdr_launcher(launcher_path, wrapper_command)

        launcher = launcher_path.read_text(encoding="utf-8")
        assert launcher.startswith("#!/bin/sh\nset -eu\n(umask 077; printf ")
        assert "herdr-launcher-started.tmp" in launcher
        assert "herdr-launcher-started" in launcher
        assert launcher.index("herdr-launcher-started") < launcher.index("exec env ")
        assert "PATH=/a/very/long/path:" in launcher
        assert wrapper_command in launcher
        assert pane_command == shlex.quote(str(launcher_path))
        assert not pane_command.startswith("exec ")
        assert "PATH=" not in pane_command
        assert "/prompt" not in pane_command
        assert len(pane_command) < 200
        assert stat.S_IMODE(launcher_path.stat().st_mode) == 0o700

    def test_launcher_is_published_atomically(self, tmp_path: Path) -> None:
        launcher_path = tmp_path / "herdr-launcher.sh"

        with patch("kaji_harness.interactive_terminal_herdr.os.replace") as replace:
            _materialize_herdr_launcher(launcher_path, "/wrapper codex")

        temporary_path, published_path = replace.call_args.args
        assert Path(temporary_path).name == "herdr-launcher.sh.tmp"
        assert published_path == launcher_path
        assert stat.S_IMODE(Path(temporary_path).stat().st_mode) == 0o700

    def test_launcher_creation_wraps_filesystem_failure(self, tmp_path: Path) -> None:
        launcher_path = tmp_path / "herdr-launcher.sh"

        with (
            patch(
                "kaji_harness.interactive_terminal_herdr.os.open",
                side_effect=OSError("disk unavailable"),
            ),
            pytest.raises(CLIExecutionError, match="Herdr launcher creation failed") as exc_info,
        ):
            _materialize_herdr_launcher(launcher_path, "/wrapper codex")

        assert "disk unavailable" in exc_info.value.stderr
        assert str(launcher_path) in exc_info.value.stderr

    def test_launcher_cleanup_failure_does_not_replace_creation_error(self, tmp_path: Path) -> None:
        launcher_path = tmp_path / "herdr-launcher.sh"

        with (
            patch(
                "kaji_harness.interactive_terminal_herdr.os.replace",
                side_effect=OSError("publish failed"),
            ),
            patch.object(Path, "unlink", side_effect=OSError("cleanup failed")) as unlink,
            pytest.raises(CLIExecutionError, match="Herdr launcher creation failed") as exc_info,
        ):
            _materialize_herdr_launcher(launcher_path, "/wrapper codex")

        # str(exc) is truncated to 200 chars, so inspect stderr / __cause__ instead of the message.
        unlink.assert_called_once()
        assert "publish failed" in exc_info.value.stderr
        assert "cleanup failed" not in exc_info.value.stderr
        assert isinstance(exc_info.value.__cause__, OSError)
        assert str(exc_info.value.__cause__) == "publish failed"

    def test_launcher_start_wait_ignores_shell_only_until_marker(self, tmp_path: Path) -> None:
        started_path = tmp_path / "herdr-launcher-started"
        polls = 0

        def create_marker(*args: object, **kwargs: object) -> None:
            nonlocal polls
            polls += 1
            if polls == 3:
                started_path.write_text("123\n", encoding="utf-8")

        with (
            patch(
                "kaji_harness.interactive_terminal_herdr._get_herdr_process_info",
            ) as process_info,
            patch("kaji_harness.interactive_terminal_herdr.time.monotonic", return_value=0.0),
            patch("kaji_harness.interactive_terminal_herdr.time.sleep", side_effect=create_marker),
        ):
            _wait_for_herdr_launcher_start("/usr/bin/herdr", "w1:p2", started_path)

        assert polls == 3
        process_info.assert_not_called()

    def test_launcher_start_timeout_reports_last_process_state(self, tmp_path: Path) -> None:
        started_path = tmp_path / "herdr-launcher-started"
        shell_only = {"shell_pid": 100, "foreground_processes": [{"pid": 100}]}

        with (
            patch(
                "kaji_harness.interactive_terminal_herdr._get_herdr_process_info",
                return_value=shell_only,
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr.time.monotonic",
                side_effect=[0.0, 1.0, 11.0],
            ),
            patch("kaji_harness.interactive_terminal_herdr.time.sleep"),
            pytest.raises(CLIExecutionError, match="start confirmation timed out") as exc_info,
        ):
            _wait_for_herdr_launcher_start("/usr/bin/herdr", "w1:p2", started_path)

        assert exc_info.value.returncode == 124
        assert "last process state: confirmed_shell_only" in exc_info.value.stderr
        assert "pane w1:p2" in exc_info.value.stderr

    def test_launcher_start_timeout_survives_process_info_failure(self, tmp_path: Path) -> None:
        started_path = tmp_path / "herdr-launcher-started"

        with (
            patch(
                "kaji_harness.interactive_terminal_herdr._get_herdr_process_info",
                side_effect=CLIExecutionError("herdr", 1, "pane disappeared"),
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr.time.monotonic",
                side_effect=[0.0, 1.0, 11.0],
            ),
            patch("kaji_harness.interactive_terminal_herdr.time.sleep"),
            pytest.raises(CLIExecutionError, match="start confirmation timed out") as exc_info,
        ):
            _wait_for_herdr_launcher_start("/usr/bin/herdr", "w1:p2", started_path)

        assert exc_info.value.returncode == 124
        assert "last process state: unavailable (pane disappeared)" in exc_info.value.stderr

    def test_marker_accepts_empty_success_then_confirms_exact_tokens(self) -> None:
        completed = subprocess.CompletedProcess(["herdr"], 0, stdout="", stderr="")
        pane = {
            "pane_id": "w1:p2",
            "tokens": {
                "kaji_origin": "w1:p1",
                "kaji_run": "run-123",
                "kaji_step": "design",
            },
        }
        with (
            patch(
                "kaji_harness.interactive_terminal_herdr.subprocess.run",
                return_value=completed,
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr._get_herdr_pane",
                return_value=pane,
            ) as get_pane,
        ):
            _mark_herdr_pane(
                "/usr/bin/herdr",
                "w1:p2",
                origin_pane="w1:p1",
                run_id="run-123",
                step_id="design",
            )

        get_pane.assert_called_once_with("/usr/bin/herdr", "w1:p2")

    def test_marker_rejects_unconfirmed_tokens_after_empty_success(self) -> None:
        completed = subprocess.CompletedProcess(["herdr"], 0, stdout="", stderr="")
        pane = {
            "pane_id": "w1:p2",
            "tokens": {
                "kaji_origin": "w1:p1",
                "kaji_run": "different-run",
                "kaji_step": "design",
            },
        }
        with (
            patch(
                "kaji_harness.interactive_terminal_herdr.subprocess.run",
                return_value=completed,
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr._get_herdr_pane",
                return_value=pane,
            ),
        ):
            with pytest.raises(CLIExecutionError, match="ownership metadata was not confirmed"):
                _mark_herdr_pane(
                    "/usr/bin/herdr",
                    "w1:p2",
                    origin_pane="w1:p1",
                    run_id="run-123",
                    step_id="design",
                )

    def test_marker_preserves_nonzero_failure_and_skips_confirmation(self) -> None:
        completed = subprocess.CompletedProcess(["herdr"], 7, stdout="", stderr="metadata rejected")
        with (
            patch(
                "kaji_harness.interactive_terminal_herdr.subprocess.run",
                return_value=completed,
            ),
            patch("kaji_harness.interactive_terminal_herdr._get_herdr_pane") as get_pane,
        ):
            with pytest.raises(CLIExecutionError) as exc_info:
                _mark_herdr_pane(
                    "/usr/bin/herdr",
                    "w1:p2",
                    origin_pane="w1:p1",
                    run_id="run-123",
                    step_id="design",
                )

        assert exc_info.value.returncode == 7
        assert get_pane.call_args_list == []

    def test_marker_accepts_typed_ok_then_confirms_exact_tokens(self) -> None:
        response = json.dumps({"result": {"type": "ok"}})
        completed = subprocess.CompletedProcess(["herdr"], 0, stdout=response, stderr="")
        pane = {
            "pane_id": "w1:p2",
            "tokens": {
                "kaji_origin": "w1:p1",
                "kaji_run": "run-123",
                "kaji_step": "design",
            },
        }
        with (
            patch(
                "kaji_harness.interactive_terminal_herdr.subprocess.run",
                return_value=completed,
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr._get_herdr_pane",
                return_value=pane,
            ),
        ):
            _mark_herdr_pane(
                "/usr/bin/herdr",
                "w1:p2",
                origin_pane="w1:p1",
                run_id="run-123",
                step_id="design",
            )

    def test_json_command_rejects_invalid_json(self) -> None:
        completed = subprocess.CompletedProcess(["herdr"], 0, stdout="not-json", stderr="")
        with patch(
            "kaji_harness.interactive_terminal_herdr.subprocess.run", return_value=completed
        ):
            with pytest.raises(CLIExecutionError, match="invalid JSON"):
                _run_herdr_json("/usr/bin/herdr", ["pane", "list"])

    def test_json_command_rejects_nonzero_exit(self) -> None:
        completed = subprocess.CompletedProcess(["herdr"], 7, stdout="", stderr="bad request")
        with patch(
            "kaji_harness.interactive_terminal_herdr.subprocess.run", return_value=completed
        ):
            with pytest.raises(CLIExecutionError) as exc_info:
                _run_herdr_json("/usr/bin/herdr", ["pane", "list"])
        assert exc_info.value.returncode == 7

    def test_command_timeout_is_wrapped_as_cli_execution_error(self) -> None:
        with patch(
            "kaji_harness.interactive_terminal_herdr.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["herdr", "pane", "list"], 10),
        ):
            with pytest.raises(CLIExecutionError, match="timed out"):
                _run_herdr("/usr/bin/herdr", ["pane", "list"])

    def test_json_command_validates_typed_result_envelope(self) -> None:
        completed = subprocess.CompletedProcess(
            ["herdr"], 0, stdout=json.dumps({"result": {"panes": []}}), stderr=""
        )
        with patch(
            "kaji_harness.interactive_terminal_herdr.subprocess.run", return_value=completed
        ):
            with pytest.raises(CLIExecutionError, match="invalid JSON response"):
                _run_herdr_json("/usr/bin/herdr", ["pane", "list"])

    @pytest.mark.parametrize("stdout", ["", json.dumps({"result": {"type": "ok"}})])
    def test_pane_run_accepts_empty_or_typed_ok_success(
        self, tmp_path: Path, stdout: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PATH", "/experiment/fake-bin:/usr/bin")
        completed = subprocess.CompletedProcess(["herdr"], 0, stdout=stdout, stderr="")
        with patch(
            "kaji_harness.interactive_terminal_herdr.subprocess.run", return_value=completed
        ) as run:
            _run_herdr_pane_command(
                "/usr/bin/herdr",
                "w1:p2",
                "kaji run workflow.yaml 396",
                workdir=tmp_path,
            )

        assert run.call_args.args[0] == [
            "/usr/bin/herdr",
            "pane",
            "run",
            "w1:p2",
            "kaji run workflow.yaml 396",
        ]
        assert run.call_args.kwargs["cwd"] == tmp_path

    def test_pane_run_preserves_nonzero_failure(self, tmp_path: Path) -> None:
        completed = subprocess.CompletedProcess(["herdr"], 7, stdout="", stderr="run rejected")
        with patch(
            "kaji_harness.interactive_terminal_herdr.subprocess.run", return_value=completed
        ):
            with pytest.raises(CLIExecutionError) as exc_info:
                _run_herdr_pane_command(
                    "/usr/bin/herdr",
                    "w1:p2",
                    "kaji run workflow.yaml 396",
                    workdir=tmp_path,
                )

        assert exc_info.value.returncode == 7

    @pytest.mark.parametrize(
        "stdout",
        [
            "not-json",
            json.dumps([]),
            json.dumps({"result": {"type": "pane_info"}}),
        ],
    )
    def test_pane_run_rejects_malformed_or_non_ok_response(
        self, tmp_path: Path, stdout: str
    ) -> None:
        completed = subprocess.CompletedProcess(["herdr"], 0, stdout=stdout, stderr="")
        with patch(
            "kaji_harness.interactive_terminal_herdr.subprocess.run", return_value=completed
        ):
            with pytest.raises(CLIExecutionError):
                _run_herdr_pane_command(
                    "/usr/bin/herdr",
                    "w1:p2",
                    "kaji run workflow.yaml 396",
                    workdir=tmp_path,
                )

    @pytest.mark.parametrize("stdout", ["", json.dumps({"result": {"type": "ok"}})])
    def test_close_accepts_success_and_confirms_target_absent(self, stdout: str) -> None:
        owned_pane = {
            "pane_id": "w1:p2",
            "workspace_id": "w1",
            "tokens": {"kaji_origin": "w1:p1", "kaji_run": "run-123"},
        }
        close_completed = subprocess.CompletedProcess(["herdr"], 0, stdout=stdout, stderr="")
        list_completed = subprocess.CompletedProcess(
            ["herdr"],
            0,
            stdout=json.dumps(
                {
                    "result": {
                        "type": "pane_list",
                        "panes": [
                            {"pane_id": "w1:p1"},
                            {"pane_id": "w1:p9"},
                        ],
                    }
                }
            ),
            stderr="",
        )
        with (
            patch(
                "kaji_harness.interactive_terminal_herdr._get_herdr_pane",
                return_value=owned_pane,
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr.subprocess.run",
                side_effect=[close_completed, list_completed],
            ) as run,
        ):
            closed = _close_owned_herdr_pane(
                "/usr/bin/herdr",
                "w1:p2",
                origin_pane="w1:p1",
                run_id="run-123",
            )

        assert closed is True
        assert [call.args[0] for call in run.call_args_list] == [
            ["/usr/bin/herdr", "pane", "close", "w1:p2"],
            ["/usr/bin/herdr", "pane", "list", "--workspace", "w1"],
        ]

    def test_close_preserves_nonzero_failure(self) -> None:
        owned_pane = {
            "pane_id": "w1:p2",
            "workspace_id": "w1",
            "tokens": {"kaji_origin": "w1:p1", "kaji_run": "run-123"},
        }
        completed = subprocess.CompletedProcess(["herdr"], 7, stdout="", stderr="close rejected")
        with (
            patch(
                "kaji_harness.interactive_terminal_herdr._get_herdr_pane",
                return_value=owned_pane,
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr.subprocess.run",
                return_value=completed,
            ),
        ):
            with pytest.raises(CLIExecutionError) as exc_info:
                _close_owned_herdr_pane(
                    "/usr/bin/herdr",
                    "w1:p2",
                    origin_pane="w1:p1",
                    run_id="run-123",
                )

        assert exc_info.value.returncode == 7

    @pytest.mark.parametrize(
        "stdout",
        [
            "not-json",
            json.dumps([]),
            json.dumps({"result": {"type": "pane_info"}}),
        ],
    )
    def test_close_rejects_malformed_or_non_ok_response(self, stdout: str) -> None:
        owned_pane = {
            "pane_id": "w1:p2",
            "workspace_id": "w1",
            "tokens": {"kaji_origin": "w1:p1", "kaji_run": "run-123"},
        }
        completed = subprocess.CompletedProcess(["herdr"], 0, stdout=stdout, stderr="")
        with (
            patch(
                "kaji_harness.interactive_terminal_herdr._get_herdr_pane",
                return_value=owned_pane,
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr.subprocess.run",
                return_value=completed,
            ),
        ):
            with pytest.raises(CLIExecutionError):
                _close_owned_herdr_pane(
                    "/usr/bin/herdr",
                    "w1:p2",
                    origin_pane="w1:p1",
                    run_id="run-123",
                )

    def test_close_rejects_unconfirmed_removal_without_targeting_other_panes(self) -> None:
        owned_pane = {
            "pane_id": "w1:p2",
            "workspace_id": "w1",
            "tokens": {"kaji_origin": "w1:p1", "kaji_run": "run-123"},
        }
        close_completed = subprocess.CompletedProcess(["herdr"], 0, stdout="", stderr="")
        list_completed = subprocess.CompletedProcess(
            ["herdr"],
            0,
            stdout=json.dumps(
                {
                    "result": {
                        "type": "pane_list",
                        "panes": [
                            {"pane_id": "w1:p1"},
                            {"pane_id": "w1:p2"},
                            {"pane_id": "w1:p9"},
                        ],
                    }
                }
            ),
            stderr="",
        )
        with (
            patch(
                "kaji_harness.interactive_terminal_herdr._get_herdr_pane",
                return_value=owned_pane,
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr.subprocess.run",
                side_effect=[close_completed, list_completed],
            ) as run,
        ):
            with pytest.raises(CLIExecutionError, match="close was not confirmed"):
                _close_owned_herdr_pane(
                    "/usr/bin/herdr",
                    "w1:p2",
                    origin_pane="w1:p1",
                    run_id="run-123",
                )

        assert [call.args[0] for call in run.call_args_list] == [
            ["/usr/bin/herdr", "pane", "close", "w1:p2"],
            ["/usr/bin/herdr", "pane", "list", "--workspace", "w1"],
        ]

    def test_first_pane_splits_origin_to_right(self) -> None:
        placement = _select_herdr_launch_placement([])
        assert placement == (None, "right")

    def test_existing_right_column_splits_bottom_pane_down(self) -> None:
        panes = [
            HerdrManagedPane("w1:p2", y=0, run_id="old"),
            HerdrManagedPane("w1:p3", y=20, run_id="new"),
        ]
        placement = _select_herdr_launch_placement(panes[-1:])
        assert placement == ("w1:p3", "down")

    def test_managed_panes_are_scoped_to_origin_workspace_and_right_column(self) -> None:
        pane_list = {
            "result": {
                "type": "pane_list",
                "panes": [
                    {
                        "pane_id": "w1:p2",
                        "tokens": {"kaji_origin": "w1:p1", "kaji_run": "right-run"},
                    },
                    {
                        "pane_id": "w1:p3",
                        "tokens": {"kaji_origin": "w1:p1", "kaji_run": "left-run"},
                    },
                ],
            }
        }
        layout = {
            "result": {
                "type": "pane_layout",
                "layout": {
                    "panes": [
                        {"pane_id": "w1:p1", "rect": {"x": 0, "y": 0}},
                        {"pane_id": "w1:p2", "rect": {"x": 60, "y": 20}},
                        {"pane_id": "w1:p3", "rect": {"x": 0, "y": 30}},
                    ]
                },
            }
        }
        with (
            patch(
                "kaji_harness.interactive_terminal_herdr._get_herdr_pane",
                return_value={"pane_id": "w1:p1", "workspace_id": "w1"},
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr._run_herdr_json",
                side_effect=[pane_list, layout],
            ) as run_json,
        ):
            panes, skipped = _list_managed_herdr_panes("/usr/bin/herdr", "w1:p1")

        assert panes == [HerdrManagedPane("w1:p2", y=20, run_id="right-run")]
        assert skipped == []
        assert run_json.call_args_list[0].args[1] == [
            "pane",
            "list",
            "--workspace",
            "w1",
        ]

    def test_launch_prunes_oldest_owned_pane_and_splits_newest_down(self, tmp_path: Path) -> None:
        panes = [
            HerdrManagedPane("w1:p2", y=0, run_id="old"),
            HerdrManagedPane("w1:p3", y=20, run_id="new"),
        ]
        response = {
            "id": "request-1",
            "result": {"type": "pane_info", "pane": {"pane_id": "w1:p4"}},
        }
        with (
            patch(
                "kaji_harness.interactive_terminal_herdr._list_managed_herdr_panes",
                return_value=(panes, []),
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr._close_owned_herdr_pane",
                return_value=True,
            ) as close,
            patch(
                "kaji_harness.interactive_terminal_herdr._run_herdr_json",
                return_value=response,
            ) as run_json,
        ):
            launch = _launch_herdr_pane("/usr/bin/herdr", "w1:p1", workdir=tmp_path)

        close.assert_called_once_with("/usr/bin/herdr", "w1:p2", origin_pane="w1:p1", run_id="old")
        assert run_json.call_args.args[1][:6] == [
            "pane",
            "split",
            "w1:p3",
            "--direction",
            "down",
            "--ratio",
        ]
        assert launch.panes_before == ["w1:p2", "w1:p3"]
        assert launch.panes_pruned == ["w1:p2"]
        assert launch.panes_skipped == []

    def test_launch_skips_prune_candidate_when_ownership_changes(self, tmp_path: Path) -> None:
        panes = [
            HerdrManagedPane("w1:p2", y=0, run_id="old"),
            HerdrManagedPane("w1:p3", y=20, run_id="new"),
        ]
        response = {
            "result": {"type": "pane_info", "pane": {"pane_id": "w1:p4"}},
        }
        with (
            patch(
                "kaji_harness.interactive_terminal_herdr._list_managed_herdr_panes",
                return_value=(panes, []),
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr._close_owned_herdr_pane",
                return_value=False,
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr._run_herdr_json",
                return_value=response,
            ),
        ):
            launch = _launch_herdr_pane("/usr/bin/herdr", "w1:p1", workdir=tmp_path)

        assert launch.panes_pruned == []
        assert launch.panes_skipped == ["w1:p2"]

    @pytest.mark.parametrize("pane_id", [None, "", "w1:p1"])
    def test_launch_rejects_missing_empty_or_origin_split_pane_id(
        self, tmp_path: Path, pane_id: object
    ) -> None:
        response = {
            "result": {"type": "pane_info", "pane": {"pane_id": pane_id}},
        }
        with (
            patch(
                "kaji_harness.interactive_terminal_herdr._list_managed_herdr_panes",
                return_value=([], []),
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr._run_herdr_json",
                return_value=response,
            ),
            pytest.raises(CLIExecutionError, match="omitted a pane ID|invalid pane ID"),
        ):
            _launch_herdr_pane("/usr/bin/herdr", "w1:p1", workdir=tmp_path)

    @pytest.mark.parametrize("workspace_id", [None, ""])
    def test_managed_pane_listing_rejects_missing_or_empty_workspace(
        self, workspace_id: object
    ) -> None:
        with (
            patch(
                "kaji_harness.interactive_terminal_herdr._get_herdr_pane",
                return_value={"pane_id": "w1:p1", "workspace_id": workspace_id},
            ),
            patch("kaji_harness.interactive_terminal_herdr._run_herdr_json") as run_json,
            pytest.raises(CLIExecutionError, match="omitted its workspace ID"),
        ):
            _list_managed_herdr_panes("/usr/bin/herdr", "w1:p1")

        run_json.assert_not_called()

    @pytest.mark.parametrize("pane_id", [None, ""])
    def test_managed_pane_listing_ignores_missing_or_empty_pane_id(self, pane_id: object) -> None:
        pane_list = {
            "result": {
                "type": "pane_list",
                "panes": [
                    {
                        "pane_id": pane_id,
                        "tokens": {"kaji_origin": "w1:p1", "kaji_run": "run-1"},
                    }
                ],
            }
        }
        layout = {
            "result": {
                "type": "pane_layout",
                "layout": {
                    "panes": [
                        {"pane_id": "w1:p1", "rect": {"x": 0, "y": 0}},
                    ]
                },
            }
        }
        with (
            patch(
                "kaji_harness.interactive_terminal_herdr._get_herdr_pane",
                return_value={"pane_id": "w1:p1", "workspace_id": "w1"},
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr._run_herdr_json",
                side_effect=[pane_list, layout],
            ),
        ):
            panes, skipped = _list_managed_herdr_panes("/usr/bin/herdr", "w1:p1")

        assert panes == []
        assert skipped == []

    @pytest.mark.parametrize("run_token", [None, ""])
    def test_managed_pane_listing_skips_missing_or_empty_run_token(self, run_token: object) -> None:
        pane_list = {
            "result": {
                "type": "pane_list",
                "panes": [
                    {
                        "pane_id": "w1:p2",
                        "tokens": {"kaji_origin": "w1:p1", "kaji_run": run_token},
                    }
                ],
            }
        }
        layout = {
            "result": {
                "type": "pane_layout",
                "layout": {
                    "panes": [
                        {"pane_id": "w1:p1", "rect": {"x": 0, "y": 0}},
                        {"pane_id": "w1:p2", "rect": {"x": 60, "y": 20}},
                    ]
                },
            }
        }
        with (
            patch(
                "kaji_harness.interactive_terminal_herdr._get_herdr_pane",
                return_value={"pane_id": "w1:p1", "workspace_id": "w1"},
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr._run_herdr_json",
                side_effect=[pane_list, layout],
            ),
        ):
            panes, skipped = _list_managed_herdr_panes("/usr/bin/herdr", "w1:p1")

        assert panes == []
        assert skipped == ["w1:p2"]

    def test_managed_pane_listing_skips_owned_pane_missing_from_layout(self) -> None:
        pane_list = {
            "result": {
                "type": "pane_list",
                "panes": [
                    {
                        "pane_id": "w1:p2",
                        "tokens": {"kaji_origin": "w1:p1", "kaji_run": "run-1"},
                    }
                ],
            }
        }
        layout = {
            "result": {
                "type": "pane_layout",
                "layout": {
                    "panes": [
                        {"pane_id": "w1:p1", "rect": {"x": 0, "y": 0}},
                    ]
                },
            }
        }
        with (
            patch(
                "kaji_harness.interactive_terminal_herdr._get_herdr_pane",
                return_value={"pane_id": "w1:p1", "workspace_id": "w1"},
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr._run_herdr_json",
                side_effect=[pane_list, layout],
            ),
        ):
            panes, skipped = _list_managed_herdr_panes("/usr/bin/herdr", "w1:p1")

        assert panes == []
        assert skipped == ["w1:p2"]

    @pytest.mark.parametrize("panes", [None, {}])
    def test_managed_pane_listing_rejects_malformed_pane_list(self, panes: object) -> None:
        pane_list = {"result": {"type": "pane_list", "panes": panes}}
        with (
            patch(
                "kaji_harness.interactive_terminal_herdr._get_herdr_pane",
                return_value={"pane_id": "w1:p1", "workspace_id": "w1"},
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr._run_herdr_json",
                return_value=pane_list,
            ),
            pytest.raises(CLIExecutionError, match="pane_list omitted panes"),
        ):
            _list_managed_herdr_panes("/usr/bin/herdr", "w1:p1")

    @pytest.mark.parametrize("layout", [None, {}, {"panes": None}])
    def test_managed_pane_listing_rejects_malformed_layout(self, layout: object) -> None:
        pane_list = {"result": {"type": "pane_list", "panes": []}}
        pane_layout = {"result": {"type": "pane_layout", "layout": layout}}
        with (
            patch(
                "kaji_harness.interactive_terminal_herdr._get_herdr_pane",
                return_value={"pane_id": "w1:p1", "workspace_id": "w1"},
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr._run_herdr_json",
                side_effect=[pane_list, pane_layout],
            ),
            pytest.raises(CLIExecutionError, match="pane_layout omitted panes"),
        ):
            _list_managed_herdr_panes("/usr/bin/herdr", "w1:p1")

    @pytest.mark.parametrize(
        ("pane_id", "origin_pane", "run_id"),
        [("w1:p2", "w1:p1", ""), ("w1:p2", "", "run-1"), ("", "w1:p1", "run-1")],
    )
    def test_close_refuses_empty_ownership_authority(
        self, pane_id: str, origin_pane: str, run_id: str
    ) -> None:
        with patch("kaji_harness.interactive_terminal_herdr._get_herdr_pane") as get_pane:
            closed = _close_owned_herdr_pane(
                "/usr/bin/herdr",
                pane_id,
                origin_pane=origin_pane,
                run_id=run_id,
            )

        assert closed is False
        get_pane.assert_not_called()

    def test_close_refuses_changed_ownership(self) -> None:
        pane = {
            "pane_id": "w1:p2",
            "tokens": {"kaji_origin": "w1:p1", "kaji_run": "different-run"},
        }
        with (
            patch(
                "kaji_harness.interactive_terminal_herdr._get_herdr_pane",
                return_value=pane,
            ),
            patch("kaji_harness.interactive_terminal_herdr._run_herdr_json") as run_json,
        ):
            closed = _close_owned_herdr_pane(
                "/usr/bin/herdr",
                "w1:p2",
                origin_pane="w1:p1",
                run_id="expected-run",
            )

        assert closed is False
        run_json.assert_not_called()

    @pytest.mark.parametrize(
        ("process_info", "expected"),
        [
            (
                {
                    "shell_pid": 100,
                    "foreground_processes": [{"pid": 100, "name": "bash"}],
                },
                "confirmed_shell_only",
            ),
            (
                {
                    "shell_pid": 100,
                    "foreground_processes": [{"pid": 200, "name": "claude"}],
                },
                "active",
            ),
            ({}, "unknown"),
            ({"shell_pid": None, "foreground_processes": []}, "unknown"),
            ({"shell_pid": 100, "foreground_processes": None}, "unknown"),
            ({"shell_pid": 100, "foreground_processes": []}, "unknown"),
            ({"shell_pid": 100, "foreground_processes": [{}]}, "unknown"),
            ({"shell_pid": 100, "foreground_processes": [{"pid": "100"}]}, "unknown"),
        ],
    )
    def test_process_info_liveness_classification(
        self, process_info: dict[str, object], expected: str
    ) -> None:
        assert _classify_herdr_process_liveness(process_info) == expected

    @pytest.mark.parametrize("process_info", [None, "invalid", []])
    def test_process_info_rejects_missing_or_malformed_container(
        self, process_info: object
    ) -> None:
        response = {
            "result": {
                "type": "pane_process_info",
                "process_info": process_info,
            }
        }
        with patch(
            "kaji_harness.interactive_terminal_herdr._run_herdr_json",
            return_value=response,
        ):
            with pytest.raises(CLIExecutionError, match="omitted process_info"):
                _get_herdr_process_info("/usr/bin/herdr", "w1:p2")


@pytest.mark.medium
class TestExecuteHerdr:
    """The high-level lifecycle remains artifact-driven and ownership-safe."""

    @pytest.fixture(autouse=True)
    def launcher_started(self) -> Iterator[None]:
        """Keep lifecycle tests focused after bounded launcher confirmation."""
        with patch("kaji_harness.interactive_terminal_herdr._wait_for_herdr_launcher_start"):
            yield

    @pytest.mark.parametrize(
        ("agent", "expected"),
        [("codex", "'$design を実行してください。'"), ("claude", "''")],
    )
    def test_wrapper_command_carries_skill_invocation_for_codex_only(
        self, tmp_path: Path, agent: str, expected: str
    ) -> None:
        """Issue #408: wrapper 第 10 引数は codex のときだけ ``$<skill>`` 行になる。"""
        prompt_path = tmp_path / "prompt.txt"
        verdict_path = tmp_path / "verdict.yaml"
        prompt_path.write_text("do work", encoding="utf-8")
        step = Step(id="design", skill="design", agent=agent)

        def run_command(*args: object, **kwargs: object) -> None:
            verdict_path.write_text("status: PASS\nreason: ok\nevidence: ok\n", encoding="utf-8")

        with (
            patch(
                "kaji_harness.interactive_terminal_herdr._preflight_herdr",
                return_value=("/usr/bin/herdr", "w1:p1", "herdr 0.8.2"),
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr._launch_herdr_pane",
                return_value=HerdrPaneLaunch(
                    pane_id="w1:p2",
                    split_target_pane="w1:p1",
                    direction="right",
                    panes_before=[],
                    panes_pruned=[],
                ),
            ),
            patch("kaji_harness.interactive_terminal_herdr._mark_herdr_pane"),
            patch(
                "kaji_harness.interactive_terminal_herdr._run_herdr_pane_command",
                side_effect=run_command,
            ),
            patch("kaji_harness.interactive_terminal_herdr._get_herdr_process_info"),
            patch(
                "kaji_harness.interactive_terminal_herdr._close_owned_herdr_pane",
                return_value=True,
            ),
        ):
            execute_interactive_terminal_herdr(
                step=step,
                prompt_path=prompt_path,
                verdict_path=verdict_path,
                workdir=tmp_path,
                timeout=30,
            )

        launcher = (tmp_path / "herdr-launcher.sh").read_text(encoding="utf-8")
        assert launcher.count(expected) >= 1
        if agent == "claude":
            assert "を実行してください。" not in launcher

    def test_verdict_recording_metadata_and_owned_cleanup(self, tmp_path: Path) -> None:
        prompt_path = tmp_path / "prompt.txt"
        verdict_path = tmp_path / "verdict.yaml"
        prompt_path.write_text("do work", encoding="utf-8")
        step = Step(id="design", skill="design", agent="claude")

        def run_command(*args: object, **kwargs: object) -> None:
            verdict_path.write_text("status: PASS\nreason: ok\nevidence: ok\n", encoding="utf-8")

        terminal_log = tmp_path / "terminal.log"
        terminal_log.write_bytes(b"\x1b[?1049hinteractive screen\r\n")
        (tmp_path / "terminal.log.state").write_bytes(b"complete".ljust(31) + b"\n")
        with (
            patch(
                "kaji_harness.interactive_terminal_herdr._preflight_herdr",
                return_value=("/usr/bin/herdr", "w1:p1", "herdr 0.8.2"),
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr._launch_herdr_pane",
                return_value=HerdrPaneLaunch(
                    pane_id="w1:p2",
                    split_target_pane="w1:p1",
                    direction="right",
                    panes_before=[],
                    panes_pruned=[],
                ),
            ) as launch,
            patch("kaji_harness.interactive_terminal_herdr._mark_herdr_pane") as mark,
            patch(
                "kaji_harness.interactive_terminal_herdr._run_herdr_pane_command",
                side_effect=run_command,
            ) as run,
            patch("kaji_harness.interactive_terminal_herdr._run_herdr") as run_herdr,
            patch("kaji_harness.interactive_terminal_herdr._get_herdr_process_info"),
            patch(
                "kaji_harness.interactive_terminal_herdr._close_owned_herdr_pane",
                return_value=True,
            ) as close,
            patch(
                "kaji_harness.interactive_terminal_herdr.uuid.uuid4",
                return_value="11111111-1111-4111-8111-111111111111",
            ),
        ):
            result = execute_interactive_terminal_herdr(
                step=step,
                prompt_path=prompt_path,
                verdict_path=verdict_path,
                workdir=tmp_path,
                timeout=30,
            )

        launch.assert_called_once()
        mark.assert_called_once_with(
            "/usr/bin/herdr",
            "w1:p2",
            origin_pane="w1:p1",
            run_id="11111111-1111-4111-8111-111111111111",
            step_id="design",
        )
        run.assert_called_once()
        pane_command = run.call_args.args[2]
        launcher_path = tmp_path / "herdr-launcher.sh"
        assert pane_command == str(launcher_path)
        assert launcher_path.read_text(encoding="utf-8").startswith(
            "#!/bin/sh\nset -eu\n(umask 077; printf "
        )
        close.assert_called_once_with(
            "/usr/bin/herdr",
            "w1:p2",
            origin_pane="w1:p1",
            run_id="11111111-1111-4111-8111-111111111111",
        )
        assert result.session_id == "11111111-1111-4111-8111-111111111111"
        assert terminal_log.read_bytes() == b"\x1b[?1049hinteractive screen\r\n"
        metadata = json.loads((tmp_path / "pane-metadata.json").read_text(encoding="utf-8"))
        assert metadata["backend"] == "herdr"
        assert metadata["transcript_kind"] == "raw_pty_recording"
        assert metadata["transcript_available"] is True
        assert metadata["transcript_recorder_state"] == "complete"
        assert metadata["transcript_truncated"] is False
        assert metadata["transcript_revision"] is None
        assert metadata["transcript_bytes"] == len(terminal_log.read_bytes())
        assert "terminal_diagnostic" not in metadata
        assert all(call.args[1][:2] != ["pane", "read"] for call in run_herdr.call_args_list)

    def test_launcher_failure_never_dispatches_and_closes_owned_pane(self, tmp_path: Path) -> None:
        prompt_path = tmp_path / "prompt.txt"
        prompt_path.write_text("do work", encoding="utf-8")
        step = Step(id="design", skill="design", agent="codex")
        launcher_error = CLIExecutionError(
            "interactive_terminal", 1, "Herdr launcher creation failed"
        )

        with (
            patch(
                "kaji_harness.interactive_terminal_herdr._preflight_herdr",
                return_value=("/usr/bin/herdr", "w1:p1", "herdr 0.8.2"),
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr._launch_herdr_pane",
                return_value=HerdrPaneLaunch(
                    pane_id="w1:p2",
                    split_target_pane="w1:p1",
                    direction="right",
                    panes_before=[],
                    panes_pruned=[],
                ),
            ),
            patch("kaji_harness.interactive_terminal_herdr._mark_herdr_pane"),
            patch(
                "kaji_harness.interactive_terminal_herdr._materialize_herdr_launcher",
                side_effect=launcher_error,
            ),
            patch("kaji_harness.interactive_terminal_herdr._run_herdr_pane_command") as run_wrapper,
            patch(
                "kaji_harness.interactive_terminal_herdr._close_owned_herdr_pane",
                return_value=True,
            ) as close,
            patch(
                "kaji_harness.interactive_terminal_herdr.uuid.uuid4",
                return_value="run-123",
            ),
            pytest.raises(CLIExecutionError, match="launcher creation failed") as exc_info,
        ):
            execute_interactive_terminal_herdr(
                step=step,
                prompt_path=prompt_path,
                verdict_path=tmp_path / "verdict.yaml",
                workdir=tmp_path,
                timeout=30,
            )

        assert exc_info.value is launcher_error
        run_wrapper.assert_not_called()
        close.assert_called_once_with(
            "/usr/bin/herdr",
            "w1:p2",
            origin_pane="w1:p1",
            run_id="run-123",
        )

    def test_launcher_start_timeout_cleans_up_owned_pane(self, tmp_path: Path) -> None:
        prompt_path = tmp_path / "prompt.txt"
        prompt_path.write_text("do work", encoding="utf-8")
        step = Step(id="design", skill="design", agent="codex")
        start_error = CLIExecutionError(
            "interactive_terminal", 124, "Herdr launcher start confirmation timed out"
        )

        with (
            patch(
                "kaji_harness.interactive_terminal_herdr._preflight_herdr",
                return_value=("/usr/bin/herdr", "w1:p1", "herdr 0.8.2"),
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr._launch_herdr_pane",
                return_value=HerdrPaneLaunch(
                    pane_id="w1:p2",
                    split_target_pane="w1:p1",
                    direction="right",
                    panes_before=[],
                    panes_pruned=[],
                ),
            ),
            patch("kaji_harness.interactive_terminal_herdr._mark_herdr_pane"),
            patch("kaji_harness.interactive_terminal_herdr._run_herdr_pane_command") as dispatch,
            patch(
                "kaji_harness.interactive_terminal_herdr._wait_for_herdr_launcher_start",
                side_effect=start_error,
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr._close_owned_herdr_pane",
                return_value=True,
            ) as close,
            patch(
                "kaji_harness.interactive_terminal_herdr.uuid.uuid4",
                return_value="run-123",
            ),
            pytest.raises(CLIExecutionError, match="start confirmation timed out") as exc_info,
        ):
            execute_interactive_terminal_herdr(
                step=step,
                prompt_path=prompt_path,
                verdict_path=tmp_path / "verdict.yaml",
                workdir=tmp_path,
                timeout=30,
            )

        assert exc_info.value is start_error
        dispatch.assert_called_once()
        close.assert_called_once_with(
            "/usr/bin/herdr",
            "w1:p2",
            origin_pane="w1:p1",
            run_id="run-123",
        )

    def test_verdict_result_survives_cleanup_failure(self, tmp_path: Path) -> None:
        prompt_path = tmp_path / "prompt.txt"
        verdict_path = tmp_path / "verdict.yaml"
        prompt_path.write_text("do work", encoding="utf-8")
        verdict_path.write_text("status: PASS\nreason: ok\nevidence: ok\n", encoding="utf-8")
        step = Step(id="design", skill="design", agent="claude")
        cleanup_error = CLIExecutionError(
            "interactive_terminal", 1, "Herdr pane close was not confirmed: w1:p2"
        )

        with (
            patch(
                "kaji_harness.interactive_terminal_herdr._preflight_herdr",
                return_value=("/usr/bin/herdr", "w1:p1", "herdr 0.8.2"),
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr._launch_herdr_pane",
                return_value=HerdrPaneLaunch(
                    pane_id="w1:p2",
                    split_target_pane="w1:p1",
                    direction="right",
                    panes_before=[],
                    panes_pruned=[],
                ),
            ),
            patch("kaji_harness.interactive_terminal_herdr._mark_herdr_pane"),
            patch("kaji_harness.interactive_terminal_herdr._run_herdr_pane_command"),
            patch(
                "kaji_harness.interactive_terminal_herdr._close_owned_herdr_pane",
                side_effect=cleanup_error,
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr.uuid.uuid4",
                side_effect=["run-id", "launch-session-id"],
            ),
        ):
            result = execute_interactive_terminal_herdr(
                step=step,
                prompt_path=prompt_path,
                verdict_path=verdict_path,
                workdir=tmp_path,
                timeout=30,
            )

        assert result.session_id == "launch-session-id"
        metadata = json.loads((tmp_path / "pane-metadata.json").read_text(encoding="utf-8"))
        assert "close was not confirmed" in metadata["close_error"]

    def test_pane_run_failure_survives_cleanup_failure(self, tmp_path: Path) -> None:
        prompt_path = tmp_path / "prompt.txt"
        prompt_path.write_text("do work", encoding="utf-8")
        step = Step(id="design", skill="design", agent="codex")
        run_error = CLIExecutionError("interactive_terminal", 7, "pane run rejected")
        cleanup_error = CLIExecutionError("interactive_terminal", 8, "pane close rejected")

        with (
            patch(
                "kaji_harness.interactive_terminal_herdr._preflight_herdr",
                return_value=("/usr/bin/herdr", "w1:p1", "herdr 0.8.2"),
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr._launch_herdr_pane",
                return_value=HerdrPaneLaunch(
                    pane_id="w1:p2",
                    split_target_pane="w1:p1",
                    direction="right",
                    panes_before=[],
                    panes_pruned=[],
                ),
            ),
            patch("kaji_harness.interactive_terminal_herdr._mark_herdr_pane"),
            patch(
                "kaji_harness.interactive_terminal_herdr._run_herdr_pane_command",
                side_effect=run_error,
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr._close_owned_herdr_pane",
                side_effect=cleanup_error,
            ),
            pytest.raises(CLIExecutionError, match="pane run rejected") as exc_info,
        ):
            execute_interactive_terminal_herdr(
                step=step,
                prompt_path=prompt_path,
                verdict_path=tmp_path / "verdict.yaml",
                workdir=tmp_path,
                timeout=30,
            )

        assert exc_info.value.returncode == 7
        metadata = json.loads((tmp_path / "pane-metadata.json").read_text(encoding="utf-8"))
        assert "pane close rejected" in metadata["close_error"]

    def test_marker_failure_does_not_close_unowned_pane(self, tmp_path: Path) -> None:
        prompt_path = tmp_path / "prompt.txt"
        prompt_path.write_text("do work", encoding="utf-8")
        step = Step(id="design", skill="design", agent="codex")
        error = CLIExecutionError("interactive_terminal", 1, "metadata rejected")

        with (
            patch(
                "kaji_harness.interactive_terminal_herdr._preflight_herdr",
                return_value=("/usr/bin/herdr", "w1:p1", "herdr 0.8.2"),
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr._launch_herdr_pane",
                return_value=HerdrPaneLaunch(
                    pane_id="w1:p2",
                    split_target_pane="w1:p1",
                    direction="right",
                    panes_before=[],
                    panes_pruned=[],
                ),
            ),
            patch("kaji_harness.interactive_terminal_herdr._mark_herdr_pane", side_effect=error),
            patch("kaji_harness.interactive_terminal_herdr._close_owned_herdr_pane") as close,
        ):
            with pytest.raises(CLIExecutionError, match="metadata rejected"):
                execute_interactive_terminal_herdr(
                    step=step,
                    prompt_path=prompt_path,
                    verdict_path=tmp_path / "verdict.yaml",
                    workdir=tmp_path,
                    timeout=30,
                )

        assert close.call_args_list == []

    def test_shell_return_before_verdict_fails_early_and_closes_owned_pane(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prompt_path = tmp_path / "prompt.txt"
        prompt_path.write_text("do work", encoding="utf-8")
        monkeypatch.setenv("CODEX_HOME", str(tmp_path / "empty-codex-home"))
        step = Step(id="design", skill="design", agent="codex")
        launch = HerdrPaneLaunch(
            pane_id="w1:p2",
            split_target_pane="w1:p1",
            direction="right",
            panes_before=[],
            panes_pruned=[],
        )
        shell_only = {
            "shell_pid": 100,
            "foreground_processes": [{"pid": 100, "name": "bash"}],
        }

        with (
            patch(
                "kaji_harness.interactive_terminal_herdr._preflight_herdr",
                return_value=("/usr/bin/herdr", "w1:p1", "herdr 0.8.2"),
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr._launch_herdr_pane",
                return_value=launch,
            ),
            patch("kaji_harness.interactive_terminal_herdr._mark_herdr_pane"),
            patch("kaji_harness.interactive_terminal_herdr._run_herdr_pane_command"),
            patch(
                "kaji_harness.interactive_terminal_herdr._get_herdr_process_info",
                return_value=shell_only,
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr._close_owned_herdr_pane",
                side_effect=CLIExecutionError(
                    "interactive_terminal", 1, "Herdr pane close was not confirmed: w1:p2"
                ),
            ) as close,
            patch(
                "kaji_harness.interactive_terminal_herdr.time.monotonic",
                side_effect=[0.0, 1.0, 2.0, 3.0],
            ),
            patch("kaji_harness.interactive_terminal_herdr.time.sleep"),
        ):
            with pytest.raises(CLIExecutionError, match="returned to its shell") as exc_info:
                execute_interactive_terminal_herdr(
                    step=step,
                    prompt_path=prompt_path,
                    verdict_path=tmp_path / "verdict.yaml",
                    workdir=tmp_path,
                    timeout=30,
                )

        close.assert_called_once()
        assert exc_info.value.session_resolution == SessionResolution(None)
        assert "rendered snapshot" not in exc_info.value.stderr
        assert "transcript may be incomplete (recorder state: unknown)" in exc_info.value.stderr
        metadata = json.loads((tmp_path / "pane-metadata.json").read_text(encoding="utf-8"))
        assert metadata["terminal_diagnostic"]["kind"] == "no_log"
        assert metadata["transcript_available"] is False
        assert metadata["transcript_recorder_state"] == "unknown"
        assert "transcript_truncated" not in metadata
        assert "close was not confirmed" in metadata["close_error"]

    def test_timeout_captures_metadata_and_closes_owned_pane(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prompt_path = tmp_path / "prompt.txt"
        prompt_path.write_text("do work", encoding="utf-8")
        monkeypatch.setenv("CODEX_HOME", str(tmp_path / "empty-codex-home"))
        step = Step(id="design", skill="design", agent="codex")
        launch = HerdrPaneLaunch(
            pane_id="w1:p2",
            split_target_pane="w1:p1",
            direction="right",
            panes_before=[],
            panes_pruned=[],
        )
        (tmp_path / "terminal.log").write_bytes(b"timed out screen\r\n")
        (tmp_path / "terminal.log.state").write_bytes(b"incomplete ENOSPC".ljust(31) + b"\n")

        with (
            patch(
                "kaji_harness.interactive_terminal_herdr._preflight_herdr",
                return_value=("/usr/bin/herdr", "w1:p1", "herdr 0.8.2"),
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr._launch_herdr_pane",
                return_value=launch,
            ),
            patch("kaji_harness.interactive_terminal_herdr._mark_herdr_pane"),
            patch("kaji_harness.interactive_terminal_herdr._run_herdr_pane_command"),
            patch(
                "kaji_harness.interactive_terminal_herdr._close_owned_herdr_pane",
                side_effect=CLIExecutionError(
                    "interactive_terminal", 124, "Herdr command timed out: pane close"
                ),
            ) as close,
            patch(
                "kaji_harness.interactive_terminal_herdr.time.monotonic",
                side_effect=[0.0, 1.0],
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr.uuid.uuid4",
                return_value="22222222-2222-4222-8222-222222222222",
            ),
        ):
            with pytest.raises(StepTimeoutError, match="design") as exc_info:
                execute_interactive_terminal_herdr(
                    step=step,
                    prompt_path=prompt_path,
                    verdict_path=tmp_path / "verdict.yaml",
                    workdir=tmp_path,
                    timeout=1,
                )

        close.assert_called_once_with(
            "/usr/bin/herdr",
            "w1:p2",
            origin_pane="w1:p1",
            run_id="22222222-2222-4222-8222-222222222222",
        )
        metadata = json.loads((tmp_path / "pane-metadata.json").read_text(encoding="utf-8"))
        assert metadata["close_on_verdict"] is True
        assert metadata["transcript_revision"] is None
        assert metadata["transcript_truncated"] is True
        assert metadata["transcript_recorder_state"] == "incomplete"
        assert metadata["transcript_error"] == "ENOSPC"
        assert metadata["transcript_bytes"] == len(b"timed out screen\r\n")
        assert metadata["terminal_diagnostic"]["kind"] == "no_pattern"
        assert "pane close" in metadata["close_error"]
        assert exc_info.value.session_resolution == SessionResolution(None)

    def test_fresh_claude_timeout_carries_launch_session_resolution(self, tmp_path: Path) -> None:
        prompt_path = tmp_path / "prompt.txt"
        prompt_path.write_text("do work", encoding="utf-8")
        step = Step(id="design", skill="design", agent="claude")
        launch = HerdrPaneLaunch(
            pane_id="w1:p2",
            split_target_pane="w1:p1",
            direction="right",
            panes_before=[],
            panes_pruned=[],
        )

        with (
            patch(
                "kaji_harness.interactive_terminal_herdr._preflight_herdr",
                return_value=("/usr/bin/herdr", "w1:p1", "herdr 0.8.2"),
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr._launch_herdr_pane",
                return_value=launch,
            ),
            patch("kaji_harness.interactive_terminal_herdr._mark_herdr_pane"),
            patch("kaji_harness.interactive_terminal_herdr._run_herdr_pane_command"),
            patch(
                "kaji_harness.interactive_terminal_herdr._close_owned_herdr_pane",
                return_value=True,
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr.time.monotonic",
                side_effect=[0.0, 1.0],
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr.uuid.uuid4",
                side_effect=["run-id", "launch-session-id"],
            ),
            pytest.raises(StepTimeoutError) as exc_info,
        ):
            execute_interactive_terminal_herdr(
                step=step,
                prompt_path=prompt_path,
                verdict_path=tmp_path / "verdict.yaml",
                workdir=tmp_path,
                timeout=1,
            )

        assert exc_info.value.session_resolution == SessionResolution("launch-session-id")

    @pytest.mark.parametrize("metadata_error", [None, OSError("metadata failed")])
    def test_interrupt_preserves_pane_and_original_exception(
        self, tmp_path: Path, metadata_error: OSError | None
    ) -> None:
        prompt_path = tmp_path / "prompt.txt"
        prompt_path.write_text("do work", encoding="utf-8")
        step = Step(id="design", skill="design", agent="codex")
        launch = HerdrPaneLaunch(
            pane_id="w1:p2",
            split_target_pane="w1:p1",
            direction="right",
            panes_before=[],
            panes_pruned=[],
        )

        with (
            patch(
                "kaji_harness.interactive_terminal_herdr._preflight_herdr",
                return_value=("/usr/bin/herdr", "w1:p1", "herdr 0.8.2"),
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr._launch_herdr_pane",
                return_value=launch,
            ),
            patch("kaji_harness.interactive_terminal_herdr._mark_herdr_pane"),
            patch("kaji_harness.interactive_terminal_herdr._run_herdr_pane_command"),
            patch(
                "kaji_harness.interactive_terminal_herdr._get_herdr_process_info",
                return_value={
                    "shell_pid": 100,
                    "foreground_processes": [{"pid": 200, "name": "codex"}],
                },
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr._write_herdr_metadata",
                side_effect=metadata_error,
            ) as write_metadata,
            patch("kaji_harness.interactive_terminal_herdr._close_owned_herdr_pane") as close,
            patch(
                "kaji_harness.interactive_terminal_herdr.time.monotonic",
                side_effect=[0.0, 1.0],
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr.time.sleep",
                side_effect=KeyboardInterrupt(),
            ),
            pytest.raises(KeyboardInterrupt),
        ):
            execute_interactive_terminal_herdr(
                step=step,
                prompt_path=prompt_path,
                verdict_path=tmp_path / "verdict.yaml",
                workdir=tmp_path,
                timeout=30,
            )

        close.assert_not_called()
        write_metadata.assert_called_once()
        assert write_metadata.call_args.kwargs["terminal_log"] == tmp_path / "terminal.log"

    def test_unknown_process_info_resets_shell_only_confirmations(self, tmp_path: Path) -> None:
        prompt_path = tmp_path / "prompt.txt"
        verdict_path = tmp_path / "verdict.yaml"
        prompt_path.write_text("do work", encoding="utf-8")
        step = Step(id="design", skill="design", agent="codex")
        launch = HerdrPaneLaunch(
            pane_id="w1:p2",
            split_target_pane="w1:p1",
            direction="right",
            panes_before=[],
            panes_pruned=[],
        )
        observations = 0

        shell_only = {
            "shell_pid": 100,
            "foreground_processes": [{"pid": 100, "name": "bash"}],
        }

        def observations_then_verdict(*args: object, **kwargs: object) -> dict[str, object]:
            nonlocal observations
            observations += 1
            if observations == 6:
                verdict_path.write_text(
                    "status: PASS\nreason: ok\nevidence: ok\n", encoding="utf-8"
                )
            if observations in {3, 6}:
                return {}
            return shell_only

        with (
            patch(
                "kaji_harness.interactive_terminal_herdr._preflight_herdr",
                return_value=("/usr/bin/herdr", "w1:p1", "herdr 0.8.2"),
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr._launch_herdr_pane",
                return_value=launch,
            ),
            patch("kaji_harness.interactive_terminal_herdr._mark_herdr_pane"),
            patch("kaji_harness.interactive_terminal_herdr._run_herdr_pane_command"),
            patch(
                "kaji_harness.interactive_terminal_herdr._get_herdr_process_info",
                side_effect=observations_then_verdict,
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr._close_owned_herdr_pane",
                return_value=True,
            ) as close,
            patch(
                "kaji_harness.interactive_terminal_herdr.time.monotonic",
                side_effect=[0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0],
            ),
            patch("kaji_harness.interactive_terminal_herdr.time.sleep"),
        ):
            result = execute_interactive_terminal_herdr(
                step=step,
                prompt_path=prompt_path,
                verdict_path=verdict_path,
                workdir=tmp_path,
                timeout=30,
            )

        assert observations == 6
        assert result.session_id is None
        close.assert_called_once()

    def test_process_info_query_failure_does_not_cleanup_pane(self, tmp_path: Path) -> None:
        prompt_path = tmp_path / "prompt.txt"
        prompt_path.write_text("do work", encoding="utf-8")
        step = Step(id="design", skill="design", agent="codex")
        error = CLIExecutionError("interactive_terminal", 1, "process-info failed")

        with (
            patch(
                "kaji_harness.interactive_terminal_herdr._preflight_herdr",
                return_value=("/usr/bin/herdr", "w1:p1", "herdr 0.8.2"),
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr._launch_herdr_pane",
                return_value=HerdrPaneLaunch(
                    pane_id="w1:p2",
                    split_target_pane="w1:p1",
                    direction="right",
                    panes_before=[],
                    panes_pruned=[],
                ),
            ),
            patch("kaji_harness.interactive_terminal_herdr._mark_herdr_pane"),
            patch("kaji_harness.interactive_terminal_herdr._run_herdr_pane_command"),
            patch(
                "kaji_harness.interactive_terminal_herdr._get_herdr_process_info",
                side_effect=error,
            ),
            patch("kaji_harness.interactive_terminal_herdr._close_owned_herdr_pane") as close,
            patch(
                "kaji_harness.interactive_terminal_herdr.time.monotonic",
                side_effect=[0.0, 1.0],
            ),
        ):
            with pytest.raises(CLIExecutionError, match="process-info failed"):
                execute_interactive_terminal_herdr(
                    step=step,
                    prompt_path=prompt_path,
                    verdict_path=tmp_path / "verdict.yaml",
                    workdir=tmp_path,
                    timeout=30,
                )

        close.assert_not_called()

    def test_verdict_retains_owned_pane_when_close_is_disabled(self, tmp_path: Path) -> None:
        prompt_path = tmp_path / "prompt.txt"
        verdict_path = tmp_path / "verdict.yaml"
        prompt_path.write_text("do work", encoding="utf-8")
        step = Step(id="design", skill="design", agent="codex")
        launch = HerdrPaneLaunch(
            pane_id="w1:p2",
            split_target_pane="w1:p1",
            direction="right",
            panes_before=[],
            panes_pruned=[],
        )

        def write_verdict(*args: object, **kwargs: object) -> None:
            verdict_path.write_text("status: PASS\nreason: ok\nevidence: ok\n", encoding="utf-8")

        with (
            patch(
                "kaji_harness.interactive_terminal_herdr._preflight_herdr",
                return_value=("/usr/bin/herdr", "w1:p1", "herdr 0.8.2"),
            ),
            patch(
                "kaji_harness.interactive_terminal_herdr._launch_herdr_pane",
                return_value=launch,
            ),
            patch("kaji_harness.interactive_terminal_herdr._mark_herdr_pane"),
            patch(
                "kaji_harness.interactive_terminal_herdr._run_herdr_pane_command",
                side_effect=write_verdict,
            ),
            patch("kaji_harness.interactive_terminal_herdr._close_owned_herdr_pane") as close,
        ):
            result = execute_interactive_terminal_herdr(
                step=step,
                prompt_path=prompt_path,
                verdict_path=verdict_path,
                workdir=tmp_path,
                timeout=30,
                close_on_verdict=False,
            )

        close.assert_not_called()
        assert result.session_id is None
        metadata = json.loads((tmp_path / "pane-metadata.json").read_text(encoding="utf-8"))
        assert metadata["close_on_verdict"] is False
        assert metadata["transcript_kind"] == "raw_pty_recording"
        assert metadata["transcript_available"] is False


@pytest.mark.medium
def test_materialized_launcher_is_directly_executable(tmp_path: Path) -> None:
    """Keep the marker private without changing the wrapper's inherited umask."""
    launcher_path = tmp_path / "herdr-launcher.sh"
    wrapper_output = tmp_path / "wrapper-output"
    wrapper_umask = tmp_path / "wrapper-umask"
    wrapper_command = shlex.join(
        [
            "/bin/sh",
            "-c",
            (
                f"umask > {shlex.quote(str(wrapper_umask))}; "
                f"printf '%s\\n' completed > {shlex.quote(str(wrapper_output))}"
            ),
        ]
    )
    pane_command = _materialize_herdr_launcher(launcher_path, wrapper_command)

    completed = subprocess.run(
        [
            "/bin/sh",
            "-c",
            'umask 022; exec "$1"',
            "launcher-test",
            shlex.split(pane_command)[0],
        ],
        check=False,
        capture_output=True,
    )

    assert completed.returncode == 0
    assert wrapper_output.read_text(encoding="utf-8") == "completed\n"
    assert int(wrapper_umask.read_text(encoding="utf-8").strip(), 8) == 0o022
    assert stat.S_IMODE(wrapper_output.stat().st_mode) == 0o644
    started_path = _herdr_launcher_started_path(launcher_path)
    assert started_path.is_file()
    assert stat.S_IMODE(started_path.stat().st_mode) == 0o600


@pytest.mark.medium
def test_stateful_fake_herdr_executable_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise the subprocess-backed fake through verdict, recorder command, and exact close."""
    repo_root = Path(__file__).resolve().parents[1]
    fake_herdr = repo_root / "experiments/herdr-interactive-terminal/scripts/herdr"
    state_path = tmp_path / "state.json"
    prompt_path = tmp_path / "prompt.txt"
    verdict_path = tmp_path / "verdict.yaml"
    prompt_path.write_text("write the verdict artifact", encoding="utf-8")
    monkeypatch.setenv("HERDR_BIN_PATH", str(fake_herdr))
    monkeypatch.setenv("HERDR_ENV", "1")
    monkeypatch.setenv("HERDR_PANE_ID", "w1:p1")
    monkeypatch.setenv("KAJI_FAKE_HERDR", "1")
    monkeypatch.setenv("KAJI_FAKE_HERDR_STATE", str(state_path))

    result = execute_interactive_terminal_herdr(
        step=Step(id="fake-herdr", skill="fake", agent="claude"),
        prompt_path=prompt_path,
        verdict_path=verdict_path,
        workdir=tmp_path,
        timeout=10,
    )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    metadata = json.loads((tmp_path / "pane-metadata.json").read_text(encoding="utf-8"))
    assert result.session_id is not None
    assert verdict_path.is_file()
    assert state["closed"] is True
    assert metadata["marker_confirmed"] is True
    assert metadata["transcript_kind"] == "raw_pty_recording"
    assert "pane" not in state.get("reads", [])
    command = state["command"]
    assert command[0].endswith("wrapper.sh")
    assert command[1] == "claude"


# ============================================================
# Issue #421: attempt deadline
# ============================================================

_HERDR = "kaji_harness.interactive_terminal_herdr"


class _FakeClock:
    """Deterministic monotonic clock whose ``sleep`` advances time."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


_LAUNCH = HerdrPaneLaunch(
    pane_id="w1:p2",
    split_target_pane="w1:p1",
    direction="right",
    panes_before=[],
    panes_pruned=[],
)


@pytest.mark.small
class TestLauncherStartWaitAttemptDeadline:
    """launcher 開始待ちは min(launcher 上限, attempt deadline) で打ち切られる。"""

    def test_attempt_deadline_first_raises_internal_signal(self, tmp_path: Path) -> None:
        clock = _FakeClock(100.0)
        with (
            patch(f"{_HERDR}.time.monotonic", clock.monotonic),
            patch(f"{_HERDR}.time.sleep", clock.sleep),
            patch(f"{_HERDR}._get_herdr_process_info") as process_info,
            pytest.raises(_AttemptDeadlineReachedError),
        ):
            _wait_for_herdr_launcher_start(
                "/usr/bin/herdr",
                "w1:p2",
                tmp_path / "herdr-launcher-started",
                attempt_deadline=103.0,
            )

        assert sum(clock.slept) == pytest.approx(3.0)
        assert clock.now == pytest.approx(103.0)
        process_info.assert_not_called()

    def test_launcher_limit_first_keeps_launcher_failure(self, tmp_path: Path) -> None:
        clock = _FakeClock(100.0)
        shell_only = {"shell_pid": 100, "foreground_processes": [{"pid": 100}]}
        with (
            patch(f"{_HERDR}.time.monotonic", clock.monotonic),
            patch(f"{_HERDR}.time.sleep", clock.sleep),
            patch(f"{_HERDR}._get_herdr_process_info", return_value=shell_only),
            pytest.raises(CLIExecutionError, match="start confirmation timed out") as exc_info,
        ):
            _wait_for_herdr_launcher_start(
                "/usr/bin/herdr",
                "w1:p2",
                tmp_path / "herdr-launcher-started",
                attempt_deadline=100.0 + 1000.0,
            )

        assert exc_info.value.returncode == 124
        assert sum(clock.slept) == pytest.approx(10.0)

    def test_none_keeps_independent_ten_second_wait(self, tmp_path: Path) -> None:
        clock = _FakeClock(0.0)
        with (
            patch(f"{_HERDR}.time.monotonic", clock.monotonic),
            patch(f"{_HERDR}.time.sleep", clock.sleep),
            patch(f"{_HERDR}._get_herdr_process_info", return_value={}),
            pytest.raises(CLIExecutionError, match="start confirmation timed out"),
        ):
            _wait_for_herdr_launcher_start(
                "/usr/bin/herdr", "w1:p2", tmp_path / "herdr-launcher-started"
            )

        assert sum(clock.slept) == pytest.approx(10.0)

    def test_marker_before_attempt_deadline_returns(self, tmp_path: Path) -> None:
        started_path = tmp_path / "herdr-launcher-started"
        clock = _FakeClock(0.0)

        def sleep_then_publish(seconds: float) -> None:
            clock.sleep(seconds)
            started_path.write_text("1\n", encoding="utf-8")

        with (
            patch(f"{_HERDR}.time.monotonic", clock.monotonic),
            patch(f"{_HERDR}.time.sleep", sleep_then_publish),
        ):
            _wait_for_herdr_launcher_start(
                "/usr/bin/herdr", "w1:p2", started_path, attempt_deadline=5.0
            )


@pytest.mark.medium
class TestExecuteHerdrAttemptDeadline:
    """runner から渡された deadline_monotonic を Herdr backend が使う。"""

    def _run(
        self,
        tmp_path: Path,
        *,
        deadline_monotonic: float | None,
        agent: str = "codex",
        launcher_wait_side_effect: BaseException | None = None,
        clock: _FakeClock | None = None,
        timeout: int = 30,
    ) -> dict[str, object]:
        prompt_path = tmp_path / "prompt.txt"
        prompt_path.write_text("do work", encoding="utf-8")
        step = Step(id="design", skill="design", agent=agent)
        clock = clock or _FakeClock(0.0)
        recorded: dict[str, object] = {}
        with (
            patch(
                f"{_HERDR}._preflight_herdr",
                return_value=("/usr/bin/herdr", "w1:p1", "herdr 0.8.2"),
            ),
            patch(f"{_HERDR}._launch_herdr_pane", return_value=_LAUNCH) as launch,
            patch(f"{_HERDR}._mark_herdr_pane"),
            patch(f"{_HERDR}._run_herdr_pane_command"),
            patch(
                f"{_HERDR}._wait_for_herdr_launcher_start",
                side_effect=launcher_wait_side_effect,
            ) as wait,
            patch(f"{_HERDR}._get_herdr_process_info", return_value={}),
            patch(f"{_HERDR}._classify_herdr_process_liveness", return_value="running"),
            patch(f"{_HERDR}._close_owned_herdr_pane", return_value=True) as close,
            patch(f"{_HERDR}.time.monotonic", clock.monotonic),
            patch(f"{_HERDR}.time.sleep", clock.sleep),
            patch(f"{_HERDR}.uuid.uuid4", return_value="run-id"),
        ):
            try:
                execute_interactive_terminal_herdr(
                    step=step,
                    prompt_path=prompt_path,
                    verdict_path=tmp_path / "verdict.yaml",
                    workdir=tmp_path,
                    timeout=timeout,
                    deadline_monotonic=deadline_monotonic,
                )
            except StepTimeoutError as exc:
                recorded["error"] = exc
            recorded.update(launch=launch, wait=wait, close=close, clock=clock)
        return recorded

    def test_expired_deadline_does_not_launch_pane(self, tmp_path: Path) -> None:
        clock = _FakeClock(500.0)

        recorded = self._run(tmp_path, deadline_monotonic=499.0, clock=clock)

        recorded["launch"].assert_not_called()  # type: ignore[attr-defined]
        error = recorded["error"]
        assert isinstance(error, StepTimeoutError)
        assert error.session_resolution is None
        assert error.timeout == 30

    def test_deadline_reached_after_launch_times_out_with_session_resolution(
        self, tmp_path: Path
    ) -> None:
        clock = _FakeClock(0.0)

        recorded = self._run(tmp_path, deadline_monotonic=2.0, clock=clock)

        error = recorded["error"]
        assert isinstance(error, StepTimeoutError)
        assert error.session_resolution == SessionResolution(None)
        # attempt deadline（2.0）が timeout(30) より優先される。
        assert clock.now < 30
        recorded["close"].assert_called_once()  # type: ignore[attr-defined]
        assert (tmp_path / "pane-metadata.json").is_file()

    def test_launcher_wait_receives_attempt_deadline(self, tmp_path: Path) -> None:
        recorded = self._run(tmp_path, deadline_monotonic=2.0)

        wait = recorded["wait"]
        assert wait.call_args.kwargs["attempt_deadline"] == 2.0  # type: ignore[attr-defined]

    def test_attempt_deadline_in_launcher_wait_takes_timeout_path(self, tmp_path: Path) -> None:
        recorded = self._run(
            tmp_path,
            deadline_monotonic=5.0,
            launcher_wait_side_effect=_AttemptDeadlineReachedError(),
        )

        error = recorded["error"]
        assert isinstance(error, StepTimeoutError)
        assert error.session_resolution == SessionResolution(None)
        recorded["close"].assert_called_once()  # type: ignore[attr-defined]
        metadata = json.loads((tmp_path / "pane-metadata.json").read_text(encoding="utf-8"))
        assert metadata["marker_confirmed"] is True

    def test_fresh_claude_launcher_budget_exhaustion_carries_launch_session(
        self, tmp_path: Path
    ) -> None:
        prompt_path = tmp_path / "prompt.txt"
        prompt_path.write_text("do work", encoding="utf-8")
        step = Step(id="design", skill="design", agent="claude")
        with (
            patch(
                f"{_HERDR}._preflight_herdr",
                return_value=("/usr/bin/herdr", "w1:p1", "herdr 0.8.2"),
            ),
            patch(f"{_HERDR}._launch_herdr_pane", return_value=_LAUNCH),
            patch(f"{_HERDR}._mark_herdr_pane"),
            patch(f"{_HERDR}._run_herdr_pane_command"),
            patch(
                f"{_HERDR}._wait_for_herdr_launcher_start",
                side_effect=_AttemptDeadlineReachedError(),
            ),
            patch(f"{_HERDR}._close_owned_herdr_pane", return_value=True),
            patch(f"{_HERDR}.uuid.uuid4", side_effect=["run-id", "launch-session-id"]),
            patch(f"{_HERDR}.time.monotonic", return_value=0.0),
            pytest.raises(StepTimeoutError) as exc_info,
        ):
            execute_interactive_terminal_herdr(
                step=step,
                prompt_path=prompt_path,
                verdict_path=tmp_path / "verdict.yaml",
                workdir=tmp_path,
                timeout=30,
                deadline_monotonic=5.0,
            )

        assert exc_info.value.session_resolution == SessionResolution("launch-session-id")

    def test_none_keeps_timeout_based_deadline(self, tmp_path: Path) -> None:
        clock = _FakeClock(0.0)

        recorded = self._run(tmp_path, deadline_monotonic=None, clock=clock, timeout=3)

        assert isinstance(recorded["error"], StepTimeoutError)
        assert clock.now >= 3


@pytest.mark.small
class TestDispatchForwardsDeadlineToHerdr:
    def test_deadline_monotonic_is_forwarded(self, tmp_path: Path) -> None:
        from kaji_harness.interactive_terminal import execute_interactive_terminal

        step = Step(id="design", skill="design", agent="codex")
        with patch(
            "kaji_harness.interactive_terminal_herdr.execute_interactive_terminal_herdr"
        ) as herdr_entry:
            execute_interactive_terminal(
                step=step,
                prompt_path=tmp_path / "prompt.txt",
                verdict_path=tmp_path / "verdict.yaml",
                workdir=tmp_path,
                timeout=30,
                deadline_monotonic=1234.5,
            )

        assert herdr_entry.call_args.kwargs["deadline_monotonic"] == 1234.5


# ============================================================
# Issue #515: PTY recorder の組み込み
# ============================================================


@pytest.mark.small
class TestRecordedCommand:
    @pytest.mark.parametrize(
        ("agent", "manifest"),
        [("claude", "claude"), ("codex", "codex"), ("antigravity", "agy")],
    )
    def test_order_is_env_hint_recorder_then_wrapper(self, agent: str, manifest: str) -> None:
        wrapper_command = (
            "/pkg/wrapper.sh claude '/prompt with spaces' /verdict /work '' '' '' '' auto ''"
        )
        command = _build_recorded_command(
            wrapper_command,
            agent=agent,
            recorder=Path("/pkg/interactive_terminal_recorder.py"),
            terminal_log=Path("/attempt dir/terminal.log"),
            env={"TMPDIR": "/tmp/attempt", "KAJI_X": "a b"},
        )

        argv = shlex.split(command)
        assert argv[:4] == ["env", "TMPDIR=/tmp/attempt", "KAJI_X=a b", f"HERDR_AGENT={manifest}"]
        assert argv[4:9] == [
            sys.executable,
            "-I",
            "/pkg/interactive_terminal_recorder.py",
            "/attempt dir/terminal.log",
            "--",
        ]
        assert argv[9:] == shlex.split(wrapper_command)
        assert command.endswith(" " + wrapper_command)

    def test_without_attempt_env_still_sets_hint(self) -> None:
        argv = shlex.split(
            _build_recorded_command(
                "/pkg/wrapper.sh",
                agent="codex",
                recorder=Path("/pkg/rec.py"),
                terminal_log=Path("/a/terminal.log"),
                env=None,
            )
        )
        assert argv[:2] == ["env", "HERDR_AGENT=codex"]

    def test_unknown_agent_has_no_manifest(self) -> None:
        with pytest.raises(ValueError, match="manifest"):
            _build_recorded_command(
                "/pkg/wrapper.sh",
                agent="unknown",
                recorder=Path("/pkg/rec.py"),
                terminal_log=Path("/a/terminal.log"),
                env=None,
            )

    def test_recorder_path_is_the_packaged_module(self) -> None:
        assert _recorder_path().name == "interactive_terminal_recorder.py"
        assert _recorder_path().is_file()


def _write_state(terminal_log: Path, text: str) -> None:
    (terminal_log.parent / (terminal_log.name + ".state")).write_bytes(
        text.encode("ascii").ljust(31) + b"\n"
    )


@pytest.mark.medium
class TestTranscriptMetadata:
    def _metadata(self, tmp_path: Path, *, include_diagnostic: bool = False) -> dict[str, object]:
        destination = tmp_path / "pane-metadata.json"
        _write_herdr_metadata(
            destination,
            herdr_version="herdr 0.8.2",
            pane_id="w1:p2",
            origin_pane="w1:p1",
            run_id="run",
            close_on_verdict=True,
            marker_confirmed=True,
            terminal_log=tmp_path / "terminal.log",
            include_diagnostic=include_diagnostic,
        )
        return cast(dict[str, object], json.loads(destination.read_text(encoding="utf-8")))

    @pytest.mark.parametrize(
        ("state", "expected_state", "truncated", "error"),
        [
            ("recording", "recording", False, None),
            ("complete", "complete", False, None),
            ("incomplete ENOSPC", "incomplete", True, "ENOSPC"),
            (None, "unknown", None, None),
            ("garbage", "unknown", None, None),
        ],
    )
    def test_recorder_state_is_reflected(
        self,
        tmp_path: Path,
        state: str | None,
        expected_state: str,
        truncated: bool | None,
        error: str | None,
    ) -> None:
        (tmp_path / "terminal.log").write_bytes(b"\x1b[?1049hraw bytes\r\n")
        if state is not None:
            _write_state(tmp_path / "terminal.log", state)

        metadata = self._metadata(tmp_path)

        assert metadata["transcript_kind"] == "raw_pty_recording"
        assert metadata["transcript_available"] is True
        assert metadata["transcript_recorder_state"] == expected_state
        assert metadata["transcript_truncated"] is truncated
        assert metadata["transcript_revision"] is None
        assert metadata["transcript_bytes"] == len(b"\x1b[?1049hraw bytes\r\n")
        assert metadata.get("transcript_error") == error
        assert "terminal_diagnostic" not in metadata

    def test_unreadable_state_is_unknown(self, tmp_path: Path) -> None:
        (tmp_path / "terminal.log").write_bytes(b"x")
        (tmp_path / "terminal.log.state").mkdir()

        metadata = self._metadata(tmp_path)

        assert metadata["transcript_recorder_state"] == "unknown"
        assert metadata["transcript_truncated"] is None

    def test_missing_log_omits_size_fields(self, tmp_path: Path) -> None:
        metadata = self._metadata(tmp_path)

        assert metadata["transcript_available"] is False
        for key in ("transcript_truncated", "transcript_revision", "transcript_bytes"):
            assert key not in metadata

    def test_state_read_error_does_not_break_metadata(self, tmp_path: Path) -> None:
        (tmp_path / "terminal.log").write_bytes(b"x")
        with patch.object(Path, "read_bytes", side_effect=PermissionError("denied")):
            metadata = self._metadata(tmp_path)
        assert metadata["transcript_recorder_state"] == "unknown"

    def test_diagnostic_reads_recorder_log(self, tmp_path: Path) -> None:
        (tmp_path / "terminal.log").write_bytes(
            b"\x1b[?1049hAPI Error: 529 overloaded_error\r\n\x1b[?1049l"
        )
        _write_state(tmp_path / "terminal.log", "complete")

        metadata = self._metadata(tmp_path, include_diagnostic=True)

        diagnostic = cast(dict[str, object], metadata["terminal_diagnostic"])
        assert diagnostic["kind"] == "provider_error"

    @pytest.mark.parametrize(
        ("state", "flagged"),
        [("complete", False), ("recording", False), ("incomplete ENOSPC", True), (None, True)],
    )
    def test_exit_detail_flags_incomplete_or_unknown(
        self, tmp_path: Path, state: str | None, flagged: bool
    ) -> None:
        terminal_log = tmp_path / "terminal.log"
        terminal_log.write_bytes(b"API Error: 529 overloaded_error\r\n")
        if state is not None:
            _write_state(terminal_log, state)

        detail = _recorded_exit_detail(terminal_log, prefix="Herdr pane exited")

        assert detail.startswith("Herdr pane exited; transient provider error detected")
        expected_state = "incomplete" if state and state.startswith("incomplete") else "unknown"
        marker = f"; transcript may be incomplete (recorder state: {expected_state})"
        assert (marker in detail) is flagged
        assert "ENOSPC" not in detail


@pytest.mark.small
def test_missing_recorder_fails_before_pane_launch(tmp_path: Path) -> None:
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("do work", encoding="utf-8")
    with (
        patch(
            "kaji_harness.interactive_terminal_herdr._preflight_herdr",
            return_value=("/usr/bin/herdr", "w1:p1", "herdr 0.8.2"),
        ),
        patch(
            "kaji_harness.interactive_terminal_herdr._recorder_path",
            return_value=tmp_path / "missing_recorder.py",
        ),
        patch("kaji_harness.interactive_terminal_herdr._launch_herdr_pane") as launch,
        pytest.raises(FileNotFoundError, match="recorder not found"),
    ):
        execute_interactive_terminal_herdr(
            step=Step(id="design", skill="design", agent="claude"),
            prompt_path=prompt_path,
            verdict_path=tmp_path / "verdict.yaml",
            workdir=tmp_path,
            timeout=30,
        )
    launch.assert_not_called()
