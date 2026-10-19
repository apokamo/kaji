"""Interactive terminal runner entry point.

``execute_interactive_terminal`` is the public entry point used by ``runner.py``. Herdr is
the only terminal backend (Issue #515 removed tmux), implemented in
``interactive_terminal_herdr``. Terminal-independent helpers live in
``interactive_terminal_common``.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from . import interactive_terminal_herdr
from .models import CLIResult, Step


def execute_interactive_terminal(
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
    """Start a real interactive CLI in a Herdr pane and wait for ``verdict.yaml``.

    Args:
        step: The workflow step with an interactive-terminal capable agent.
        prompt_path: Absolute path to the attempt's ``prompt.txt``.
        verdict_path: Absolute path the agent must write ``verdict.yaml`` to.
        workdir: Trusted project worktree used as cwd / ``--cd``. Resolved by
            ``runner.py`` to the same ``effective_workdir`` as the headless
            runner (backend-independent).
        timeout: Seconds to wait for ``verdict.yaml`` before failing.
        session_id: Previous session id to resume (``None`` → fresh run).
        close_on_verdict: Close the owned pane after the verdict artifact appears
            (best-effort cleanup). When ``False`` the pane is kept for inspection.
        execution_policy: Workflow policy passed to the agent wrapper.
        env: Variables set for the agent process (Issue #407). Delivered by an
            ``env K=V ...`` command prefix because the pane does not inherit
            kaji's environment. ``None`` adds nothing.
        deadline_monotonic: Absolute ``time.monotonic()`` hard deadline of the
            attempt (Issue #421). The runner computes it once at attempt start so
            it matches the deadline shown in the prompt. ``None`` falls back to
            ``time.monotonic() + timeout`` measured after the launcher start.

    Returns:
        ``CLIResult(full_output="", session_id=<resolved id or None>)``.

    Raises:
        CLINotFoundError: ``herdr`` is missing or older than the supported version.
        HerdrSessionRequiredError: kaji is not running inside an explicit Herdr pane
            (``HERDR_ENV=1`` and ``HERDR_PANE_ID``).
        CLIExecutionError: A Herdr operation failed or the agent exited before
            writing ``verdict.yaml``.
        StepTimeoutError: ``verdict.yaml`` did not appear before the deadline, or
            ``deadline_monotonic`` had already passed before the pane launch.
        ValueError: ``step.agent`` is missing or unsupported.
        FileNotFoundError: ``prompt.txt``, the wrapper script or the recorder is missing.
    """
    return interactive_terminal_herdr.execute_interactive_terminal_herdr(
        step=step,
        prompt_path=prompt_path,
        verdict_path=verdict_path,
        workdir=workdir,
        timeout=timeout,
        session_id=session_id,
        close_on_verdict=close_on_verdict,
        execution_policy=execution_policy,
        env=env,
        deadline_monotonic=deadline_monotonic,
    )
