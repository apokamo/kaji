"""Interactive terminal runner (backend dispatch).

``execute_interactive_terminal`` is the public entry point used by ``runner.py``. It only
selects the terminal backend: ``interactive_terminal_herdr`` for ``backend="herdr"`` and
``interactive_terminal_tmux`` otherwise (the default). Terminal-independent helpers live in
``interactive_terminal_common``.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from . import interactive_terminal_herdr, interactive_terminal_tmux
from .models import CLIResult, Step


def execute_interactive_terminal(
    *,
    step: Step,
    prompt_path: Path,
    verdict_path: Path,
    workdir: Path,
    timeout: int,
    session_id: str | None = None,
    backend: Literal["tmux", "herdr"] = "tmux",
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
        backend: Terminal backend selected for this run.
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
    if backend == "herdr":
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
    return interactive_terminal_tmux.execute_interactive_terminal_tmux(
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
