"""Application service for selecting and validating a recovery target run."""

from __future__ import annotations

from pathlib import Path

from ..config import KajiConfig
from ..errors import RecoveryTargetError
from ..providers import IssueContext, IssueProvider, normalize_id
from .snapshot import read_run_log_events


def resolve_recover_issue_context(
    config: KajiConfig,
    provider: IssueProvider,
    issue_input: str,
) -> IssueContext:
    """Resolve the canonical Issue context used by ``kaji recover``.

    Args:
        config: Loaded kaji configuration with a validated provider section.
        provider: Provider used to load the canonical Issue context.
        issue_input: User-supplied Issue identifier.

    Returns:
        The provider's canonical Issue context.
    """
    assert config.provider is not None
    provider_type = config.provider.type
    machine_id = config.provider.local.machine_id if provider_type == "local" else None
    resolved_id = normalize_id(
        issue_input,
        provider_name=provider_type,
        machine_id=machine_id,
    )
    return provider.resolve_issue_context(resolved_id.value)


def select_target_run_dir(runs_dir: Path, run_id: str | None) -> Path:
    """Select an ERROR or ABORT run that is safe for failure triage.

    Args:
        runs_dir: Directory containing run artifact directories.
        run_id: Explicit run identifier, or ``None`` to select the latest.

    Returns:
        The validated target run directory.

    Raises:
        RecoveryTargetError: The run is absent, unreadable, still running, or
            completed with an ineligible status.
    """
    if not runs_dir.is_dir():
        raise RecoveryTargetError(f"no runs found under {runs_dir}")
    if run_id is not None:
        run_dir = runs_dir / run_id
        if not run_dir.is_dir():
            raise RecoveryTargetError(f"run dir not found: {run_dir}")
    else:
        candidates = sorted(path for path in runs_dir.iterdir() if path.is_dir())
        if not candidates:
            raise RecoveryTargetError(f"no runs found under {runs_dir}")
        run_dir = candidates[-1]

    run_log = run_dir / "run.log"
    if not run_log.is_file():
        raise RecoveryTargetError(f"run.log not found: {run_log}")
    try:
        events = read_run_log_events(run_log)
    except OSError as exc:
        raise RecoveryTargetError(f"cannot read {run_log}: {exc}") from exc
    end_events = [event for event in events if event.get("event") == "workflow_end"]
    if not end_events:
        raise RecoveryTargetError(
            f"run {run_dir.name} is still in progress (no workflow_end event); "
            "refusing to run failure triage against it"
        )
    status = end_events[-1].get("status")
    if status not in ("ERROR", "ABORT"):
        raise RecoveryTargetError(
            f"run {run_dir.name} ended with status {status!r}; "
            "failure triage only applies to ERROR / ABORT runs"
        )
    return run_dir
