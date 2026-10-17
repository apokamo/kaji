"""Interactive terminal runner (tmux backend).

This module contains the tmux implementation of the interactive terminal runner.
It starts a real interactive agent CLI (``claude`` / ``codex`` / ``antigravity``) and waits for
the agent-written ``verdict.yaml`` artifact. It
intentionally avoids parsing stdout: completion is decided by the
artifact-primary verdict resolution introduced in Issue #220.

Tmux remains the default backend (ADR 007 v4, Issue #396). When selected, ``kaji run`` must run
inside a tmux session; the runner adds a pane to the current window,
records the transcript with ``tmux pipe-pane``, decides liveness via
``#{pane_dead}``, and cleans up with ``tmux kill-pane``. There is no ``/proc``
scan and no util-linux ``script(1)`` dependency, so Linux and macOS share one
implementation.

Pane placement (Issue #238): the first agent pane is created to the right of
the origin pane (``split-window -h``); subsequent agent panes split the right
column vertically (``split-window -v``) so the origin/agent widths stay stable
instead of shrinking each step. The right column keeps at most
``_MAX_VISIBLE_AGENT_PANES`` kaji-managed agent panes — when a new pane would
exceed that, the oldest (top-most) managed pane is killed first. kaji-created
panes are tagged with a ``@kaji_interactive_terminal`` pane option so manually
created panes are never pruned.

The single completion trigger is the appearance of ``verdict.yaml``; the agent
process is not waited on. The post-verdict ``kill-pane`` is best-effort cleanup
with no latency contract.
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
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .agents import AGENT_CAPABILITIES
from .errors import (
    CLIExecutionError,
    CLINotFoundError,
    StepTimeoutError,
    TmuxSessionRequiredError,
)
from .interactive_terminal_common import (
    _build_wrapper_command,
    _extract_codex_session_id,
    _resolve_abnormal_exit_session,
    _terminal_exit_detail,
    _wrapper_path,
    _wrapper_skill_invocation,
    read_terminal_diagnostic,
)
from .models import CLIResult, Step

# Issue #235: 起動コンソール向け progress logger（kaji.* 名前空間）。
_console = logging.getLogger("kaji.interactive_terminal")
_SESSION_ID_GRACE_SECONDS = 5.0
_VERDICT_POLL_INTERVAL_SECONDS = 2

# marker; they were added in tmux 3.1, so the minimum is raised from 3.0.
_MIN_TMUX_VERSION = (3, 1)
# Pane option name marking a pane as kaji-created (value: ``origin=<pane_id>``).
_KAJI_PANE_OPTION = "@kaji_interactive_terminal"
# Max kaji-managed agent panes kept visible in the right column at once.
_MAX_VISIBLE_AGENT_PANES = 2


@dataclass(frozen=True)
class KajiAgentPane:
    """A kaji-created agent pane discovered via ``tmux list-panes``.

    ``pane_top`` is the y-offset used to order panes: in the right-column layout
    a newly created pane is added below, so a smaller ``pane_top`` means an older
    pane. ``created_at`` wall-clock is intentionally *not* used (NTP corrections
    can run it backwards), so the geometry is the single ordering source.
    """

    pane_id: str
    pane_top: int
    pane_left: int
    pane_width: int


@dataclass(frozen=True)
class _PaneLaunch:
    """Placement outcome of a single agent-pane launch (diagnostic metadata)."""

    pane_id: str
    split_target_pane: str
    split_flag: str
    panes_before: list[str] = field(default_factory=list)
    panes_pruned: list[str] = field(default_factory=list)


def _build_tmux_split_argv(
    tmux: str,
    wrapper: Path,
    *,
    split_target_pane: str,
    split_flag: str,
    agent: str,
    prompt_path: Path,
    verdict_path: Path,
    workdir: Path,
    resume_session_id: str,
    launch_session_id: str,
    model: str,
    effort: str,
    execution_policy: str = "auto",
    env: Mapping[str, str] | None = None,
    skill_invocation: str = "",
) -> list[str]:
    """Assemble the ``tmux split-window`` argv.

    ``-d`` keeps focus on the current pane; ``-P -F '#{pane_id}'`` prints the
    created pane id, which becomes the lifecycle handle for polling, transcript,
    and cleanup. The split direction and target are chosen by the caller (Issue
    #238): the first agent pane splits the origin pane horizontally (``-h``,
    right column), and later panes split the right column's bottom pane
    vertically (``-v``) so widths stay stable.

    Args:
        split_target_pane: The ``-t`` target the new pane splits off from.
        split_flag: ``"-h"`` (right) or ``"-v"`` (below). Any other value is a
            programming error.

    Raises:
        ValueError: ``split_flag`` is neither ``"-h"`` nor ``"-v"``.
    """
    if split_flag not in {"-h", "-v"}:
        raise ValueError(f"split_flag must be '-h' or '-v', got {split_flag!r}")
    return [
        tmux,
        "split-window",
        "-d",
        split_flag,
        "-P",
        "-F",
        "#{pane_id}",
        "-t",
        split_target_pane,
        _build_wrapper_command(
            wrapper,
            agent=agent,
            prompt_path=prompt_path,
            verdict_path=verdict_path,
            workdir=workdir,
            resume_session_id=resume_session_id,
            launch_session_id=launch_session_id,
            model=model,
            effort=effort,
            execution_policy=execution_policy,
            env=env,
            skill_invocation=skill_invocation,
        ),
    ]


def execute_interactive_terminal_tmux(
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
) -> CLIResult:
    """Start a real interactive CLI in a tmux pane and wait for ``verdict.yaml``.

    Args:
        step: The workflow step with an interactive-terminal capable agent.
        prompt_path: Absolute path to the attempt's ``prompt.txt``.
        verdict_path: Absolute path the agent must write ``verdict.yaml`` to.
        workdir: Trusted project worktree used as cwd / ``--cd``. Resolved by
            ``runner.py`` to the same ``effective_workdir`` as the headless
            runner (backend-independent).
        timeout: Seconds to wait for ``verdict.yaml`` before failing.
        session_id: Previous session id to resume (``None`` → fresh run).
        close_on_verdict: ``kill-pane`` after the verdict artifact appears
            (best-effort cleanup). When ``False`` the pane is left with
            ``remain-on-exit on`` so it survives the agent's natural exit.
        execution_policy: Workflow policy passed to the agent wrapper.
        env: Variables set for the agent process (Issue #407). Delivered by an
            ``env K=V ...`` command prefix because the pane does not inherit
            kaji's environment. ``None`` adds nothing.
        deadline_monotonic: Absolute ``time.monotonic()`` hard deadline of the
            attempt (Issue #421). The runner computes it once at attempt start so
            it matches the deadline shown in the prompt. ``None`` falls back to
            ``time.monotonic() + timeout`` measured after the pane launch.

    Returns:
        ``CLIResult(full_output="", session_id=<resolved id or None>)``.

    Raises:
        CLINotFoundError: ``tmux`` is missing, ``$TMUX`` / ``$TMUX_PANE`` is
            unset, or ``tmux`` is older than 3.1.
        CLIExecutionError: ``split-window`` failed, ``list-panes`` failed, the
            kaji marker could not be set, or the pane died before writing
            ``verdict.yaml``.
        StepTimeoutError: ``verdict.yaml`` did not appear before the deadline, or
            ``deadline_monotonic`` had already passed before the pane launch.
        ValueError: ``step.agent`` is missing or unsupported.
        FileNotFoundError: ``prompt.txt`` or the wrapper script is missing.
    """
    if step.agent is None:
        raise ValueError(f"interactive terminal runner requires step.agent (step={step.id})")
    capabilities = AGENT_CAPABILITIES.get(step.agent)
    if capabilities is None or not capabilities.supports_interactive_terminal:
        raise ValueError(f"interactive terminal runner does not support agent: {step.agent}")
    if not prompt_path.is_file():
        raise FileNotFoundError(f"prompt.txt not found: {prompt_path}")

    tmux = _resolve_tmux()
    target_pane = _resolve_target_pane()
    _validate_tmux_version(tmux)

    wrapper = _wrapper_path()
    if not wrapper.is_file():
        raise FileNotFoundError(f"interactive terminal wrapper not found: {wrapper}")

    terminal_log = prompt_path.parent / "terminal.log"
    metadata_path = prompt_path.parent / "pane-metadata.json"
    # Claude fresh runs need a runner-generated UUID so resume can reuse it.
    # Resume runs and Codex (which mints its own id) pass an empty marker.
    launch_session_id = str(uuid.uuid4()) if step.agent == "claude" and session_id is None else ""

    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        # Issue #421: attempt の期限を preflight で使い切った場合は pane を起動しない。
        # pane も agent session も存在しないため session 解決は試みない。
        raise StepTimeoutError(step.id, timeout, session_resolution=None)

    launch = _launch_pane(
        tmux,
        wrapper,
        target_pane=target_pane,
        agent=step.agent,
        prompt_path=prompt_path,
        verdict_path=verdict_path,
        workdir=workdir,
        resume_session_id=session_id or "",
        launch_session_id=launch_session_id,
        model=step.model or "",
        effort=step.effort or "",
        execution_policy=execution_policy,
        env=env,
        skill_invocation=_wrapper_skill_invocation(step),
    )
    pane_id = launch.pane_id
    # Issue #235: pane 起動成功直後に起動コンソールへ progress を出す。
    # Issue #232: step / agent / timeout を加え、親コンソール 1 行で追跡可能にする。
    _console.info(
        "pane launched: step=%s agent=%s pane=%s timeout=%ds verdict=%s",
        step.id,
        step.agent,
        pane_id,
        timeout,
        verdict_path,
    )
    if not _pipe_pane(tmux, pane_id, terminal_log):
        # tmux rejected the pipe — most likely the pane already closed because a
        # short-running agent wrote verdict.yaml and exited before we could
        # attach the logging pipe. A present verdict means the step succeeded, so
        # do not let a transcript-only setup failure mask it. Only a missing
        # verdict is a genuine launch failure.
        if verdict_path.is_file():
            return CLIResult(full_output="", session_id=session_id or launch_session_id or None)
        _kill_pane(tmux, pane_id)
        raise CLIExecutionError(
            "interactive_terminal",
            1,
            "tmux pipe-pane failed before any verdict appeared: "
            + _terminal_exit_detail(terminal_log),
        )
    if not close_on_verdict:
        _set_remain_on_exit(tmux, pane_id)

    deadline = deadline_monotonic if deadline_monotonic is not None else time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if verdict_path.is_file():
                # Snapshot the pane state at verdict detection (diagnostic evidence).
                # Under the verdict-trigger contract the agent CLI is still alive
                # here, so #{pane_dead} is normally 0.
                _write_pane_metadata(
                    tmux,
                    pane_id,
                    metadata_path,
                    target_pane=target_pane,
                    close_on_verdict=close_on_verdict,
                    layout=launch,
                )
                result_session_id = session_id or launch_session_id or None
                if result_session_id is None and step.agent == "codex":
                    _wait_for_pane_exit_or_session_id(
                        tmux,
                        pane_id,
                        terminal_log,
                        prompt_path=prompt_path,
                        verdict_path=verdict_path,
                        deadline=min(deadline, time.monotonic() + _SESSION_ID_GRACE_SECONDS),
                    )
                    result_session_id = _extract_codex_session_id(
                        terminal_log, prompt_path=prompt_path, verdict_path=verdict_path
                    )
                if close_on_verdict:
                    _kill_pane(tmux, pane_id)
                if result_session_id is None and step.agent == "codex":
                    # Re-scan after cleanup: the rollout file may finalize on exit.
                    result_session_id = _extract_codex_session_id(
                        terminal_log, prompt_path=prompt_path, verdict_path=verdict_path
                    )
                return CLIResult(full_output="", session_id=result_session_id)

            if _pane_dead(tmux, pane_id):
                # The pane exited before any verdict appeared (e.g. the agent failed
                # at launch). Fail loud with the real error instead of polling until
                # the much longer step timeout.
                _write_pane_metadata(
                    tmux,
                    pane_id,
                    metadata_path,
                    target_pane=target_pane,
                    close_on_verdict=close_on_verdict,
                    layout=launch,
                    terminal_log=terminal_log,
                )
                raise CLIExecutionError(
                    step.id,
                    1,
                    _terminal_exit_detail(terminal_log),
                    session_resolution=_resolve_abnormal_exit_session(
                        step.agent,
                        prompt_path=prompt_path,
                        verdict_path=verdict_path,
                        resume_session_id=session_id,
                        launch_session_id=launch_session_id,
                        pane_alive=False,
                    ),
                )
            time.sleep(_VERDICT_POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        # Issue #403: 中断直前の agent 状態を人間が目視・回収できるよう pane は kill せず、
        # 孤児 pane の識別子だけを artifact に残す。KeyboardInterrupt は再送出する。
        try:
            _write_pane_metadata(
                tmux,
                pane_id,
                metadata_path,
                target_pane=target_pane,
                close_on_verdict=close_on_verdict,
                layout=launch,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            # metadata 書き出しの失敗で KeyboardInterrupt を別の例外に置き換えない。
            _console.warning("orphan pane metadata snapshot failed: %s", exc)
        raise

    # Timeout: verdict never appeared. Best-effort cleanup, then fail-loud.
    _write_pane_metadata(
        tmux,
        pane_id,
        metadata_path,
        target_pane=target_pane,
        close_on_verdict=close_on_verdict,
        layout=launch,
    )
    # Issue #403: session 解決は kill の *前* に 1 回だけ行う（kill 後の再走査はしない）。
    resolved = _resolve_abnormal_exit_session(
        step.agent,
        prompt_path=prompt_path,
        verdict_path=verdict_path,
        resume_session_id=session_id,
        launch_session_id=launch_session_id,
        pane_alive=True,
    )
    _kill_pane(tmux, pane_id)
    raise StepTimeoutError(step.id, timeout, session_resolution=resolved)


def _resolve_tmux() -> str:
    tmux = shutil.which("tmux")
    if tmux is None:
        raise CLINotFoundError("CLI 'tmux' not found. Install tmux or use agent_runner='headless'.")
    return tmux


def _resolve_target_pane() -> str:
    """現在の tmux pane を解決する。

    Raises:
        TmuxSessionRequiredError: tmux セッション外から起動された（``$TMUX`` 未設定）。
            既知のユーザー前提エラーとして incident 記録の対象外になる（Issue #322）。
        CLINotFoundError: tmux セッション内だが ``$TMUX_PANE`` が無い（tmux 側の異常）。
    """
    if not os.environ.get("TMUX"):
        raise TmuxSessionRequiredError(
            "interactive terminal runner requires tmux. Run `kaji run` inside tmux "
            "or use agent_runner='headless'."
        )
    target_pane = os.environ.get("TMUX_PANE")
    if not target_pane:
        raise CLINotFoundError("TMUX_PANE is not set; cannot target the current tmux pane.")
    return target_pane


def _validate_tmux_version(tmux: str) -> None:
    proc = subprocess.run([tmux, "-V"], text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise CLIExecutionError("interactive_terminal", proc.returncode, proc.stderr)
    match = re.search(r"tmux\s+(\d+)\.(\d+)", proc.stdout)
    if match is None:
        raise CLIExecutionError("interactive_terminal", proc.returncode, proc.stdout or proc.stderr)
    version = (int(match.group(1)), int(match.group(2)))
    if version < _MIN_TMUX_VERSION:
        raise CLINotFoundError(
            f"interactive terminal runner requires tmux >= 3.1, got {proc.stdout.strip()}"
        )


def _launch_pane(
    tmux: str,
    wrapper: Path,
    *,
    target_pane: str,
    agent: str,
    prompt_path: Path,
    verdict_path: Path,
    workdir: Path,
    resume_session_id: str,
    launch_session_id: str,
    model: str,
    effort: str,
    execution_policy: str,
    env: Mapping[str, str] | None = None,
    skill_invocation: str = "",
) -> _PaneLaunch:
    """Place and launch one agent pane, returning its id and placement metadata.

    Placement (Issue #238): existing kaji-managed agent panes are listed; if the
    right column already holds the maximum, the oldest (top-most) panes are
    pruned so the new pane keeps the column at ``_MAX_VISIBLE_AGENT_PANES``. With
    no managed pane left the origin pane is split horizontally (right column);
    otherwise the bottom managed pane is split vertically.
    """
    existing = _list_kaji_agent_panes(tmux, target_pane)
    # keep one slot free for the pane we are about to create, so launch ends at
    # _MAX_VISIBLE_AGENT_PANES.
    remaining = _prune_kaji_agent_panes(tmux, existing, keep=_MAX_VISIBLE_AGENT_PANES - 1)
    remaining_ids = {pane.pane_id for pane in remaining}
    pruned_ids = [pane.pane_id for pane in existing if pane.pane_id not in remaining_ids]

    if remaining:
        # remaining is sorted by pane_top ascending; the last is the bottom pane.
        split_target_pane = remaining[-1].pane_id
        split_flag = "-v"
    else:
        split_target_pane = target_pane
        split_flag = "-h"

    argv = _build_tmux_split_argv(
        tmux,
        wrapper,
        split_target_pane=split_target_pane,
        split_flag=split_flag,
        agent=agent,
        prompt_path=prompt_path,
        verdict_path=verdict_path,
        workdir=workdir,
        resume_session_id=resume_session_id,
        launch_session_id=launch_session_id,
        model=model,
        effort=effort,
        execution_policy=execution_policy,
        env=env,
        skill_invocation=skill_invocation,
    )
    proc = subprocess.run(argv, text=True, capture_output=True, check=False, cwd=workdir)
    if proc.returncode != 0:
        raise CLIExecutionError("interactive_terminal", proc.returncode, proc.stderr)
    pane_id = proc.stdout.strip()
    if not pane_id.startswith("%"):
        raise CLIExecutionError(
            "interactive_terminal",
            proc.returncode,
            f"tmux did not return a pane id: {proc.stdout!r}",
        )
    try:
        _set_kaji_agent_pane_marker(tmux, pane_id, target_pane=target_pane)
    except CLIExecutionError:
        # Without the marker the pane cannot be safely identified for later
        # cleanup; best-effort kill it before failing loud.
        _kill_pane(tmux, pane_id)
        raise
    return _PaneLaunch(
        pane_id=pane_id,
        split_target_pane=split_target_pane,
        split_flag=split_flag,
        panes_before=[pane.pane_id for pane in existing],
        panes_pruned=pruned_ids,
    )


def _parse_kaji_pane_marker(value: str) -> dict[str, str]:
    """Parse a ``@kaji_interactive_terminal`` marker into a field dict.

    The marker is a whitespace-separated list of ``key=value`` tokens (currently
    just ``origin=<pane_id>``). Unknown fields, empty values, and malformed
    tokens are tolerated: malformed tokens are dropped rather than raising, so a
    user-set or corrupted option never crashes pane discovery.
    """
    fields: dict[str, str] = {}
    for token in value.split():
        key, sep, val = token.partition("=")
        if sep and key:
            fields[key] = val
    return fields


def _list_kaji_agent_panes(tmux: str, target_pane: str) -> list[KajiAgentPane]:
    """List kaji-created agent panes in ``target_pane``'s window, oldest first.

    Only panes whose ``@kaji_interactive_terminal`` marker resolves to
    ``origin=<target_pane>`` are returned; the origin pane itself, unmarked
    panes, and panes from a different origin are ignored so manually created
    panes are never touched. A failed ``list-panes`` fails loud rather than
    risk mis-cleanup on broken tmux state.
    """
    format_string = "\t".join(
        [
            "#{pane_id}",
            "#{pane_top}",
            "#{pane_left}",
            "#{pane_width}",
            f"#{{{_KAJI_PANE_OPTION}}}",
        ]
    )
    proc = subprocess.run(
        [tmux, "list-panes", "-F", format_string, "-t", target_pane],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise CLIExecutionError(
            "interactive_terminal",
            proc.returncode,
            proc.stderr or "tmux list-panes failed",
        )
    panes: list[KajiAgentPane] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        pane_id, top, left, width, marker = parts[0], parts[1], parts[2], parts[3], parts[4]
        if pane_id == target_pane:
            continue
        if _parse_kaji_pane_marker(marker).get("origin") != target_pane:
            continue
        try:
            pane = KajiAgentPane(
                pane_id=pane_id,
                pane_top=int(top),
                pane_left=int(left),
                pane_width=int(width),
            )
        except ValueError:
            continue
        panes.append(pane)
    panes.sort(key=lambda pane: pane.pane_top)
    return panes


def _prune_kaji_agent_panes(
    tmux: str, panes: list[KajiAgentPane], *, keep: int
) -> list[KajiAgentPane]:
    """Kill the oldest panes so at most ``keep`` remain, returning the survivors.

    Oldest = smallest ``pane_top`` (top of the right column). The survivors are
    the newest ``keep`` panes, returned sorted by ``pane_top`` ascending.
    """
    ordered = sorted(panes, key=lambda pane: pane.pane_top)
    if len(ordered) <= keep:
        return ordered
    survivors = ordered[len(ordered) - keep :] if keep > 0 else []
    for pane in ordered[: len(ordered) - len(survivors)]:
        _kill_pane(tmux, pane.pane_id)
    return survivors


def _set_kaji_agent_pane_marker(tmux: str, pane_id: str, *, target_pane: str) -> None:
    """Tag a freshly created pane as kaji-managed via a pane user option.

    Raises:
        CLIExecutionError: ``set-option`` failed; the caller cleans up the
            orphaned pane and fails loud, since an unmarked pane cannot be safely
            identified for later pruning.
    """
    proc = subprocess.run(
        [tmux, "set-option", "-p", "-t", pane_id, _KAJI_PANE_OPTION, f"origin={target_pane}"],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise CLIExecutionError(
            "interactive_terminal",
            proc.returncode,
            proc.stderr or "tmux set-option (kaji marker) failed",
        )


def _pipe_pane(tmux: str, pane_id: str, terminal_log: Path) -> bool:
    """Attach a logging pipe to the pane's output.

    Returns:
        ``True`` if tmux accepted the pipe; ``False`` if it was rejected (e.g.
        the pane already closed because a short-running agent exited
        immediately). The caller decides whether that is fatal based on whether
        ``verdict.yaml`` is already present.
    """
    terminal_log.parent.mkdir(parents=True, exist_ok=True)
    command = f"cat >> {shlex.quote(str(terminal_log))}"
    proc = subprocess.run(
        [tmux, "pipe-pane", "-o", "-t", pane_id, command],
        text=True,
        capture_output=True,
        check=False,
    )
    return proc.returncode == 0


def _set_remain_on_exit(tmux: str, pane_id: str) -> None:
    """Keep the pane (as ``[dead]``) after the agent exits, for inspection."""
    subprocess.run(
        [tmux, "set-option", "-p", "-t", pane_id, "remain-on-exit", "on"],
        text=True,
        capture_output=True,
        check=False,
    )


def _pane_dead(tmux: str, pane_id: str) -> bool:
    """Return whether the pane has died; a failed pane lookup counts as dead."""
    proc = subprocess.run(
        [tmux, "display-message", "-p", "-t", pane_id, "#{pane_dead}"],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return True
    return proc.stdout.strip() == "1"


def _kill_pane(tmux: str, pane_id: str) -> None:
    """Best-effort ``kill-pane``; ignores a pane that is already gone."""
    subprocess.run([tmux, "kill-pane", "-t", pane_id], text=True, capture_output=True, check=False)


def _write_pane_metadata(
    tmux: str,
    pane_id: str,
    destination: Path,
    *,
    target_pane: str,
    close_on_verdict: bool,
    layout: _PaneLaunch | None = None,
    terminal_log: Path | None = None,
) -> None:
    """Snapshot the pane's ``#{pane_dead}`` (and related) fields for diagnostics.

    ``terminal_log`` is only passed at the pane-death call site (Issue #296): its
    structured ``TerminalDiagnostic`` is attached under ``terminal_diagnostic`` so
    the provider-error/no-pattern/extraction-failure distinction survives even
    though the classifier-facing ``CLIExecutionError`` message stays canonical-only.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    metadata: dict[str, object] = {
        "tmux_version": _tmux_version_text(tmux),
        "pane_id": pane_id,
        "target_pane": target_pane,
        "close_on_verdict": close_on_verdict,
    }
    if terminal_log is not None:
        diagnostic = read_terminal_diagnostic(terminal_log)
        metadata["terminal_diagnostic"] = {
            "kind": diagnostic.kind,
            "matched_pattern": diagnostic.matched_pattern,
            "clean_excerpt": diagnostic.clean_excerpt,
            "clean_tail": diagnostic.clean_tail,
        }
    if layout is not None:
        # Issue #238: pane placement diagnostics (right-column layout).
        metadata["layout_target_pane"] = target_pane
        metadata["split_target_pane"] = layout.split_target_pane
        metadata["split_direction"] = "horizontal" if layout.split_flag == "-h" else "vertical"
        metadata["kaji_agent_panes_before"] = layout.panes_before
        metadata["kaji_agent_panes_pruned"] = layout.panes_pruned
    format_string = "\t".join(
        [
            "pane_id=#{pane_id}",
            "pane_pid=#{pane_pid}",
            "pane_current_command=#{pane_current_command}",
            "pane_dead=#{pane_dead}",
            "pane_dead_status=#{pane_dead_status}",
            "pane_dead_signal=#{pane_dead_signal}",
        ]
    )
    proc = subprocess.run(
        [tmux, "display-message", "-p", "-t", pane_id, format_string],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode == 0:
        for part in proc.stdout.strip().split("\t"):
            key, _, value = part.partition("=")
            metadata[key] = value
    else:
        metadata["display_error"] = proc.stderr or proc.stdout
    destination.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _tmux_version_text(tmux: str) -> str:
    proc = subprocess.run([tmux, "-V"], text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        return proc.stderr.strip()
    return proc.stdout.strip()


def _wait_for_pane_exit_or_session_id(
    tmux: str,
    pane_id: str,
    terminal_log: Path,
    *,
    prompt_path: Path,
    verdict_path: Path,
    deadline: float,
) -> None:
    """Give Codex a brief chance to print its explicit resume command."""
    while time.monotonic() < deadline:
        if _pane_dead(tmux, pane_id) or _extract_codex_session_id(
            terminal_log, prompt_path=prompt_path, verdict_path=verdict_path
        ):
            return
        time.sleep(0.2)
