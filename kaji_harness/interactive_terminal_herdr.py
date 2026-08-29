"""Herdr backend for the interactive terminal runner.

The backend uses the release-matched ``herdr`` CLI as a JSON request/response
wrapper. It never targets the UI-focused pane implicitly: the caller must be a
Herdr pane with ``HERDR_ENV=1`` and an explicit ``HERDR_PANE_ID``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .agents import AGENT_CAPABILITIES
from .errors import (
    CLIExecutionError,
    CLINotFoundError,
    HerdrSessionRequiredError,
    StepTimeoutError,
)
from .interactive_terminal import (
    _build_wrapper_command,
    _extract_codex_session_id,
    _resolve_abnormal_exit_session,
    _terminal_exit_detail,
    _wrapper_path,
    read_terminal_diagnostic,
)
from .models import CLIResult, Step

_console = logging.getLogger("kaji.interactive_terminal")

_MIN_HERDR_VERSION = (0, 8, 2)
_VERDICT_POLL_INTERVAL_SECONDS = 2
_PROCESS_EXIT_CONFIRMATIONS = 3
_TRANSCRIPT_LINES = 2000
_HERDR_METADATA_SOURCE = "kaji"
_MAX_VISIBLE_AGENT_PANES = 2
_HERDR_COMMAND_TIMEOUT_SECONDS = 10
_HERDR_LAUNCHER_START_TIMEOUT_SECONDS = 10
_HERDR_LAUNCHER_START_POLL_INTERVAL_SECONDS = 0.1
_HERDR_LAUNCHER_STARTED_FILENAME = "herdr-launcher-started"


class _HerdrResultEnvelope(BaseModel):
    """Validated common fields for one Herdr CLI result."""

    model_config = ConfigDict(extra="allow", strict=True)

    type: str = Field(min_length=1)


class _HerdrResponseEnvelope(BaseModel):
    """Validated outer envelope for one Herdr CLI JSON response."""

    model_config = ConfigDict(extra="ignore", strict=True)

    result: _HerdrResultEnvelope


@dataclass(frozen=True)
class HerdrManagedPane:
    """A kaji-owned Herdr pane ordered by its layout y-coordinate."""

    pane_id: str
    y: int
    run_id: str


@dataclass(frozen=True)
class HerdrPaneLaunch:
    """Placement and pruning outcome for one new Herdr agent pane."""

    pane_id: str
    split_target_pane: str
    direction: Literal["right", "down"]
    panes_before: list[str]
    panes_pruned: list[str]
    panes_skipped: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class HerdrPaneRead:
    """Rendered pane snapshot returned by ``pane read``.

    Attributes:
        text: Rendered terminal text.
        truncated: Whether Herdr omitted older content, or None when the CLI cannot report it.
        revision: Pane revision associated with the snapshot.
    """

    text: str
    truncated: bool | None
    revision: int


def execute_interactive_terminal_herdr(
    *,
    step: Step,
    prompt_path: Path,
    verdict_path: Path,
    workdir: Path,
    timeout: int,
    session_id: str | None = None,
    close_on_verdict: bool = True,
    execution_policy: str = "auto",
) -> CLIResult:
    """Start an interactive agent in a Herdr pane and wait for ``verdict.yaml``.

    Args:
        step: Workflow step with an interactive-terminal capable agent.
        prompt_path: Attempt-local prompt file.
        verdict_path: Artifact path whose appearance completes the step.
        workdir: Trusted cwd for the created pane and agent wrapper.
        timeout: Maximum seconds to wait for the verdict artifact.
        session_id: Previous provider session ID to resume.
        close_on_verdict: Close the owned pane after a verdict when true.
        execution_policy: Workflow execution policy passed to the wrapper.

    Returns:
        Empty-output CLI result with the resolved provider session ID.

    Raises:
        CLINotFoundError: Herdr is missing or its version is unsupported.
        HerdrSessionRequiredError: Caller context is not an explicit Herdr pane.
        CLIExecutionError: A Herdr operation fails or the agent exits early.
        StepTimeoutError: The verdict artifact does not appear before timeout.
        ValueError: The step has no supported agent.
        FileNotFoundError: The prompt or packaged wrapper is missing.
    """
    _validate_step(step, prompt_path)
    herdr, origin_pane, herdr_version = _preflight_herdr()
    wrapper = _wrapper_path()
    if not wrapper.is_file():
        raise FileNotFoundError(f"interactive terminal wrapper not found: {wrapper}")

    run_id = str(uuid.uuid4())
    launch_session_id = str(uuid.uuid4()) if step.agent == "claude" and session_id is None else ""
    launch = _launch_herdr_pane(herdr, origin_pane, workdir=workdir)
    pane_id = launch.pane_id
    metadata_path = prompt_path.parent / "pane-metadata.json"
    terminal_log = prompt_path.parent / "terminal.log"

    try:
        _mark_herdr_pane(
            herdr,
            pane_id,
            origin_pane=origin_pane,
            run_id=run_id,
            step_id=step.id,
        )
    except CLIExecutionError:
        _write_herdr_metadata(
            metadata_path,
            herdr_version=herdr_version,
            pane_id=pane_id,
            origin_pane=origin_pane,
            run_id=run_id,
            close_on_verdict=close_on_verdict,
            marker_confirmed=False,
            layout=launch,
        )
        raise

    command = _build_wrapper_command(
        wrapper,
        agent=cast(str, step.agent),
        prompt_path=prompt_path,
        verdict_path=verdict_path,
        workdir=workdir,
        resume_session_id=session_id or "",
        launch_session_id=launch_session_id,
        model=step.model or "",
        effort=step.effort or "",
        execution_policy=execution_policy,
    )
    try:
        launcher_path = prompt_path.parent / "herdr-launcher.sh"
        pane_command = _materialize_herdr_launcher(
            launcher_path,
            command,
        )
        _run_herdr_pane_command(herdr, pane_id, pane_command, workdir=workdir)
        _wait_for_herdr_launcher_start(
            herdr,
            pane_id,
            _herdr_launcher_started_path(launcher_path),
        )
    except CLIExecutionError:
        pane_read = _capture_herdr_snapshot(herdr, pane_id, terminal_log)
        close_error = _close_owned_herdr_pane_best_effort(
            herdr,
            pane_id,
            origin_pane=origin_pane,
            run_id=run_id,
        )
        _write_herdr_metadata(
            metadata_path,
            herdr_version=herdr_version,
            pane_id=pane_id,
            origin_pane=origin_pane,
            run_id=run_id,
            close_on_verdict=close_on_verdict,
            marker_confirmed=True,
            pane_read=pane_read,
            terminal_log=terminal_log,
            close_error=close_error,
            layout=launch,
        )
        raise

    _console.info(
        "pane launched: step=%s agent=%s pane=%s timeout=%ds verdict=%s backend=herdr",
        step.id,
        step.agent,
        pane_id,
        timeout,
        verdict_path,
    )

    deadline = time.monotonic() + timeout
    shell_only_observations = 0
    process_info: dict[str, object] | None = None
    try:
        while time.monotonic() < deadline:
            if verdict_path.is_file():
                pane_read = _capture_herdr_snapshot(herdr, pane_id, terminal_log)
                result_session_id = session_id or launch_session_id or None
                if result_session_id is None and step.agent == "codex":
                    result_session_id = _extract_codex_session_id(
                        terminal_log,
                        prompt_path=prompt_path,
                        verdict_path=verdict_path,
                    )
                close_error = None
                if close_on_verdict:
                    close_error = _close_owned_herdr_pane_best_effort(
                        herdr,
                        pane_id,
                        origin_pane=origin_pane,
                        run_id=run_id,
                    )
                _write_herdr_metadata(
                    metadata_path,
                    herdr_version=herdr_version,
                    pane_id=pane_id,
                    origin_pane=origin_pane,
                    run_id=run_id,
                    close_on_verdict=close_on_verdict,
                    marker_confirmed=True,
                    pane_read=pane_read,
                    close_error=close_error,
                    layout=launch,
                )
                return CLIResult(full_output="", session_id=result_session_id)

            process_info = _get_herdr_process_info(herdr, pane_id)
            liveness = _classify_herdr_process_liveness(process_info)
            if liveness == "confirmed_shell_only":
                shell_only_observations += 1
                if shell_only_observations >= _PROCESS_EXIT_CONFIRMATIONS:
                    pane_read = _capture_herdr_snapshot(herdr, pane_id, terminal_log)
                    resolved = _resolve_abnormal_exit_session(
                        cast(str, step.agent),
                        prompt_path=prompt_path,
                        verdict_path=verdict_path,
                        resume_session_id=session_id,
                        launch_session_id=launch_session_id,
                        pane_alive=False,
                    )
                    close_error = _close_owned_herdr_pane_best_effort(
                        herdr,
                        pane_id,
                        origin_pane=origin_pane,
                        run_id=run_id,
                    )
                    _write_herdr_metadata(
                        metadata_path,
                        herdr_version=herdr_version,
                        pane_id=pane_id,
                        origin_pane=origin_pane,
                        run_id=run_id,
                        close_on_verdict=close_on_verdict,
                        marker_confirmed=True,
                        pane_read=pane_read,
                        process_info=process_info,
                        terminal_log=terminal_log,
                        close_error=close_error,
                        layout=launch,
                    )
                    raise CLIExecutionError(
                        step.id,
                        1,
                        _terminal_exit_detail(
                            terminal_log,
                            prefix=(
                                "Herdr pane returned to its shell before writing verdict.yaml; "
                                "rendered snapshot may be incomplete"
                            ),
                        ),
                        session_resolution=resolved,
                    )
            else:
                shell_only_observations = 0
            time.sleep(_VERDICT_POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        # Preserve the live pane for inspection and leave enough ownership metadata for
        # recovery to report the orphan. Metadata failure must not mask Ctrl-C.
        try:
            pane_read = _capture_herdr_snapshot(herdr, pane_id, terminal_log)
            _write_herdr_metadata(
                metadata_path,
                herdr_version=herdr_version,
                pane_id=pane_id,
                origin_pane=origin_pane,
                run_id=run_id,
                close_on_verdict=close_on_verdict,
                marker_confirmed=True,
                pane_read=pane_read,
                process_info=process_info,
                layout=launch,
            )
        except OSError as exc:
            _console.warning("orphan pane metadata snapshot failed: %s", exc)
        raise

    pane_read = _capture_herdr_snapshot(herdr, pane_id, terminal_log)
    resolved = _resolve_abnormal_exit_session(
        cast(str, step.agent),
        prompt_path=prompt_path,
        verdict_path=verdict_path,
        resume_session_id=session_id,
        launch_session_id=launch_session_id,
        pane_alive=True,
    )
    close_error = _close_owned_herdr_pane_best_effort(
        herdr,
        pane_id,
        origin_pane=origin_pane,
        run_id=run_id,
    )
    _write_herdr_metadata(
        metadata_path,
        herdr_version=herdr_version,
        pane_id=pane_id,
        origin_pane=origin_pane,
        run_id=run_id,
        close_on_verdict=close_on_verdict,
        marker_confirmed=True,
        pane_read=pane_read,
        terminal_log=terminal_log,
        close_error=close_error,
        layout=launch,
    )
    raise StepTimeoutError(step.id, timeout, session_resolution=resolved)


def _validate_step(step: Step, prompt_path: Path) -> None:
    """Validate backend-independent interactive step inputs."""
    if step.agent is None:
        raise ValueError(f"interactive terminal runner requires step.agent (step={step.id})")
    capabilities = AGENT_CAPABILITIES.get(step.agent)
    if capabilities is None or not capabilities.supports_interactive_terminal:
        raise ValueError(f"interactive terminal runner does not support agent: {step.agent}")
    if not prompt_path.is_file():
        raise FileNotFoundError(f"prompt.txt not found: {prompt_path}")


def _resolve_herdr() -> str:
    """Resolve the installed Herdr CLI.

    Raises:
        CLINotFoundError: The ``herdr`` executable is not on ``PATH``.
    """
    configured_path = os.environ.get("HERDR_BIN_PATH")
    if configured_path:
        configured = Path(configured_path).expanduser()
        if configured.is_file() and os.access(configured, os.X_OK):
            return str(configured)
    herdr = shutil.which("herdr")
    if herdr is None:
        raise CLINotFoundError(
            "CLI 'herdr' not found. Install Herdr or select interactive_terminal_backend='tmux'."
        )
    return herdr


def _resolve_herdr_origin() -> str:
    """Resolve the explicit caller pane from the Herdr environment.

    Raises:
        HerdrSessionRequiredError: The process is outside Herdr or lacks a pane ID.
    """
    if os.environ.get("HERDR_ENV") != "1":
        raise HerdrSessionRequiredError(
            "Herdr interactive terminal backend must run inside Herdr (HERDR_ENV=1)."
        )
    origin_pane = os.environ.get("HERDR_PANE_ID")
    if not origin_pane:
        raise HerdrSessionRequiredError(
            "HERDR_PANE_ID is not set; cannot identify the caller pane safely."
        )
    return origin_pane


def _parse_herdr_version(text: str) -> tuple[int, int, int]:
    """Parse a semantic version tuple from ``herdr --version`` output.

    Raises:
        ValueError: No three-part numeric Herdr version is present.
    """
    match = re.search(r"\bherdr\s+(\d+)\.(\d+)\.(\d+)", text)
    if match is None:
        raise ValueError(f"unrecognized Herdr version output: {text!r}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _preflight_herdr() -> tuple[str, str, str]:
    """Validate CLI version, caller context, and origin pane visibility."""
    origin_pane = _resolve_herdr_origin()
    herdr = _resolve_herdr()
    completed = _run_herdr(herdr, ["--version"])
    try:
        version = _parse_herdr_version(completed.stdout)
    except ValueError as error:
        raise CLIExecutionError("interactive_terminal", 1, str(error)) from error
    if version < _MIN_HERDR_VERSION:
        raise CLINotFoundError(
            f"interactive terminal runner requires Herdr >= 0.8.2, got {completed.stdout.strip()}"
        )
    _run_herdr(herdr, ["status"])
    pane = _get_current_herdr_pane(herdr)
    if pane.get("pane_id") != origin_pane:
        raise CLIExecutionError(
            "interactive_terminal", 1, "Herdr returned a different pane for HERDR_PANE_ID"
        )
    return herdr, origin_pane, completed.stdout.strip()


def _run_herdr(
    herdr: str, arguments: list[str], *, workdir: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run one Herdr CLI request and require a successful exit status.

    Args:
        herdr: Resolved executable path.
        arguments: CLI arguments after the executable.
        workdir: Optional cwd for the client process.

    Returns:
        Successful completed process, including the command's response streams.

    Raises:
        CLIExecutionError: The command exits with a non-zero status.
    """
    try:
        completed = subprocess.run(
            [herdr, *arguments],
            text=True,
            capture_output=True,
            check=False,
            cwd=workdir,
            timeout=_HERDR_COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        operation = " ".join(arguments[:2]) or "request"
        raise CLIExecutionError(
            "interactive_terminal",
            124,
            (
                f"Herdr command timed out after {_HERDR_COMMAND_TIMEOUT_SECONDS} seconds: "
                f"{operation}"
            ),
        ) from error
    if completed.returncode != 0:
        raise CLIExecutionError(
            "interactive_terminal", completed.returncode, completed.stderr or completed.stdout
        )
    return completed


def _run_herdr_json(
    herdr: str, arguments: list[str], *, workdir: Path | None = None
) -> dict[str, object]:
    """Run one Herdr CLI request and parse its required JSON response.

    Args:
        herdr: Resolved executable path.
        arguments: CLI arguments after the executable.
        workdir: Optional cwd for the client process.

    Returns:
        Parsed JSON response envelope.

    Raises:
        CLIExecutionError: The command fails or emits a non-object JSON response.
    """
    completed = _run_herdr(herdr, arguments, workdir=workdir)
    return _parse_herdr_json_response(completed.stdout)


def _parse_herdr_json_response(text: str) -> dict[str, object]:
    """Validate a Herdr JSON response with the common Pydantic envelope."""
    try:
        parsed = _HerdrResponseEnvelope.model_validate_json(text)
    except ValidationError as error:
        raise CLIExecutionError(
            "interactive_terminal",
            1,
            f"Herdr returned invalid JSON response: {error}",
        ) from error
    return cast(dict[str, object], parsed.model_dump(mode="python"))


def _run_herdr_optional_ok_json(
    herdr: str, arguments: list[str], *, workdir: Path | None = None
) -> None:
    """Run a Herdr mutation that may omit its successful JSON envelope.

    Herdr 0.8.2 emits empty stdout for successful ``pane report-metadata``,
    ``pane run``, and ``pane close`` requests. A non-empty response remains
    strict: it must be a typed ``ok`` envelope.

    Args:
        herdr: Resolved executable path.
        arguments: CLI arguments after the executable.
        workdir: Optional cwd for the client process.

    Raises:
        CLIExecutionError: The command fails or emits an unexpected response.
    """
    completed = _run_herdr(herdr, arguments, workdir=workdir)
    if not completed.stdout.strip():
        return
    _herdr_result(_parse_herdr_json_response(completed.stdout), "ok")


def _herdr_result(response: dict[str, object], expected_type: str) -> dict[str, object]:
    """Extract and validate the typed Herdr result object.

    Raises:
        CLIExecutionError: The response does not contain the expected result type.
    """
    result = response.get("result")
    if not isinstance(result, dict) or result.get("type") != expected_type:
        raise CLIExecutionError(
            "interactive_terminal",
            1,
            f"Herdr response type mismatch: expected {expected_type!r}",
        )
    return cast(dict[str, object], result)


def _get_herdr_pane(herdr: str, pane_id: str) -> dict[str, object]:
    """Return one explicitly identified Herdr pane."""
    result = _herdr_result(_run_herdr_json(herdr, ["pane", "get", pane_id]), "pane_info")
    pane = result.get("pane")
    if not isinstance(pane, dict):
        raise CLIExecutionError("interactive_terminal", 1, "Herdr pane_info omitted pane")
    return cast(dict[str, object], pane)


def _get_current_herdr_pane(herdr: str) -> dict[str, object]:
    """Return the pane resolved from the calling process's Herdr context."""
    result = _herdr_result(
        _run_herdr_json(herdr, ["pane", "current", "--current"]),
        "pane_current",
    )
    pane = result.get("pane")
    if not isinstance(pane, dict):
        raise CLIExecutionError("interactive_terminal", 1, "Herdr pane_current omitted pane")
    return cast(dict[str, object], pane)


def _build_herdr_split_argv(
    herdr: str,
    *,
    split_target_pane: str,
    direction: Literal["right", "down"],
    workdir: Path,
) -> list[str]:
    """Build an explicit split argv that preserves caller cwd, focus, and PATH."""
    arguments = [
        herdr,
        "pane",
        "split",
        split_target_pane,
        "--direction",
        direction,
        "--ratio",
        "0.5",
        "--cwd",
        str(workdir),
        "--no-focus",
    ]
    caller_path = os.environ.get("PATH")
    if caller_path:
        arguments.extend(["--env", f"PATH={caller_path}"])
    return arguments


def _launch_herdr_pane(herdr: str, origin_pane: str, *, workdir: Path) -> HerdrPaneLaunch:
    """Prune owned panes, split the right column, and return launch metadata."""
    existing, skipped = _list_managed_herdr_panes(herdr, origin_pane)
    prune_count = max(0, len(existing) - (_MAX_VISIBLE_AGENT_PANES - 1))
    pruned = existing[:prune_count]
    remaining = existing[prune_count:]
    pruned_pane_ids: list[str] = []
    for pane in pruned:
        close_error = _close_owned_herdr_pane_best_effort(
            herdr,
            pane.pane_id,
            origin_pane=origin_pane,
            run_id=pane.run_id,
        )
        if close_error is None:
            pruned_pane_ids.append(pane.pane_id)
        else:
            skipped.append(pane.pane_id)

    selected_target, direction = _select_herdr_launch_placement(remaining)
    split_target_pane = selected_target or origin_pane
    argv = _build_herdr_split_argv(
        herdr,
        split_target_pane=split_target_pane,
        direction=direction,
        workdir=workdir,
    )
    result = _herdr_result(_run_herdr_json(herdr, argv[1:], workdir=workdir), "pane_info")
    new_pane = result.get("pane")
    if not isinstance(new_pane, dict) or not isinstance(new_pane.get("pane_id"), str):
        raise CLIExecutionError("interactive_terminal", 1, "Herdr pane split omitted a pane ID")
    pane_id = cast(str, new_pane["pane_id"])
    if not pane_id or pane_id == origin_pane:
        raise CLIExecutionError(
            "interactive_terminal", 1, "Herdr pane split returned an invalid pane ID"
        )
    return HerdrPaneLaunch(
        pane_id=pane_id,
        split_target_pane=split_target_pane,
        direction=direction,
        panes_before=[pane.pane_id for pane in existing],
        panes_pruned=pruned_pane_ids,
        panes_skipped=skipped,
    )


def _select_herdr_launch_placement(
    managed_panes: list[HerdrManagedPane],
) -> tuple[str | None, Literal["right", "down"]]:
    """Choose origin-right or bottom-managed-pane-down placement."""
    if not managed_panes:
        return None, "right"
    bottom = max(managed_panes, key=lambda pane: pane.y)
    return bottom.pane_id, "down"


def _list_managed_herdr_panes(
    herdr: str, origin_pane: str
) -> tuple[list[HerdrManagedPane], list[str]]:
    """List valid marker-owned panes and stale candidates skipped from management."""
    origin = _get_herdr_pane(herdr, origin_pane)
    workspace_id = origin.get("workspace_id")
    if (
        origin.get("pane_id") != origin_pane
        or not isinstance(workspace_id, str)
        or not workspace_id
    ):
        raise CLIExecutionError(
            "interactive_terminal", 1, "Herdr origin pane omitted its workspace ID"
        )
    pane_list = _herdr_result(
        _run_herdr_json(herdr, ["pane", "list", "--workspace", workspace_id]),
        "pane_list",
    )
    panes = pane_list.get("panes")
    if not isinstance(panes, list):
        raise CLIExecutionError("interactive_terminal", 1, "Herdr pane_list omitted panes")

    layout_result = _herdr_result(
        _run_herdr_json(herdr, ["pane", "layout", "--pane", origin_pane]),
        "pane_layout",
    )
    layout = layout_result.get("layout")
    if not isinstance(layout, dict) or not isinstance(layout.get("panes"), list):
        raise CLIExecutionError("interactive_terminal", 1, "Herdr pane_layout omitted panes")
    pane_positions: dict[str, tuple[int, int]] = {}
    for item in cast(list[object], layout["panes"]):
        if not isinstance(item, dict):
            continue
        pane_id = item.get("pane_id")
        rect = item.get("rect")
        if (
            isinstance(pane_id, str)
            and isinstance(rect, dict)
            and isinstance(rect.get("x"), int)
            and isinstance(rect.get("y"), int)
        ):
            pane_positions[pane_id] = (cast(int, rect["x"]), cast(int, rect["y"]))
    origin_position = pane_positions.get(origin_pane)
    if origin_position is None:
        raise CLIExecutionError(
            "interactive_terminal", 1, "Herdr pane_layout omitted the origin pane position"
        )
    origin_x = origin_position[0]

    managed: list[HerdrManagedPane] = []
    skipped: list[str] = []
    for item in cast(list[object], panes):
        if not isinstance(item, dict):
            continue
        pane_id = item.get("pane_id")
        tokens = item.get("tokens")
        if not isinstance(pane_id, str) or not pane_id or not isinstance(tokens, dict):
            continue
        if tokens.get("kaji_origin") != origin_pane:
            continue
        run_id = tokens.get("kaji_run")
        position = pane_positions.get(pane_id)
        if not isinstance(run_id, str) or not run_id or position is None:
            _console.warning(
                "stale Herdr pane candidate skipped: pane=%s reason=missing run token or layout",
                pane_id,
            )
            skipped.append(pane_id)
            continue
        if position[0] <= origin_x:
            continue
        managed.append(HerdrManagedPane(pane_id=pane_id, y=position[1], run_id=run_id))
    managed.sort(key=lambda pane: pane.y)
    return managed, skipped


def _build_herdr_marker_argv(
    herdr: str,
    *,
    pane_id: str,
    origin_pane: str,
    run_id: str,
    step_id: str,
) -> list[str]:
    """Build the source-scoped metadata request marking kaji pane ownership.

    The request deliberately omits ``--ttl-ms``. Herdr then retains these
    ownership tokens until they are replaced, explicitly cleared, or the pane
    closes, so a later cleanup request can re-read them safely.
    """
    return [
        herdr,
        "pane",
        "report-metadata",
        pane_id,
        "--source",
        _HERDR_METADATA_SOURCE,
        "--token",
        f"kaji_origin={origin_pane}",
        "--token",
        f"kaji_run={run_id}",
        "--token",
        f"kaji_step={step_id}",
    ]


def _mark_herdr_pane(
    herdr: str,
    pane_id: str,
    *,
    origin_pane: str,
    run_id: str,
    step_id: str,
) -> None:
    """Attach and independently confirm ownership of a newly created pane."""
    argv = _build_herdr_marker_argv(
        herdr,
        pane_id=pane_id,
        origin_pane=origin_pane,
        run_id=run_id,
        step_id=step_id,
    )
    _run_herdr_optional_ok_json(herdr, argv[1:])
    pane = _get_herdr_pane(herdr, pane_id)
    tokens = pane.get("tokens")
    expected_tokens = {
        "kaji_origin": origin_pane,
        "kaji_run": run_id,
        "kaji_step": step_id,
    }
    if (
        pane.get("pane_id") != pane_id
        or not isinstance(tokens, dict)
        or any(tokens.get(name) != value for name, value in expected_tokens.items())
    ):
        raise CLIExecutionError(
            "interactive_terminal",
            1,
            f"Herdr pane ownership metadata was not confirmed: {pane_id}",
        )


def _run_herdr_pane_command(herdr: str, pane_id: str, command: str, *, workdir: Path) -> None:
    """Run one short launcher command in an explicit Herdr pane."""
    _run_herdr_optional_ok_json(
        herdr,
        ["pane", "run", pane_id, command],
        workdir=workdir,
    )


def _materialize_herdr_launcher(launcher_path: Path, wrapper_command: str) -> str:
    """Publish a private launcher and return the short pane command.

    Herdr encodes ``pane run`` text according to terminal mode observed at request time.
    Keeping the long wrapper payload in a file avoids depending on fresh-shell input mode.

    Args:
        launcher_path: Executable path in a unique attempt directory. The uniqueness
            makes the fixed ``.tmp`` name safe with exclusive creation.
        wrapper_command: Shell-quoted packaged wrapper command.

    Returns:
        Short child command that runs the launcher without replacing the interactive shell.

    Raises:
        CLIExecutionError: The launcher cannot be written or published atomically.
    """
    caller_path = os.environ.get("PATH")
    environment_prefix = f"env {shlex.quote(f'PATH={caller_path}')} " if caller_path else ""
    started_path = _herdr_launcher_started_path(launcher_path)
    started_temporary_path = started_path.with_suffix(".tmp")
    content = (
        "#!/bin/sh\n"
        "set -eu\n"
        f"(umask 077; printf '%s\\n' \"$$\" > "
        f"{shlex.quote(str(started_temporary_path))})\n"
        f"mv -f {shlex.quote(str(started_temporary_path))} {shlex.quote(str(started_path))}\n"
        f"exec {environment_prefix}{wrapper_command}\n"
    )
    temporary_path = launcher_path.with_suffix(launcher_path.suffix + ".tmp")
    try:
        launcher_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        descriptor = os.open(temporary_path, flags, 0o700)
        with os.fdopen(descriptor, "w", encoding="utf-8") as launcher:
            launcher.write(content)
            launcher.flush()
            os.fsync(launcher.fileno())
        os.replace(temporary_path, launcher_path)
    except OSError as error:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise CLIExecutionError(
            "interactive_terminal",
            1,
            f"Herdr launcher creation failed for {launcher_path}: {error}",
        ) from error
    return shlex.quote(str(launcher_path))


def _herdr_launcher_started_path(launcher_path: Path) -> Path:
    """Return the attempt-local launcher start marker path."""
    return launcher_path.with_name(_HERDR_LAUNCHER_STARTED_FILENAME)


def _wait_for_herdr_launcher_start(herdr: str, pane_id: str, started_path: Path) -> None:
    """Wait boundedly for proof that the dispatched launcher executed.

    Shell-only observations before the marker are startup state, not agent exit. Process
    information is sampled only to make timeout diagnostics actionable; the atomically
    published filesystem marker is the start authority.

    Raises:
        CLIExecutionError: The marker does not appear within the bounded wait.
    """
    deadline = time.monotonic() + _HERDR_LAUNCHER_START_TIMEOUT_SECONDS
    while True:
        if started_path.is_file():
            return
        if time.monotonic() >= deadline:
            break
        time.sleep(_HERDR_LAUNCHER_START_POLL_INTERVAL_SECONDS)

    last_process_state: str
    try:
        process_info = _get_herdr_process_info(herdr, pane_id)
        last_process_state = _classify_herdr_process_liveness(process_info)
    except CLIExecutionError as error:
        last_process_state = f"unavailable ({error.stderr})"
    raise CLIExecutionError(
        "interactive_terminal",
        124,
        (
            f"Herdr launcher start confirmation timed out for pane {pane_id} after "
            f"{_HERDR_LAUNCHER_START_TIMEOUT_SECONDS} seconds; "
            f"last process state: {last_process_state}"
        ),
    )


def _read_herdr_pane(herdr: str, pane_id: str) -> HerdrPaneRead:
    """Read a rendered recent-unwrapped diagnostic snapshot."""
    completed = _run_herdr(
        herdr,
        [
            "pane",
            "read",
            pane_id,
            "--source",
            "recent-unwrapped",
            "--lines",
            str(_TRANSCRIPT_LINES),
        ],
    )
    pane = _get_herdr_pane(herdr, pane_id)
    revision = pane.get("revision")
    if pane.get("pane_id") != pane_id or not isinstance(revision, int):
        raise CLIExecutionError(
            "interactive_terminal", 1, "Herdr pane read revision was not confirmed"
        )
    return HerdrPaneRead(text=completed.stdout, truncated=None, revision=revision)


def _capture_herdr_snapshot(herdr: str, pane_id: str, terminal_log: Path) -> HerdrPaneRead | None:
    """Save a best-effort rendered pane snapshot without masking the main result."""
    try:
        pane_read = _read_herdr_pane(herdr, pane_id)
        terminal_log.parent.mkdir(parents=True, exist_ok=True)
        terminal_log.write_text(pane_read.text, encoding="utf-8")
    except (CLIExecutionError, OSError) as error:
        _console.warning(
            "Herdr rendered snapshot capture failed: pane=%s detail=%s", pane_id, error
        )
        return None
    return pane_read


def _get_herdr_process_info(herdr: str, pane_id: str) -> dict[str, object]:
    """Return foreground process information for an explicit Herdr pane."""
    result = _herdr_result(
        _run_herdr_json(herdr, ["pane", "process-info", "--pane", pane_id]),
        "pane_process_info",
    )
    process_info = result.get("process_info")
    if not isinstance(process_info, dict):
        raise CLIExecutionError(
            "interactive_terminal", 1, "Herdr pane_process_info omitted process_info"
        )
    return cast(dict[str, object], process_info)


def _classify_herdr_process_liveness(
    process_info: dict[str, object],
) -> Literal["active", "confirmed_shell_only", "unknown"]:
    """Classify process liveness without treating optional-field absence as shell exit."""
    shell_pid = process_info.get("shell_pid")
    foreground_processes = process_info.get("foreground_processes")
    if type(shell_pid) is not int or not isinstance(foreground_processes, list):
        return "unknown"
    if not foreground_processes:
        return "unknown"

    process_pids: list[int] = []
    for process in foreground_processes:
        if not isinstance(process, dict):
            return "unknown"
        process_pid = process.get("pid")
        if type(process_pid) is not int:
            return "unknown"
        process_pids.append(process_pid)

    if any(process_pid != shell_pid for process_pid in process_pids):
        return "active"
    return "confirmed_shell_only"


def _close_owned_herdr_pane(
    herdr: str,
    pane_id: str,
    *,
    origin_pane: str,
    run_id: str,
) -> bool:
    """Close a pane only when its current tokens confirm exact kaji ownership.

    Returns:
        True when the owned pane was closed, otherwise false.
    """
    if not pane_id or not origin_pane or not run_id:
        return False
    try:
        pane = _get_herdr_pane(herdr, pane_id)
    except CLIExecutionError:
        return False
    tokens = pane.get("tokens")
    if pane.get("pane_id") != pane_id or not isinstance(tokens, dict):
        return False
    if tokens.get("kaji_origin") != origin_pane or tokens.get("kaji_run") != run_id:
        return False
    workspace_id = pane.get("workspace_id")
    if not isinstance(workspace_id, str) or not workspace_id:
        raise CLIExecutionError(
            "interactive_terminal",
            1,
            f"Herdr owned pane omitted its workspace ID: {pane_id}",
        )
    _run_herdr_optional_ok_json(herdr, ["pane", "close", pane_id])
    _confirm_herdr_pane_closed(
        herdr,
        pane_id,
        workspace_id=workspace_id,
    )
    return True


def _close_owned_herdr_pane_best_effort(
    herdr: str,
    pane_id: str,
    *,
    origin_pane: str,
    run_id: str,
) -> str | None:
    """Try an ownership-safe close without replacing the caller's main outcome.

    Returns:
        None when close was confirmed, otherwise a diagnostic error string.
    """
    try:
        closed = _close_owned_herdr_pane(
            herdr,
            pane_id,
            origin_pane=origin_pane,
            run_id=run_id,
        )
    except (CLIExecutionError, OSError) as error:
        detail = str(error)
    else:
        if closed:
            return None
        detail = f"Herdr pane ownership was not confirmed at cleanup: {pane_id}"
    _console.warning("Herdr pane cleanup failed: pane=%s detail=%s", pane_id, detail)
    return detail


def _confirm_herdr_pane_closed(herdr: str, pane_id: str, *, workspace_id: str) -> None:
    """Confirm that one closed pane ID is absent from its exact workspace."""
    pane_list = _herdr_result(
        _run_herdr_json(herdr, ["pane", "list", "--workspace", workspace_id]),
        "pane_list",
    )
    panes = pane_list.get("panes")
    if not isinstance(panes, list):
        raise CLIExecutionError("interactive_terminal", 1, "Herdr pane_list omitted panes")
    for item in cast(list[object], panes):
        if not isinstance(item, dict) or not isinstance(item.get("pane_id"), str):
            raise CLIExecutionError(
                "interactive_terminal",
                1,
                "Herdr pane_list returned an invalid pane entry",
            )
        if item["pane_id"] == pane_id:
            raise CLIExecutionError(
                "interactive_terminal",
                1,
                f"Herdr pane close was not confirmed: {pane_id}",
            )


def _write_herdr_metadata(
    destination: Path,
    *,
    herdr_version: str,
    pane_id: str,
    origin_pane: str,
    run_id: str,
    close_on_verdict: bool,
    marker_confirmed: bool,
    pane_read: HerdrPaneRead | None = None,
    process_info: dict[str, object] | None = None,
    layout: HerdrPaneLaunch | None = None,
    terminal_log: Path | None = None,
    close_error: str | None = None,
) -> None:
    """Write a structured Herdr pane diagnostic snapshot."""
    metadata: dict[str, object] = {
        "backend": "herdr",
        "herdr_version": herdr_version,
        "pane_id": pane_id,
        "origin_pane": origin_pane,
        "kaji_run": run_id,
        "marker_confirmed": marker_confirmed,
        "close_on_verdict": close_on_verdict,
        "transcript_kind": "rendered_recent_unwrapped_snapshot",
        "transcript_available": pane_read is not None,
    }
    if pane_read is not None:
        metadata["transcript_truncated"] = pane_read.truncated
        metadata["transcript_revision"] = pane_read.revision
    if process_info is not None:
        metadata["process_info"] = process_info
    if terminal_log is not None:
        diagnostic = read_terminal_diagnostic(terminal_log)
        metadata["terminal_diagnostic"] = {
            "kind": diagnostic.kind,
            "matched_pattern": diagnostic.matched_pattern,
            "clean_excerpt": diagnostic.clean_excerpt,
            "clean_tail": diagnostic.clean_tail,
        }
    if close_error is not None:
        metadata["close_error"] = close_error
    if layout is not None:
        metadata["layout_target_pane"] = origin_pane
        metadata["split_target_pane"] = layout.split_target_pane
        metadata["split_direction"] = layout.direction
        metadata["kaji_agent_panes_before"] = layout.panes_before
        metadata["kaji_agent_panes_pruned"] = layout.panes_pruned
        metadata["kaji_agent_panes_skipped"] = layout.panes_skipped
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
