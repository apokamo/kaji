"""Interactive terminal runner.

This runner starts a real interactive agent CLI (``claude`` / ``codex``) inside
a ``tmux`` pane and waits for the agent-written ``verdict.yaml`` artifact. It
intentionally avoids parsing stdout: completion is decided by the
artifact-primary verdict resolution introduced in Issue #220.

The terminal backend is tmux only (ADR 007 v2, Issue #230). ``kaji run`` must
run inside a tmux session; the runner adds a pane to the current window with
``tmux split-window -h`` (to the user's right), records the transcript with
``tmux pipe-pane``, decides liveness via ``#{pane_dead}``, and cleans up with
``tmux kill-pane``. There is no ``/proc`` scan and no util-linux ``script(1)``
dependency, so Linux and macOS share one implementation.

The single completion trigger is the appearance of ``verdict.yaml``; the agent
process is not waited on. The post-verdict ``kill-pane`` is best-effort cleanup
with no latency contract.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from .errors import CLIExecutionError, CLINotFoundError, StepTimeoutError
from .models import CLIResult, Step

_CODEX_RESUME_RE = re.compile(
    r"\bcodex\s+resume\s+([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\b"
)
_CODEX_SESSION_FILE_RE = re.compile(
    r"rollout-.*-([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.jsonl$"
)
_SESSION_ID_GRACE_SECONDS = 5.0
_CODEX_SESSION_SCAN_LIMIT = 100
_VERDICT_POLL_INTERVAL_SECONDS = 2
_TERMINAL_LOG_TAIL_CHARS = 2000
_MIN_TMUX_VERSION = (3, 0)


def _terminal_exit_detail(terminal_log: Path) -> str:
    """Build a diagnostic string from the terminal log tail for early pane exits."""
    if not terminal_log.is_file():
        return f"tmux pane exited before writing verdict.yaml (no {terminal_log.name})"
    text = terminal_log.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return f"tmux pane exited before writing verdict.yaml ({terminal_log.name} empty)"
    return f"tmux pane exited before writing verdict.yaml; log tail:\n{text[-_TERMINAL_LOG_TAIL_CHARS:]}"


def _wrapper_path() -> Path:
    """Resolve the packaged ``assets/interactive-terminal/wrapper.sh``.

    The wrapper ships as package data under ``kaji_harness/assets`` so it is
    available both from a source checkout and from an installed wheel/sdist.
    """
    return Path(__file__).resolve().parent / "assets" / "interactive-terminal" / "wrapper.sh"


def _build_wrapper_command(
    wrapper: Path,
    *,
    agent: str,
    prompt_path: Path,
    verdict_path: Path,
    workdir: Path,
    resume_session_id: str,
    launch_session_id: str,
    model: str,
    effort: str,
) -> str:
    """Build the single shell command tmux runs in the new pane.

    ``split-window`` takes one command argument, so the wrapper argv is
    shell-quoted with ``shlex.join``. The 8 wrapper arguments follow the
    Wrapper 契約 order exactly: ``agent prompt_path verdict_path workdir
    resume_session_id launch_session_id model effort``.
    """
    return shlex.join(
        [
            str(wrapper),
            agent,
            str(prompt_path),
            str(verdict_path),
            str(workdir),
            resume_session_id,
            launch_session_id,
            model,
            effort,
        ]
    )


def _build_tmux_split_argv(
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
) -> list[str]:
    """Assemble the ``tmux split-window`` argv.

    ``-d`` keeps focus on the current pane; ``-h`` splits horizontally so the
    new pane appears to the user's right (Issue #230 MF2); ``-P -F '#{pane_id}'``
    prints the created pane id, which becomes the lifecycle handle for polling,
    transcript, and cleanup.
    """
    return [
        tmux,
        "split-window",
        "-d",
        "-h",
        "-P",
        "-F",
        "#{pane_id}",
        "-t",
        target_pane,
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
        ),
    ]


def execute_interactive_terminal(
    *,
    step: Step,
    prompt_path: Path,
    verdict_path: Path,
    workdir: Path,
    timeout: int,
    session_id: str | None = None,
    close_on_verdict: bool = True,
) -> CLIResult:
    """Start a real interactive CLI in a tmux pane and wait for ``verdict.yaml``.

    Args:
        step: The workflow step (``agent`` must be ``claude`` or ``codex``).
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

    Returns:
        ``CLIResult(full_output="", session_id=<resolved id or None>)``.

    Raises:
        CLINotFoundError: ``tmux`` is missing, ``$TMUX`` / ``$TMUX_PANE`` is
            unset, or ``tmux`` is older than 3.0.
        CLIExecutionError: ``split-window`` failed, or the pane died before
            writing ``verdict.yaml``.
        StepTimeoutError: ``verdict.yaml`` did not appear before the deadline.
        ValueError: ``step.agent`` is missing or unsupported.
        FileNotFoundError: ``prompt.txt`` or the wrapper script is missing.
    """
    if step.agent is None:
        raise ValueError(f"interactive terminal runner requires step.agent (step={step.id})")
    if step.agent not in {"claude", "codex"}:
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

    pane_id = _launch_pane(
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
    )
    _pipe_pane(tmux, pane_id, terminal_log)
    if not close_on_verdict:
        _set_remain_on_exit(tmux, pane_id)

    deadline = time.monotonic() + timeout
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
            )
            raise CLIExecutionError(step.id, 1, _terminal_exit_detail(terminal_log))
        time.sleep(_VERDICT_POLL_INTERVAL_SECONDS)

    # Timeout: verdict never appeared. Best-effort cleanup, then fail-loud.
    _write_pane_metadata(
        tmux, pane_id, metadata_path, target_pane=target_pane, close_on_verdict=close_on_verdict
    )
    _kill_pane(tmux, pane_id)
    raise StepTimeoutError(step.id, timeout)


def _resolve_tmux() -> str:
    tmux = shutil.which("tmux")
    if tmux is None:
        raise CLINotFoundError("CLI 'tmux' not found. Install tmux or use agent_runner='headless'.")
    return tmux


def _resolve_target_pane() -> str:
    if not os.environ.get("TMUX"):
        raise CLINotFoundError(
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
            f"interactive terminal runner requires tmux >= 3.0, got {proc.stdout.strip()}"
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
) -> str:
    argv = _build_tmux_split_argv(
        tmux,
        wrapper,
        target_pane=target_pane,
        agent=agent,
        prompt_path=prompt_path,
        verdict_path=verdict_path,
        workdir=workdir,
        resume_session_id=resume_session_id,
        launch_session_id=launch_session_id,
        model=model,
        effort=effort,
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
    return pane_id


def _pipe_pane(tmux: str, pane_id: str, terminal_log: Path) -> None:
    terminal_log.parent.mkdir(parents=True, exist_ok=True)
    command = f"cat >> {shlex.quote(str(terminal_log))}"
    proc = subprocess.run(
        [tmux, "pipe-pane", "-o", "-t", pane_id, command],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        _kill_pane(tmux, pane_id)
        raise CLIExecutionError("interactive_terminal", proc.returncode, proc.stderr)


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
) -> None:
    """Snapshot the pane's ``#{pane_dead}`` (and related) fields for diagnostics."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    metadata: dict[str, object] = {
        "tmux_version": _tmux_version_text(tmux),
        "pane_id": pane_id,
        "target_pane": target_pane,
        "close_on_verdict": close_on_verdict,
    }
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


