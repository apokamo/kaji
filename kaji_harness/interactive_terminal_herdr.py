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
import sys
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, NoReturn, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import interactive_terminal_recorder
from .agents import AGENT_CAPABILITIES
from .errors import (
    CLIExecutionError,
    CLINotFoundError,
    HerdrSessionRequiredError,
    SessionResolution,
    StepTimeoutError,
)
from .interactive_terminal_common import (
    _build_wrapper_command,
    _extract_codex_session_id,
    _format_terminal_exit_detail,
    _resolve_abnormal_exit_session,
    _wrapper_path,
    _wrapper_skill_invocation,
    read_terminal_diagnostic,
)
from .models import CLIResult, Step

_console = logging.getLogger("kaji.interactive_terminal")

_MIN_HERDR_VERSION = (0, 8, 2)
_VERDICT_POLL_INTERVAL_SECONDS = 2
_PROCESS_EXIT_CONFIRMATIONS = 3
# Issue #515: Herdr の agent screen manifest 名。recorder を挟むと foreground が python に
# なるため、HERDR_AGENT ヒントで検出対象を伝える。
_HERDR_AGENT_MANIFESTS = {"claude": "claude", "codex": "codex", "antigravity": "agy"}
_TRANSCRIPT_KIND = "raw_pty_recording"
_HERDR_CONTEXT_GUIDANCE = 'Run kaji inside a Herdr pane, or set agent_runner = "headless".'
_HERDR_METADATA_SOURCE = "kaji"
_MAX_VISIBLE_AGENT_PANES = 2
_HERDR_COMMAND_TIMEOUT_SECONDS = 10
_HERDR_LAUNCHER_START_TIMEOUT_SECONDS = 10
_HERDR_LAUNCHER_START_POLL_INTERVAL_SECONDS = 0.1
_HERDR_LAUNCHER_STARTED_FILENAME = "herdr-launcher-started"
# Issue #532: issue 単位の未追跡 session holder 記録（session-state.json と同じディレクトリ）。
HERDR_UNTRACKED_SESSIONS_FILENAME = "herdr-untracked-sessions.json"

UntrackedSessionReason = Literal[
    "marker_failed_close_failed",
    "ownership_changed",
    "close_failed_marker_failed",
    "abnormal_exit_close_failed",
]
SessionMarkerStatus = Literal["marked", "absent", "ownership_changed", "failed"]


class _AttemptDeadlineReachedError(Exception):
    """The attempt deadline passed while waiting for the Herdr launcher to start.

    Internal signal (Issue #421): distinguishes an attempt-budget exhaustion from a
    launcher failure (``CLIExecutionError``, exit 124) so the caller can route it through
    the regular timeout path.
    """


class _HerdrResultEnvelope(BaseModel):
    """Validated common fields for one Herdr CLI result."""

    model_config = ConfigDict(extra="allow", strict=True)

    type: str = Field(min_length=1)


class _HerdrResponseEnvelope(BaseModel):
    """Validated outer envelope for one Herdr CLI JSON response."""

    model_config = ConfigDict(extra="ignore", strict=True)

    result: _HerdrResultEnvelope


