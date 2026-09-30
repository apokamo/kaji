"""Artifact ``verdict.yaml`` fallback for ``kaji issue resolve-verdict`` (Issue #426).

``resolve-verdict`` は Issue コメントの verdict marker を最優先する。marker が存在しない
場合に限り、run コンテキストから特定した run（と記録済み復旧元 run）の最新 attempt の
``verdict.yaml`` から判定を解決する。本モジュールは読み取り専用で、artifact への
書き込みや marker の自動投稿は行わない。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePath
from typing import Any

from pydantic import BaseModel, ConfigDict, StrictBool, StrictStr, ValidationError

from .errors import (
    HarnessError,
    VerdictArtifactNotFoundError,
    VerdictArtifactUnusableError,
)
from .result import RESULT_FILE
from .verdict import load_verdict_yaml_for_marker_vocabulary

VERDICT_PATH_ENV = "KAJI_VERDICT_PATH"
VERDICT_FILE = "verdict.yaml"

# ``runner.allocate_run_dir`` の採番形式: ``YYMMDDHHMMSS`` または衝突時 ``-NNN`` 付き。
_RUN_ID_RE = re.compile(r"\d{12}(?:-\d{3,})?")
_ATTEMPT_RE = re.compile(r"attempt-(\d{3,})")


@dataclass(frozen=True)
class RunContext:
    """Run identified as the starting point of an artifact lookup.

    Attributes:
        run_id: Starting run id.
        runs_dir: ``<artifacts>/<issue>/runs`` directory, or ``None`` when it must be
            resolved lazily (``--run`` without a verdict path).
    """

    run_id: str
    runs_dir: Path | None = None

    @property
    def run_dir(self) -> Path | None:
        """Return ``runs_dir / run_id`` when ``runs_dir`` is known."""
        return None if self.runs_dir is None else self.runs_dir / self.run_id


@dataclass(frozen=True)
class ResolvedArtifactVerdict:
    """Verdict adopted from a local ``verdict.yaml`` artifact.

    Attributes:
        step: Producing workflow step.
        status: Status recorded in ``verdict.yaml``.
        run_id: Run the verdict was adopted from (parent run after a recovery hop).
        requested_run_id: Run identified from the run context.
        attempt: Adopted attempt number.
        verdict_path: Absolute path of the adopted ``verdict.yaml``.
        ended_at: Recorded ``result.json`` ``ended_at`` or ``None`` when unknown.
    """

    step: str
    status: str
    run_id: str
    requested_run_id: str
    attempt: int
    verdict_path: Path
    ended_at: str | None

    def as_json(self) -> dict[str, object]:
        """Return the stdout JSON payload (``source: artifact``)."""
        return {
            "step": self.step,
            "status": self.status,
            "meta": {},
            "source": "artifact",
            "run_id": self.run_id,
            "requested_run_id": self.requested_run_id,
            "attempt": self.attempt,
            "verdict_path": str(self.verdict_path),
            "ended_at": self.ended_at,
        }


def parse_run_id(run_id: str) -> str:
    """Validate a run id against the ``allocate_run_dir`` grammar.

    Args:
        run_id: Candidate run id.

    Returns:
        ``run_id`` unchanged.

    Raises:
        ValueError: ``run_id`` does not match ``YYMMDDHHMMSS`` / ``YYMMDDHHMMSS-NNN``.
    """
    if _RUN_ID_RE.fullmatch(run_id) is None:
        raise ValueError(f"invalid run id {run_id!r}: expected YYMMDDHHMMSS or YYMMDDHHMMSS-NNN")
    return run_id


def attempt_number(name: str) -> int | None:
    """Return ``N`` for an ``attempt-NNN`` directory name, else ``None``."""
    match = _ATTEMPT_RE.fullmatch(name)
    return None if match is None else int(match.group(1))


def run_context_from_verdict_path(path: str | PurePath, *, issue_id: str) -> RunContext:
    """Derive the run from a step's own ``verdict_path``.

    Expected shape: ``<root>/<issue>/runs/<run_id>/steps/<step>/attempt-NNN/verdict.yaml``.
    The file itself does not have to exist (the consumer's verdict is not saved yet).

    Args:
        path: The caller's ``verdict_path``.
        issue_id: Normalized Issue id the ``<issue>`` segment must equal.

    Returns:
        The run context with a known ``runs_dir``.

    Raises:
        ValueError: The path shape is wrong or belongs to another Issue.
    """
    parts = Path(path).parts
    # (..., <issue>, "runs", <run_id>, "steps", <step>, "attempt-NNN", "verdict.yaml")
    if len(parts) < 7 or parts[-1] != VERDICT_FILE:
        raise ValueError(
            f"invalid verdict path {str(path)!r}: expected .../attempt-NNN/verdict.yaml"
        )
    attempt, steps, run_id, runs, issue = parts[-2], parts[-4], parts[-5], parts[-6], parts[-7]
    if attempt_number(attempt) is None or steps != "steps" or runs != "runs":
        raise ValueError(
            f"invalid verdict path {str(path)!r}: expected "
            "<issue>/runs/<run_id>/steps/<step>/attempt-NNN/verdict.yaml"
        )
    parse_run_id(run_id)
    if issue != issue_id:
        raise ValueError(f"verdict path belongs to issue {issue!r}, expected issue {issue_id!r}")
    runs_dir = Path(*Path(path).absolute().parts[:-5])
    return RunContext(run_id=run_id, runs_dir=runs_dir)


def run_context_from_env(environ: Mapping[str, str], issue_id: str) -> RunContext | None:
    """Return the run context injected through ``KAJI_VERDICT_PATH``, if usable.

    Invalid or foreign values are treated as "no run context" instead of an error so
    the marker resolution path of existing callers is never affected.
    """
    raw = environ.get(VERDICT_PATH_ENV)
    if not raw:
        return None
    try:
        return run_context_from_verdict_path(raw, issue_id=issue_id)
    except ValueError:
        return None


class _ResultRecord(BaseModel):
    """Fields of ``result.json`` that decide whether an attempt can be adopted.

    ``ended_at`` stays untyped on purpose: an unknown or malformed value is reported as
    ``None`` (provenance only) and never blocks adoption. Old records without
    ``synthetic`` default to ``False``.
    """

    model_config = ConfigDict(extra="ignore")

    status: StrictStr | None = None
    synthetic: StrictBool = False
    error: StrictStr | None = None
    ended_at: object = None


class _ChainRecord(BaseModel):
    """Fields of ``recovery-chain.json`` needed to walk to the recovery parent."""

    model_config = ConfigDict(extra="ignore")

    root_run_id: StrictStr
    parent_run_id: StrictStr


def _read_chain_parent(path: Path) -> str | None:
    """Return ``parent_run_id`` from ``recovery-chain.json``, or ``None`` when absent.

    Raises:
        VerdictArtifactNotFoundError: The file exists but is unreadable, not valid
            UTF-8 / JSON, or does not carry string ``root_run_id`` / ``parent_run_id``.
    """
    if not path.is_file():
        return None
    try:
        record = _ChainRecord.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        # ValueError covers UnicodeDecodeError, JSONDecodeError and ValidationError.
        raise VerdictArtifactNotFoundError(f"{path} is unreadable or invalid: {exc}") from exc
    return record.parent_run_id


def evaluate_result(result: Mapping[str, Any], verdict_status: str) -> str | None:
    """Check a parsed ``result.json`` against ``verdict.yaml`` and extract ``ended_at``.

    ``exit_code`` / ``signal`` are deliberately ignored: a normal attempt can leave a
    positive returncode after the CLI's SIGTERM handling. The runner always records
    ``synthetic: true`` and ``error`` on abnormal paths.

    Args:
        result: Decoded ``result.json`` object.
        verdict_status: Status from the same attempt's ``verdict.yaml``.

    Returns:
        The recorded ``ended_at`` string, or ``None`` when absent, unparsable, or
        lacking a timezone.

    Raises:
        VerdictArtifactUnusableError: The attempt ended abnormally or disagrees on status.
    """
    try:
        record = _ResultRecord.model_validate(result)
    except ValidationError as exc:
        raise VerdictArtifactUnusableError(f"result.json has invalid fields: {exc}") from exc
    if record.synthetic or record.error is not None:
        raise VerdictArtifactUnusableError(
            "latest attempt ended abnormally (result.json records synthetic or error)"
        )
    if record.status != verdict_status:
        raise VerdictArtifactUnusableError(
            f"result.json status {record.status!r} conflicts with "
            f"verdict.yaml status {verdict_status!r}"
        )
    ended_at = record.ended_at
    if not isinstance(ended_at, str):
        return None
    try:
        parsed = datetime.fromisoformat(ended_at)
    except ValueError:
        return None
    return ended_at if parsed.tzinfo is not None else None


def _latest_attempt(step_dir: Path) -> tuple[int, Path] | None:
    """Return the numerically greatest ``attempt-NNN`` directory, or ``None``."""
    if not step_dir.is_dir():
        return None
    attempts = [
        (number, child)
        for child in step_dir.iterdir()
        if (number := attempt_number(child.name)) is not None and child.is_dir()
    ]
    return max(attempts, key=lambda item: item[0]) if attempts else None


def _adopt(
    step: str, number: int, attempt_dir: Path, run_id: str, requested: str
) -> ResolvedArtifactVerdict:
    """Validate the latest attempt and build the resolved verdict."""
    verdict_path = attempt_dir / VERDICT_FILE
    if not verdict_path.is_file():
        raise VerdictArtifactUnusableError(f"{verdict_path} is missing in the latest attempt")
    try:
        verdict = load_verdict_yaml_for_marker_vocabulary(verdict_path)
    except (HarnessError, OSError, UnicodeDecodeError) as exc:
        raise VerdictArtifactUnusableError(f"{verdict_path} is unusable: {exc}") from exc

    ended_at: str | None = None
    result_path = attempt_dir / RESULT_FILE
    if result_path.exists():
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VerdictArtifactUnusableError(f"{result_path} is unreadable: {exc}") from exc
        if not isinstance(result, dict):
            raise VerdictArtifactUnusableError(f"{result_path} is not a JSON object")
        ended_at = evaluate_result(result, verdict.status)

    return ResolvedArtifactVerdict(
        step=step,
        status=verdict.status,
        run_id=run_id,
        requested_run_id=requested,
        attempt=number,
        verdict_path=verdict_path.absolute(),
        ended_at=ended_at,
    )


def resolve_artifact_verdict(run_dir: Path, *, step: str) -> ResolvedArtifactVerdict:
    """Resolve the latest attempt's verdict for ``step`` starting at ``run_dir``.

    The walk moves to the recorded recovery parent (``recovery-chain.json``) only when
    the step was never executed in the current run (no ``attempt-NNN`` directory).
    Older attempts are never consulted once a latest attempt exists.

    Args:
        run_dir: ``<artifacts>/<issue>/runs/<run_id>`` of the requested run.
        step: Producing workflow step.

    Returns:
        The adopted verdict with provenance.

    Raises:
        VerdictArtifactNotFoundError: Run dir absent, or the step never ran in the run
            and its recorded recovery sources (or the chain is unreadable / invalid).
        VerdictArtifactUnusableError: The latest attempt cannot be adopted.
    """
    if not run_dir.is_dir():
        raise VerdictArtifactNotFoundError(f"run directory {run_dir} is not present locally")
    runs_dir = run_dir.parent
    requested = run_dir.name
    visited = {requested}
    current = run_dir
    while True:
        latest = _latest_attempt(current / "steps" / step)
        if latest is not None:
            return _adopt(step, latest[0], latest[1], current.name, requested)
        parent = _read_chain_parent(current / "recovery-chain.json")
        if parent is None:
            raise VerdictArtifactNotFoundError(
                f"step {step!r} was never executed in run {requested!r} "
                "or its recorded recovery sources"
            )
        if _RUN_ID_RE.fullmatch(parent) is None or parent in visited:
            raise VerdictArtifactNotFoundError(
                f"recovery chain of run {current.name!r} has an invalid or cyclic parent {parent!r}"
            )
        visited.add(parent)
        current = runs_dir / parent
        if not current.is_dir():
            raise VerdictArtifactNotFoundError(
                f"recovery parent run {parent!r} is not present locally"
            )
