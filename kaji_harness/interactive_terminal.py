"""Interactive terminal runner.

This runner starts a real interactive agent CLI (``claude`` / ``codex``) inside
``kitty`` and waits for the agent-written ``verdict.yaml`` artifact. It
intentionally avoids parsing stdout: completion is decided by the
artifact-primary verdict resolution introduced in Issue #220.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import time
import uuid
from pathlib import Path

from .errors import CLINotFoundError, StepTimeoutError
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


def _wrapper_path() -> Path:
    """Resolve ``assets/interactive-terminal/wrapper.sh`` next to the package."""
    return Path(__file__).resolve().parent.parent / "assets" / "interactive-terminal" / "wrapper.sh"


def _build_kitty_argv(
    kitty: str,
    wrapper: Path,
    *,
    agent: str,
    step_id: str,
    prompt_path: Path,
    verdict_path: Path,
    terminal_log: Path,
    workdir: Path,
    resume_session_id: str,
    launch_session_id: str,
    model: str,
    effort: str,
) -> list[str]:
    """Assemble the ``kitty`` argv that launches the wrapper.

    The 9 wrapper arguments follow the Wrapper 契約 order exactly:
    ``agent prompt_path verdict_path terminal_log workdir resume_session_id
    launch_session_id model effort``.
    """
    title = f"kaji-{agent}-{step_id}"
    return [
        kitty,
        "--title",
        title,
        "--hold",
        str(wrapper),
        agent,
        str(prompt_path),
        str(verdict_path),
        str(terminal_log),
        str(workdir),
        resume_session_id,
        launch_session_id,
        model,
        effort,
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
    """Start a real interactive CLI in kitty and wait for ``verdict.yaml``.

    Args:
        step: The workflow step (``agent`` must be ``claude`` or ``codex``).
        prompt_path: Absolute path to the attempt's ``prompt.txt``.
        verdict_path: Absolute path the agent must write ``verdict.yaml`` to.
        workdir: Trusted project worktree used as cwd / ``--cd``.
        timeout: Seconds to wait for ``verdict.yaml`` before failing.
        session_id: Previous session id to resume (``None`` → fresh run).
        close_on_verdict: Close the terminal / wrapper / agent after the
            verdict artifact appears.

    Returns:
        ``CLIResult(full_output="", session_id=<resolved id or None>)``.

    Raises:
        CLINotFoundError: ``kitty`` is not on PATH or failed to launch.
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

    kitty = shutil.which("kitty")
    if kitty is None:
        raise CLINotFoundError(
            "CLI 'kitty' not found. Install kitty or use agent_runner='headless'."
        )

    wrapper = _wrapper_path()
    if not wrapper.is_file():
        raise FileNotFoundError(f"interactive terminal wrapper not found: {wrapper}")

    terminal_log = prompt_path.parent / "terminal.log"
    # Claude fresh runs need a runner-generated UUID so resume can reuse it.
    # Resume runs and Codex (which mints its own id) pass an empty marker.
    launch_session_id = str(uuid.uuid4()) if step.agent == "claude" and session_id is None else ""
    argv = _build_kitty_argv(
        kitty,
        wrapper,
        agent=step.agent,
        step_id=step.id,
        prompt_path=prompt_path,
        verdict_path=verdict_path,
        terminal_log=terminal_log,
        workdir=workdir,
        resume_session_id=session_id or "",
        launch_session_id=launch_session_id,
        model=step.model or "",
        effort=step.effort or "",
    )
    try:
        process = subprocess.Popen(argv, cwd=workdir, start_new_session=True)
    except FileNotFoundError as exc:
        raise CLINotFoundError(f"CLI '{argv[0]}' not found. Is it installed?") from exc
    except OSError as exc:
        raise RuntimeError(
            "failed to launch interactive terminal runner:\n"
            f"  argv: {argv!r}\n"
            f"  workdir: {workdir}\n"
            f"  prompt_path: {prompt_path}\n"
            f"  verdict_path: {verdict_path}\n"
            f"  error: {exc}"
        ) from exc

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if verdict_path.is_file():
            result_session_id = session_id or launch_session_id or None
            if result_session_id is None and step.agent == "codex":
                _wait_for_process_or_session_id(
                    process,
                    terminal_log,
                    prompt_path=prompt_path,
                    verdict_path=verdict_path,
                    deadline=min(deadline, time.monotonic() + _SESSION_ID_GRACE_SECONDS),
                )
                result_session_id = _extract_codex_session_id(
                    terminal_log, prompt_path=prompt_path, verdict_path=verdict_path
                )
            if close_on_verdict:
                _close_terminal(process, markers=[prompt_path, verdict_path, terminal_log])
            if result_session_id is None and step.agent == "codex":
                # Re-scan after close: the rollout file may finalize on exit.
                result_session_id = _extract_codex_session_id(
                    terminal_log, prompt_path=prompt_path, verdict_path=verdict_path
                )
            return CLIResult(full_output="", session_id=result_session_id)
        time.sleep(_VERDICT_POLL_INTERVAL_SECONDS)

    # Timeout: verdict never appeared. Best-effort cleanup, then fail-loud.
    _close_terminal(process, markers=[prompt_path, verdict_path, terminal_log])
    raise StepTimeoutError(step.id, timeout)


def _wait_for_process_or_session_id(
    process: subprocess.Popen[bytes],
    terminal_log: Path,
    *,
    prompt_path: Path,
    verdict_path: Path,
    deadline: float,
) -> None:
    """Give Codex a brief chance to print its explicit resume command."""
    while time.monotonic() < deadline:
        if process.poll() is not None or _extract_codex_session_id(
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


def _close_terminal(process: subprocess.Popen[bytes], *, markers: list[Path]) -> None:
    """Best-effort close for the kitty process after the verdict artifact appears."""
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
    _kill_processes_matching(markers)


def _kill_processes_matching(markers: list[Path]) -> None:
    """Kill detached agent processes whose argv still references this attempt."""
    marker_text = [str(marker) for marker in markers]
    proc = Path("/proc")
    if not proc.is_dir():
        return

    matches: set[int] = set()
    for child in proc.iterdir():
        if not child.name.isdigit():
            continue
        pid = int(child.name)
        if pid == os.getpid():
            continue
        try:
            raw = (child / "cmdline").read_bytes()
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        cmdline = raw.replace(b"\0", b" ").decode("utf-8", errors="replace")
        if any(marker in cmdline for marker in marker_text):
            matches.add(pid)

    _signal_matches(matches, signal.SIGTERM)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        alive = {pid for pid in matches if _pid_exists(pid)}
        if not alive:
            return
        time.sleep(0.2)
    _signal_matches(matches, signal.SIGKILL)


def _signal_matches(pids: set[int], sig: signal.Signals) -> None:
    groups: set[int] = set()
    for pid in pids:
        try:
            groups.add(os.getpgid(pid))
        except ProcessLookupError:
            continue
    for pgid in groups:
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            continue


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