def _extract_codex_session_id(
    terminal_log: Path, *, prompt_path: Path | None = None, verdict_path: Path | None = None
) -> str | None:
    """Extract Codex's session UUID from ``terminal.log`` or the session store."""
    if not terminal_log.is_file():
        return _extract_codex_session_id_from_store(
            prompt_path=prompt_path, verdict_path=verdict_path
        )
    text = terminal_log.read_text(encoding="utf-8", errors="replace")
    matches = _CODEX_RESUME_RE.findall(text)
    if matches:
        return str(matches[-1])
    return _extract_codex_session_id_from_store(prompt_path=prompt_path, verdict_path=verdict_path)


def _extract_codex_session_id_from_store(
    *, prompt_path: Path | None, verdict_path: Path | None
) -> str | None:
    """Find Codex's rollout session file when the final resume line was not printed.

    Scans ``CODEX_HOME/sessions/**/*.jsonl`` (then ``~/.codex/sessions``) by
    descending mtime and adopts the UUID of the first rollout file whose body
    references this attempt's ``prompt_path`` / ``verdict_path`` marker.
    """
    markers = [str(path) for path in (prompt_path, verdict_path) if path is not None]
    if not markers:
        return None

    sessions_dir = _codex_home() / "sessions"
    if not sessions_dir.is_dir():
        return None

    candidates = sorted(
        sessions_dir.rglob("*.jsonl"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:_CODEX_SESSION_SCAN_LIMIT]
    for candidate in candidates:
        match = _CODEX_SESSION_FILE_RE.match(candidate.name)
        if match is None:
            continue
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except (FileNotFoundError, PermissionError, OSError):
            continue
        if any(marker in text for marker in markers):
            return match.group(1)
    return None


def _codex_home() -> Path:
    if value := os.environ.get("CODEX_HOME"):
        return Path(value)
    return Path.home() / ".codex"