class UntrackedSessionEntry(BaseModel):
    """One provider session whose holder pane may survive without a ``kaji_session`` token."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    session_id: str = Field(min_length=1)
    pane_id: str = Field(min_length=1)
    origin_pane: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    reason: UntrackedSessionReason


class UntrackedSessionRegistry(BaseModel):
    """Persisted ``herdr-untracked-sessions.json`` content (Issue #532)."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    entries: list[UntrackedSessionEntry] = Field(default_factory=list)


@dataclass(frozen=True)
class SessionMarkerOutcome:
    """Result of re-reporting the session token after a Codex fresh verdict (Issue #532)."""

    status: SessionMarkerStatus
    error: str | None = None


@dataclass(frozen=True)
class UntrackedRecordOutcome:
    """Whether a possibly untracked session holder was persisted (Issue #532)."""

    recorded: bool
    error: str | None = None


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
    session_panes_closed: list[str] = field(default_factory=list)


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
    env: Mapping[str, str] | None = None,
    deadline_monotonic: float | None = None,
    untracked_sessions_path: Path | None = None,
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
        env: Variables set for the agent process (Issue #407), delivered through the
            ``env K=V ...`` prefix of the wrapper command. ``None`` adds nothing.
        deadline_monotonic: Absolute ``time.monotonic()`` hard deadline of the attempt
            (Issue #421), computed once by the runner so it matches the deadline shown in
            the prompt. ``None`` falls back to ``time.monotonic() + timeout`` measured
            after the launcher start confirmation.
        untracked_sessions_path: Issue-scoped ``herdr-untracked-sessions.json`` (Issue
            #532). Session holders that may survive without a ``kaji_session`` token are
            recorded here, and a resume step checks it before creating a pane. ``None``
            disables recording and checking.

    Returns:
        Empty-output CLI result with the resolved provider session ID.

    Raises:
        CLINotFoundError: Herdr is missing or its version is unsupported.
        HerdrSessionRequiredError: Caller context is not an explicit Herdr pane.
        CLIExecutionError: A Herdr operation fails, the agent exits early, or a resume
            step finds a pane holding its provider session that kaji cannot close.
        StepTimeoutError: The verdict artifact does not appear before timeout, or the
            attempt deadline was consumed before the pane launch / launcher start.
        ValueError: The step has no supported agent.
        FileNotFoundError: The prompt or packaged wrapper is missing.
    """
    _validate_step(step, prompt_path)
    herdr, origin_pane, herdr_version = _preflight_herdr()
    wrapper = _wrapper_path()
    if not wrapper.is_file():
        raise FileNotFoundError(f"interactive terminal wrapper not found: {wrapper}")
    recorder = _recorder_path()
    for required in (Path(sys.executable), recorder):
        if not required.is_file():
            raise FileNotFoundError(f"interactive terminal recorder not found: {required}")

    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        # Issue #421: attempt の期限を preflight で使い切った場合は pane を起動しない。
        # pane も agent session も存在しないため session 解決は試みない。
        raise StepTimeoutError(step.id, timeout, session_resolution=None)

    run_id = str(uuid.uuid4())
    launch_session_id = str(uuid.uuid4()) if step.agent == "claude" and session_id is None else ""
    launch = _launch_herdr_pane(
        herdr,
        origin_pane,
        workdir=workdir,
        resume_session_id=session_id,
        untracked_sessions_path=untracked_sessions_path,
    )
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
            session_id=session_id or launch_session_id or None,
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

    command = _build_recorded_command(
        _build_wrapper_command(
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
            skill_invocation=_wrapper_skill_invocation(step),
        ),
        agent=cast(str, step.agent),
        recorder=recorder,
        terminal_log=terminal_log,
        env=env,
    )
    launcher_budget_exhausted = False
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
            attempt_deadline=deadline_monotonic,
        )
    except _AttemptDeadlineReachedError:
        # launcher 固有の失敗ではなく attempt の期限消費。verdict 待ちループの timeout
        # 経路と同じ後処理（session 解決 / pane close / metadata）へ進む。
        launcher_budget_exhausted = True
    except CLIExecutionError:
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
            terminal_log=terminal_log,
            include_diagnostic=True,
            close_error=close_error,
            layout=launch,
        )
        raise

    if launcher_budget_exhausted:
        _raise_step_timeout(
            herdr,
            step=step,
            timeout=timeout,
            pane_id=pane_id,
            origin_pane=origin_pane,
            run_id=run_id,
            herdr_version=herdr_version,
            metadata_path=metadata_path,
            terminal_log=terminal_log,
            prompt_path=prompt_path,
            verdict_path=verdict_path,
            session_id=session_id,
            launch_session_id=launch_session_id,
            close_on_verdict=close_on_verdict,
            layout=launch,
            untracked_sessions_path=untracked_sessions_path,
        )

    _console.info(
        "pane launched: step=%s agent=%s pane=%s timeout=%ds verdict=%s backend=herdr",
        step.id,
        step.agent,
        pane_id,
        timeout,
        verdict_path,
    )

    deadline = deadline_monotonic if deadline_monotonic is not None else time.monotonic() + timeout
    shell_only_observations = 0
    process_info: dict[str, object] | None = None
    try:
        while time.monotonic() < deadline:
            if verdict_path.is_file():
                result_session_id = session_id or launch_session_id or None
                if result_session_id is None and step.agent == "codex":
                    result_session_id = _extract_codex_session_id(
                        terminal_log,
                        prompt_path=prompt_path,
                        verdict_path=verdict_path,
                    )
                # 既知 session の pane も ownership 変更で kaji_session を失いうるため、
                # session が分かる全 pane を追跡対象にする（PR #533 review point 1）。
                close_error, marker, tracking = _finish_verdict_pane(
                    herdr,
                    pane_id=pane_id,
                    origin_pane=origin_pane,
                    run_id=run_id,
                    step_id=step.id,
                    close_on_verdict=close_on_verdict,
                    tracked_session_id=result_session_id,
                    untracked_sessions_path=untracked_sessions_path,
                )
                _write_herdr_metadata(
                    metadata_path,
                    herdr_version=herdr_version,
                    pane_id=pane_id,
                    origin_pane=origin_pane,
                    run_id=run_id,
                    close_on_verdict=close_on_verdict,
                    marker_confirmed=True,
                    terminal_log=terminal_log,
                    close_error=close_error,
                    layout=launch,
                    session_marker=marker,
                    untracked=tracking,
                )
                return CLIResult(full_output="", session_id=result_session_id)

            process_info = _get_herdr_process_info(herdr, pane_id)
            liveness = _classify_herdr_process_liveness(process_info)
            if liveness == "confirmed_shell_only":
                shell_only_observations += 1
                if shell_only_observations >= _PROCESS_EXIT_CONFIRMATIONS:
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
                    tracking = _track_abnormal_exit_holder(
                        step=step,
                        pane_id=pane_id,
                        origin_pane=origin_pane,
                        run_id=run_id,
                        resolved=resolved,
                        close_error=close_error,
                        untracked_sessions_path=untracked_sessions_path,
                    )
                    _write_herdr_metadata(
                        metadata_path,
                        herdr_version=herdr_version,
                        pane_id=pane_id,
                        origin_pane=origin_pane,
                        run_id=run_id,
                        close_on_verdict=close_on_verdict,
                        marker_confirmed=True,
                        process_info=process_info,
                        terminal_log=terminal_log,
                        include_diagnostic=True,
                        close_error=close_error,
                        layout=launch,
                        untracked=tracking,
                    )
                    detail, kind = _recorded_exit_detail(
                        terminal_log,
                        prefix="Herdr pane returned to its shell before writing verdict.yaml",
                    )
                    # Issue #527: no_pattern の detail は原因行の要約を含むので切らずに載せる。
                    raise CLIExecutionError(
                        step.id,
                        1,
                        detail,
                        session_resolution=resolved,
                        summary=detail if kind == "no_pattern" else None,
                    )
            else:
                shell_only_observations = 0
            time.sleep(_VERDICT_POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        # Preserve the live pane for inspection and leave enough ownership metadata for
        # recovery to report the orphan. Metadata failure must not mask Ctrl-C.
        try:
            _write_herdr_metadata(
                metadata_path,
                herdr_version=herdr_version,
                pane_id=pane_id,
                origin_pane=origin_pane,
                run_id=run_id,
                close_on_verdict=close_on_verdict,
                marker_confirmed=True,
                process_info=process_info,
                terminal_log=terminal_log,
                layout=launch,
            )
        except OSError as exc:
            _console.warning("orphan pane metadata snapshot failed: %s", exc)
        raise

    _raise_step_timeout(
        herdr,
        step=step,
        timeout=timeout,
        pane_id=pane_id,
        origin_pane=origin_pane,
        run_id=run_id,
        herdr_version=herdr_version,
        metadata_path=metadata_path,
        terminal_log=terminal_log,
        prompt_path=prompt_path,
        verdict_path=verdict_path,
        session_id=session_id,
        launch_session_id=launch_session_id,
        close_on_verdict=close_on_verdict,
        layout=launch,
        untracked_sessions_path=untracked_sessions_path,
    )


def _finish_verdict_pane(
    herdr: str,
    *,
    pane_id: str,
    origin_pane: str,
    run_id: str,
    step_id: str,
    close_on_verdict: bool,
    tracked_session_id: str | None,
    untracked_sessions_path: Path | None,
) -> tuple[str | None, SessionMarkerOutcome | None, UntrackedRecordOutcome | None]:
    """Close or keep the verdict pane and keep its provider session discoverable.

    ``tracked_session_id`` is the provider session the pane holds, whenever it is known.
    A Codex fresh pane's initial marker could not carry ``kaji_session``, and a pane started
    with a known session can lose that token when its ownership changes, so every known
    session is tracked. When the pane stays alive (close disabled or close failed), the
    token is re-reported only after the current ownership is confirmed (design review R1).
    A holder that may survive without the token is recorded in the untracked registry
    (design review R2). No branch replaces the verdict or the resolved session.

    Returns:
        ``(close_error, session_marker, untracked)`` diagnostics for ``pane-metadata.json``.
    """
    close_error: str | None = None
    marker: SessionMarkerOutcome | None = None
    reason: UntrackedSessionReason | None = None
    if close_on_verdict:
        close_error = _close_owned_herdr_pane_best_effort(
            herdr, pane_id, origin_pane=origin_pane, run_id=run_id
        )
        if close_error is not None and tracked_session_id:
            marker = _report_session_token_if_owned(
                herdr,
                pane_id,
                origin_pane=origin_pane,
                run_id=run_id,
                step_id=step_id,
                session_id=tracked_session_id,
            )
            if marker.status == "failed":
                reason = "close_failed_marker_failed"
            elif marker.status == "ownership_changed":
                reason = "ownership_changed"
    elif tracked_session_id:
        marker = _report_session_token_if_owned(
            herdr,
            pane_id,
            origin_pane=origin_pane,
            run_id=run_id,
            step_id=step_id,
            session_id=tracked_session_id,
        )
        if marker.status == "failed":
            # 識別できない holder を残すと lock 待ちが再発するため、false でも閉じる。
            close_error = _close_owned_herdr_pane_best_effort(
                herdr, pane_id, origin_pane=origin_pane, run_id=run_id
            )
            if close_error is not None:
                reason = "marker_failed_close_failed"
        elif marker.status == "ownership_changed":
            reason = "ownership_changed"
    if marker is not None and marker.status in ("failed", "ownership_changed"):
        _console.warning(
            "Herdr session token was not reported: pane=%s status=%s detail=%s",
            pane_id,
            marker.status,
            marker.error,
        )
    untracked: UntrackedRecordOutcome | None = None
    if reason is not None and tracked_session_id:
        untracked = _record_untracked_session(
            untracked_sessions_path,
            UntrackedSessionEntry(
                session_id=tracked_session_id,
                pane_id=pane_id,
                origin_pane=origin_pane,
                run_id=run_id,
                step_id=step_id,
                reason=reason,
            ),
        )
    return close_error, marker, untracked


def _track_abnormal_exit_holder(
    *,
    step: Step,
    pane_id: str,
    origin_pane: str,
    run_id: str,
    resolved: SessionResolution,
    close_error: str | None,
    untracked_sessions_path: Path | None,
) -> UntrackedRecordOutcome | None:
    """Record a holder left by a timeout / shell-return close failure.

    A Codex fresh pane has no ``kaji_session`` token, and a pane started with a known
    session may have lost it when its ownership changed (a likely reason the close failed),
    so every resolved session is recorded. A record for a pane that still carries the
    token is resolved by the token scan at the next resume.
    """
    if close_error is None or not resolved.session_id:
        return None
    return _record_untracked_session(
        untracked_sessions_path,
        UntrackedSessionEntry(
            session_id=resolved.session_id,
            pane_id=pane_id,
            origin_pane=origin_pane,
            run_id=run_id,
            step_id=step.id,
            reason="abnormal_exit_close_failed",
        ),
    )


def _load_untracked_sessions(path: Path) -> UntrackedSessionRegistry:
    """Load the untracked holder registry, treating a missing file as empty.

    Raises:
        CLIExecutionError: The file cannot be read, is not UTF-8, or fails Pydantic
            validation. An unreadable record must not be ignored, otherwise the lock
            wait can recur.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return UntrackedSessionRegistry()
    except (OSError, UnicodeDecodeError) as error:
        raise CLIExecutionError(
            "interactive_terminal",
            1,
            f"Herdr untracked session record cannot be read: {path}: {error}",
        ) from error
    try:
        return UntrackedSessionRegistry.model_validate_json(text)
    except ValidationError as error:
        raise CLIExecutionError(
            "interactive_terminal",
            1,
            f"Herdr untracked session record is invalid: {path}: {error}",
        ) from error


def _save_untracked_sessions(path: Path, registry: UntrackedSessionRegistry) -> None:
    """Atomically replace the untracked holder registry.

    Raises:
        OSError: The temporary file cannot be written or moved into place.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            registry.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def _record_untracked_session(
    path: Path | None, entry: UntrackedSessionEntry
) -> UntrackedRecordOutcome:
    """Append one possibly untracked holder without replacing the step outcome."""
    if path is None:
        return UntrackedRecordOutcome(recorded=False)
    try:
        registry = _load_untracked_sessions(path)
        _save_untracked_sessions(path, UntrackedSessionRegistry(entries=[*registry.entries, entry]))
    except (CLIExecutionError, OSError) as error:
        detail = str(error)
        _console.warning(
            "Herdr untracked session record failed: pane=%s session=%s detail=%s",
            entry.pane_id,
            entry.session_id,
            detail,
        )
        return UntrackedRecordOutcome(recorded=False, error=detail)
    _console.warning(
        "Herdr pane may still hold provider session: pane=%s session=%s reason=%s record=%s",
        entry.pane_id,
        entry.session_id,
        entry.reason,
        path,
    )
    return UntrackedRecordOutcome(recorded=True)


def _raise_step_timeout(
    herdr: str,
    *,
    step: Step,
    timeout: int,
    pane_id: str,
    origin_pane: str,
    run_id: str,
    herdr_version: str,
    metadata_path: Path,
    terminal_log: Path,
    prompt_path: Path,
    verdict_path: Path,
    session_id: str | None,
    launch_session_id: str,
    close_on_verdict: bool,
    layout: HerdrPaneLaunch,
    untracked_sessions_path: Path | None = None,
) -> NoReturn:
    """Run the timeout cleanup (session resolution, pane close, metadata) and raise.

    Shared by the verdict-wait loop and the launcher start wait so that an attempt
    deadline reached in either place yields the same ``StepTimeoutError`` (Issue #403 /
    #421). The session is resolved once, before the pane is closed.

    Raises:
        StepTimeoutError: Always.
    """
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
    tracking = _track_abnormal_exit_holder(
        step=step,
        pane_id=pane_id,
        origin_pane=origin_pane,
        run_id=run_id,
        resolved=resolved,
        close_error=close_error,
        untracked_sessions_path=untracked_sessions_path,
    )
    _write_herdr_metadata(
        metadata_path,
        herdr_version=herdr_version,
        pane_id=pane_id,
        origin_pane=origin_pane,
        run_id=run_id,
        close_on_verdict=close_on_verdict,
        marker_confirmed=True,
        terminal_log=terminal_log,
        include_diagnostic=True,
        close_error=close_error,
        layout=layout,
        untracked=tracking,
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
            "CLI 'herdr' not found. Install Herdr (>= 0.8.2) or use the headless runner "
            '(agent_runner = "headless").'
        )
    return herdr


def _resolve_herdr_origin() -> str:
    """Resolve the explicit caller pane from the Herdr environment.

    Raises:
        HerdrSessionRequiredError: The process is outside Herdr or lacks a pane ID.
    """
    if os.environ.get("HERDR_ENV") != "1":
        raise HerdrSessionRequiredError(
            "Herdr interactive terminal backend must run inside Herdr (HERDR_ENV=1). "
            f"{_HERDR_CONTEXT_GUIDANCE}"
        )
    origin_pane = os.environ.get("HERDR_PANE_ID")
    if not origin_pane:
        raise HerdrSessionRequiredError(
            "HERDR_PANE_ID is not set; cannot identify the caller pane safely. "
            f"{_HERDR_CONTEXT_GUIDANCE}"
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


def _launch_herdr_pane(
    herdr: str,
    origin_pane: str,
    *,
    workdir: Path,
    resume_session_id: str | None = None,
    untracked_sessions_path: Path | None = None,
) -> HerdrPaneLaunch:
    """Close resume-session holders, prune owned panes, split, and return launch metadata.

    Args:
        herdr: Resolved executable path.
        origin_pane: Caller pane that owns the managed panes.
        workdir: cwd for the new pane.
        resume_session_id: Provider session the new pane resumes (Issue #532). Panes
            still holding it are handled before the existing list / prune.
        untracked_sessions_path: Untracked holder registry checked for a resume step.

    Raises:
        CLIExecutionError: A Herdr operation fails, or a holder of
            ``resume_session_id`` cannot be closed safely (no pane is split then).
    """
    session_panes_closed: list[str] = []
    if resume_session_id:
        session_panes_closed = _close_resume_session_holders(
            herdr,
            origin_pane,
            resume_session_id=resume_session_id,
            untracked_sessions_path=untracked_sessions_path,
        )
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
        session_panes_closed=session_panes_closed,
    )


def _list_all_herdr_panes(herdr: str) -> dict[str, dict[str, object]]:
    """Return every Herdr pane across workspaces keyed by pane ID.

    Raises:
        CLIExecutionError: The request fails or returns malformed pane entries.
    """
    pane_list = _herdr_result(_run_herdr_json(herdr, ["pane", "list"]), "pane_list")
    panes = pane_list.get("panes")
    if not isinstance(panes, list):
        raise CLIExecutionError("interactive_terminal", 1, "Herdr pane_list omitted panes")
    listed: dict[str, dict[str, object]] = {}
    for item in cast(list[object], panes):
        if not isinstance(item, dict) or not isinstance(item.get("pane_id"), str):
            raise CLIExecutionError(
                "interactive_terminal", 1, "Herdr pane_list returned an invalid pane entry"
            )
        listed[cast(str, item["pane_id"])] = cast(dict[str, object], item)
    return listed


def _pane_tokens(pane: Mapping[str, object]) -> dict[str, object]:
    """Return the pane's token mapping, or an empty mapping when absent."""
    tokens = pane.get("tokens")
    return cast(dict[str, object], tokens) if isinstance(tokens, dict) else {}


def _session_holder_error(
    *,
    pane_id: str,
    session_id: str,
    detail: str,
    record_path: Path | None = None,
) -> CLIExecutionError:
    """Build the actionable error for a holder kaji must not or could not close."""
    record = f" (recorded in {record_path})" if record_path is not None else ""
    return CLIExecutionError(
        "interactive_terminal",
        1,
        (
            f"Herdr pane {pane_id} may still have provider session {session_id} open"
            f"{record}: {detail}; close it with `herdr pane close {pane_id}` and rerun "
            "the step."
        ),
    )


def _close_resume_session_holders(
    herdr: str,
    origin_pane: str,
    *,
    resume_session_id: str,
    untracked_sessions_path: Path | None,
) -> list[str]:
    """Close kaji-owned panes holding the resume session before a new pane starts.

    ``kaji_session`` only selects close candidates; ownership is still decided by
    ``_close_owned_herdr_pane`` (exact pane, origin / run re-check, removal confirmed).
    A holder from another origin, the origin pane itself, or one that cannot be closed
    fails the step before any pane is split. The untracked registry is checked next.

    Returns:
        Pane IDs closed for the resume, in close order.

    Raises:
        CLIExecutionError: A holder cannot be closed safely, or Herdr / the registry fails.
    """
    panes = _list_all_herdr_panes(herdr)
    closed: list[str] = []
    for pane_id, pane in panes.items():
        tokens = _pane_tokens(pane)
        if tokens.get("kaji_session") != resume_session_id:
            continue
        holder_origin = tokens.get("kaji_origin")
        holder_run = tokens.get("kaji_run")
        if (
            pane_id == origin_pane
            or holder_origin != origin_pane
            or not isinstance(holder_run, str)
            or not holder_run
        ):
            raise _session_holder_error(
                pane_id=pane_id,
                session_id=resume_session_id,
                detail=f"it is not owned by this kaji origin {origin_pane} "
                f"(kaji origin {holder_origin})",
            )
        _close_session_holder(
            herdr,
            pane_id,
            origin_pane=origin_pane,
            run_id=holder_run,
            session_id=resume_session_id,
        )
        closed.append(pane_id)

    if untracked_sessions_path is None:
        return closed
    registry = _load_untracked_sessions(untracked_sessions_path)
    matching = [entry for entry in registry.entries if entry.session_id == resume_session_id]
    if not matching:
        return closed
    resolved: list[UntrackedSessionEntry] = []
    for entry in matching:
        holder = None if entry.pane_id in closed else panes.get(entry.pane_id)
        if holder is None:
            resolved.append(entry)
            continue
        tokens = _pane_tokens(holder)
        if (
            entry.origin_pane != origin_pane
            or tokens.get("kaji_origin") != entry.origin_pane
            or tokens.get("kaji_run") != entry.run_id
        ):
            raise _session_holder_error(
                pane_id=entry.pane_id,
                session_id=resume_session_id,
                detail=f"its ownership could not be confirmed (reason: {entry.reason})",
                record_path=untracked_sessions_path,
            )
        _close_session_holder(
            herdr,
            entry.pane_id,
            origin_pane=origin_pane,
            run_id=entry.run_id,
            session_id=resume_session_id,
            record_path=untracked_sessions_path,
        )
        closed.append(entry.pane_id)
        resolved.append(entry)
    remaining = [entry for entry in registry.entries if entry not in resolved]
    try:
        _save_untracked_sessions(
            untracked_sessions_path, UntrackedSessionRegistry(entries=remaining)
        )
    except OSError as error:
        # pane が消えていることは確認済み。次回の検査も同じ判定になるので step は続ける。
        _console.warning(
            "Herdr untracked session record cleanup failed: path=%s detail=%s",
            untracked_sessions_path,
            error,
        )
    return closed


def _close_session_holder(
    herdr: str,
    pane_id: str,
    *,
    origin_pane: str,
    run_id: str,
    session_id: str,
    record_path: Path | None = None,
) -> None:
    """Close one session holder with the ownership-safe close or raise an actionable error.

    Raises:
        CLIExecutionError: Ownership was not confirmed or the close was not confirmed.
    """
    try:
        closed = _close_owned_herdr_pane(herdr, pane_id, origin_pane=origin_pane, run_id=run_id)
    except CLIExecutionError as error:
        raise _session_holder_error(
            pane_id=pane_id,
            session_id=session_id,
            detail=f"closing it failed ({error.stderr})",
            record_path=record_path,
        ) from error
    if not closed:
        raise _session_holder_error(
            pane_id=pane_id,
            session_id=session_id,
            detail="its ownership was not confirmed at close",
            record_path=record_path,
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
    session_id: str | None = None,
) -> list[str]:
    """Build the source-scoped metadata request marking kaji pane ownership.

    The request deliberately omits ``--ttl-ms``. Herdr then retains these
    ownership tokens until they are replaced, explicitly cleared, or the pane
    closes, so a later cleanup request can re-read them safely. ``session_id`` adds
    ``kaji_session`` (Issue #532), which only selects resume-time close candidates.
    """
    session_tokens = ["--token", f"kaji_session={session_id}"] if session_id else []
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
        *session_tokens,
    ]


def _expected_marker_tokens(
    *, origin_pane: str, run_id: str, step_id: str, session_id: str | None
) -> dict[str, str]:
    """Return the token values a confirmed marker must expose."""
    expected = {"kaji_origin": origin_pane, "kaji_run": run_id, "kaji_step": step_id}
    if session_id:
        expected["kaji_session"] = session_id
    return expected


def _mark_herdr_pane(
    herdr: str,
    pane_id: str,
    *,
    origin_pane: str,
    run_id: str,
    step_id: str,
    session_id: str | None = None,
) -> None:
    """Attach and independently confirm ownership of a newly created pane.

    Only for the initial marker; it writes before confirming, so it must not be reused
    on a pane whose ownership may have changed (see ``_report_session_token_if_owned``).
    """
    argv = _build_herdr_marker_argv(
        herdr,
        pane_id=pane_id,
        origin_pane=origin_pane,
        run_id=run_id,
        step_id=step_id,
        session_id=session_id,
    )
    _run_herdr_optional_ok_json(herdr, argv[1:])
    pane = _get_herdr_pane(herdr, pane_id)
    tokens = pane.get("tokens")
    expected_tokens = _expected_marker_tokens(
        origin_pane=origin_pane, run_id=run_id, step_id=step_id, session_id=session_id
    )
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


def _report_session_token_if_owned(
    herdr: str,
    pane_id: str,
    *,
    origin_pane: str,
    run_id: str,
    step_id: str,
    session_id: str,
) -> SessionMarkerOutcome:
    """Re-report all ownership tokens plus ``kaji_session`` only on a still-owned pane.

    The current origin / run are checked before writing so a pane whose ownership
    changed is never claimed back (design review R1). The check-then-write window is the
    same one ``_close_owned_herdr_pane`` already accepts (Herdr has no compare-and-set).

    Returns:
        ``marked`` when all four tokens were confirmed, ``absent`` when the pane is gone,
        ``ownership_changed`` when its tokens are not ours (nothing written), otherwise
        ``failed`` (nothing confirmed).
    """
    try:
        pane = _get_herdr_pane(herdr, pane_id)
    except (CLIExecutionError, OSError) as error:
        try:
            listed = _list_all_herdr_panes(herdr)
        except (CLIExecutionError, OSError) as list_error:
            return SessionMarkerOutcome(
                "failed", f"{_herdr_error_detail(error)}; {_herdr_error_detail(list_error)}"
            )
        if pane_id not in listed:
            return SessionMarkerOutcome("absent")
        return SessionMarkerOutcome("failed", _herdr_error_detail(error))
    tokens = _pane_tokens(pane)
    if (
        pane.get("pane_id") != pane_id
        or tokens.get("kaji_origin") != origin_pane
        or tokens.get("kaji_run") != run_id
    ):
        return SessionMarkerOutcome(
            "ownership_changed",
            f"Herdr pane ownership changed before the session token was reported: {pane_id}",
        )
    try:
        _mark_herdr_pane(
            herdr,
            pane_id,
            origin_pane=origin_pane,
            run_id=run_id,
            step_id=step_id,
            session_id=session_id,
        )
    except (CLIExecutionError, OSError) as error:
        # OSError (e.g. the Herdr executable vanished) must not replace the verdict either.
        return SessionMarkerOutcome("failed", _herdr_error_detail(error))
    return SessionMarkerOutcome("marked")


def _herdr_error_detail(error: CLIExecutionError | OSError) -> str:
    """Return a diagnostic string for a failed Herdr call."""
    if isinstance(error, CLIExecutionError):
        return error.stderr
    return str(error)


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


def _wait_for_herdr_launcher_start(
    herdr: str,
    pane_id: str,
    started_path: Path,
    *,
    attempt_deadline: float | None = None,
) -> None:
    """Wait boundedly for proof that the dispatched launcher executed.

    Shell-only observations before the marker are startup state, not agent exit. Process
    information is sampled only to make timeout diagnostics actionable; the atomically
    published filesystem marker is the start authority.

    The wait is capped by ``min(launcher limit, attempt_deadline)`` (Issue #421). When the
    attempt deadline comes first the attempt budget is spent, which is not a launcher
    failure, so ``_AttemptDeadlineReachedError`` is raised instead.

    Raises:
        CLIExecutionError: The marker does not appear within the launcher's own limit.
        _AttemptDeadlineReachedError: The attempt deadline passed before the marker
            appeared and before the launcher's own limit.
    """
    launcher_deadline = time.monotonic() + _HERDR_LAUNCHER_START_TIMEOUT_SECONDS
    attempt_first = attempt_deadline is not None and attempt_deadline <= launcher_deadline
    limit = (
        attempt_deadline if attempt_first and attempt_deadline is not None else launcher_deadline
    )
    while True:
        if started_path.is_file():
            return
        now = time.monotonic()
        if now >= limit:
            break
        time.sleep(min(_HERDR_LAUNCHER_START_POLL_INTERVAL_SECONDS, limit - now))
    if attempt_first:
        raise _AttemptDeadlineReachedError

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


AgentPaneState = Literal["active", "shell_only", "absent", "not_owned", "unknown"]


def inspect_agent_pane(
    pane_id: str, *, origin_pane: str | None, run_id: str | None
) -> AgentPaneState:
    """Issue #540: 失敗 step の pane に agent が残っているかを読み取り専用で照会する。

    ``pane get`` と ``pane process-info`` だけを呼び、pane を閉じたり印を付けたりしない。

    Args:
        pane_id: ``pane-metadata.json`` の ``pane_id``。
        origin_pane: 同 ``origin_pane``（所有確認に使う）。
        run_id: 同 ``kaji_run``（所有確認に使う）。

    Returns:
        ``active``（kaji 所有で前景に agent プロセス）/ ``shell_only`` / ``absent``（pane が
        無い）/ ``not_owned``（tokens が一致しない別の pane）/ ``unknown``（照会不能・判定不能）。
    """
    try:
        herdr = _resolve_herdr()
    except CLINotFoundError:
        return "unknown"
    try:
        pane = _get_herdr_pane(herdr, pane_id)
    except CLIExecutionError as exc:
        return "absent" if "not found" in str(exc).lower() else "unknown"
    except OSError:
        return "unknown"
    tokens = pane.get("tokens")
    if pane.get("pane_id") != pane_id or not isinstance(tokens, dict):
        return "not_owned"
    if tokens.get("kaji_origin") != origin_pane or tokens.get("kaji_run") != run_id:
        return "not_owned"
    try:
        process_info = _get_herdr_process_info(herdr, pane_id)
    except (CLIExecutionError, OSError):
        return "unknown"
    liveness = _classify_herdr_process_liveness(process_info)
    if liveness == "active":
        return "active"
    if liveness == "confirmed_shell_only":
        return "shell_only"
    return "unknown"


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


def _recorder_path() -> Path:
    """Return the packaged PTY recorder started by path inside the pane (Issue #515)."""
    return Path(interactive_terminal_recorder.__file__).resolve()


def _build_recorded_command(
    wrapper_command: str,
    *,
    agent: str,
    recorder: Path,
    terminal_log: Path,
    env: Mapping[str, str] | None,
) -> str:
    """Prefix the wrapper command with the PTY recorder that writes ``terminal_log``.

    The order is attempt env (Issue #407) → ``HERDR_AGENT`` → recorder → wrapper argv.
    ``HERDR_AGENT`` tells Herdr which agent screen manifest to use although the pane's
    foreground process is the recorder; the recorder removes it before exec'ing the
    wrapper so it does not leak into the agent or nested kaji panes.

    Raises:
        ValueError: ``agent`` has no Herdr manifest.
    """
    manifest = _HERDR_AGENT_MANIFESTS.get(agent)
    if manifest is None:
        raise ValueError(f"no Herdr agent manifest for agent: {agent}")
    assignments = [f"{key}={value}" for key, value in (env or {}).items()]
    prefix = shlex.join(
        [
            "env",
            *assignments,
            f"{interactive_terminal_recorder.HERDR_AGENT_ENV}={manifest}",
            sys.executable,
            "-I",
            str(recorder),
            str(terminal_log),
            "--",
        ]
    )
    return f"{prefix} {wrapper_command}"


def _recorded_exit_detail(terminal_log: Path, *, prefix: str) -> tuple[str, str]:
    """Early-exit detail from the recording, flagging recordings of unknown completeness.

    Returns:
        ``(detail, kind)``. ``kind`` is the transcript diagnostic kind the detail was built
        from (read once), so the caller can tell the ``no_pattern`` path (Issue #527).
    """
    diagnostic = read_terminal_diagnostic(terminal_log)
    detail = _format_terminal_exit_detail(diagnostic, log_name=terminal_log.name, prefix=prefix)
    state, _error = interactive_terminal_recorder.read_recorder_state(terminal_log)
    if state in ("incomplete", "unknown"):
        detail += f"; transcript may be incomplete (recorder state: {state})"
    return detail, diagnostic.kind


def _transcript_metadata(terminal_log: Path) -> dict[str, object]:
    """Describe the recorder's ``terminal.log`` without raising (Issue #515)."""
    try:
        available = terminal_log.is_file()
        size = terminal_log.stat().st_size if available else None
    except OSError:
        available, size = False, None
    state, error = interactive_terminal_recorder.read_recorder_state(terminal_log)
    metadata: dict[str, object] = {
        "transcript_kind": _TRANSCRIPT_KIND,
        "transcript_available": available,
        "transcript_recorder_state": state,
    }
    if available:
        truncated: bool | None = None
        if state in ("recording", "complete"):
            truncated = False
        elif state == "incomplete":
            truncated = True
        metadata["transcript_truncated"] = truncated
        metadata["transcript_revision"] = None
        metadata["transcript_bytes"] = size
    if state == "incomplete" and error is not None:
        metadata["transcript_error"] = error
    return metadata


def _write_herdr_metadata(
    destination: Path,
    *,
    herdr_version: str,
    pane_id: str,
    origin_pane: str,
    run_id: str,
    close_on_verdict: bool,
    marker_confirmed: bool,
    process_info: dict[str, object] | None = None,
    layout: HerdrPaneLaunch | None = None,
    terminal_log: Path | None = None,
    include_diagnostic: bool = False,
    close_error: str | None = None,
    session_marker: SessionMarkerOutcome | None = None,
    untracked: UntrackedRecordOutcome | None = None,
) -> None:
    """Write a structured Herdr pane diagnostic snapshot.

    ``terminal_log`` adds the transcript metadata of the recorder's log; it is omitted
    when the wrapper never started. ``include_diagnostic`` additionally records the
    early-exit ``terminal_diagnostic`` (launcher failure, shell return, timeout).
    """
    metadata: dict[str, object] = {
        "backend": "herdr",
        "herdr_version": herdr_version,
        "pane_id": pane_id,
        "origin_pane": origin_pane,
        "kaji_run": run_id,
        "marker_confirmed": marker_confirmed,
        "close_on_verdict": close_on_verdict,
        "untracked_session_recorded": untracked is not None and untracked.recorded,
    }
    if untracked is not None and untracked.error is not None:
        metadata["untracked_record_error"] = untracked.error
    if session_marker is not None:
        metadata["session_marker"] = {
            "status": session_marker.status,
            "error": session_marker.error,
        }
    if terminal_log is not None:
        metadata.update(_transcript_metadata(terminal_log))
    if process_info is not None:
        metadata["process_info"] = process_info
    if terminal_log is not None and include_diagnostic:
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
        metadata["kaji_session_panes_closed"] = layout.session_panes_closed
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
