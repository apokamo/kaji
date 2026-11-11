"""run.log reading and required-artifact rules (Issue #540).

``kaji status`` / ``kaji recover --retry-failed-step`` / ``kaji run-series --resume`` と
``collect_snapshot(..., allow_unterminated=True)`` が共有する、run.log の解釈と
「存在すべきなのに無い・壊れている artifact」の判定。recovery package を import
しない（recovery の snapshot から呼ばれるため）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

RUN_LOG_FILE = "run.log"
RESULT_FILE = "result.json"

ReadErrorKind = Literal["missing", "unreadable", "invalid"]


@dataclass(frozen=True)
class ArtifactProblem:
    """必須 artifact の欠落・破損 1 件。``path`` は run dir からの相対パス。"""

    path: str
    error: ReadErrorKind
    message: str


@dataclass(frozen=True)
class RunLogRead:
    """run.log の読み取り結果。``events`` が ``None`` なら ``problem`` に理由。"""

    events: list[dict[str, Any]] | None
    problem: ArtifactProblem | None = None


def read_run_log(run_dir: Path) -> RunLogRead:
    """run.log を読み、``workflow_start`` を含む event 列を返す。

    run dir があれば run.log は常に存在すべき artifact。欠落・読めない・parse できる
    event が無い・``workflow_start`` が無い場合は ``events=None`` と問題を返す。
    """
    path = run_dir / RUN_LOG_FILE
    if not path.is_file():
        return RunLogRead(None, ArtifactProblem(RUN_LOG_FILE, "missing", "run.log not found"))
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return RunLogRead(None, ArtifactProblem(RUN_LOG_FILE, "unreadable", str(exc)))
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            events.append(entry)
    if not any(event.get("event") == "workflow_start" for event in events):
        return RunLogRead(
            None, ArtifactProblem(RUN_LOG_FILE, "invalid", "no parsable workflow_start event")
        )
    return RunLogRead(events)


def last_event(events: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    """``name`` の最後の event を返す。"""
    for entry in reversed(events):
        if entry.get("event") == name:
            return entry
    return None


def _attempt_of(event: dict[str, Any]) -> int | None:
    value = event.get("attempt")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def find_unterminated_step(events: list[dict[str, Any]]) -> tuple[str, int | None] | None:
    """最後の ``step_start`` のうち、同じ step・attempt の ``step_end`` が後に無いもの。

    Returns:
        ``(step_id, attempt)``。全 step が ``step_end`` 済みなら ``None``。
    """
    for index in range(len(events) - 1, -1, -1):
        event = events[index]
        if event.get("event") != "step_start":
            continue
        step_id = event.get("step_id")
        if not isinstance(step_id, str):
            continue
        attempt = _attempt_of(event)
        ended = any(
            later.get("event") == "step_end"
            and later.get("step_id") == step_id
            and _attempt_of(later) == attempt
            for later in events[index + 1 :]
        )
        if ended:
            return None
        return step_id, attempt
    return None


def attempt_dir_name(attempt: int) -> str:
    """attempt 番号から ``attempt-NNN`` を返す。"""
    return f"attempt-{attempt:03d}"


def required_result_problems(run_dir: Path, events: list[dict[str, Any]]) -> list[ArtifactProblem]:
    """``step_end`` がある attempt の ``result.json`` の欠落・破損を列挙する。

    ``step_end`` の無い attempt（実行中・kill された in-flight attempt）は対象外。
    """
    problems: list[ArtifactProblem] = []
    seen: set[tuple[str, int]] = set()
    for event in events:
        if event.get("event") != "step_end":
            continue
        step_id = event.get("step_id")
        attempt = _attempt_of(event)
        if not isinstance(step_id, str) or attempt is None or (step_id, attempt) in seen:
            continue
        seen.add((step_id, attempt))
        relative = f"steps/{step_id}/{attempt_dir_name(attempt)}/{RESULT_FILE}"
        path = run_dir / relative
        if not path.is_file():
            problems.append(ArtifactProblem(relative, "missing", "result.json not found"))
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(ArtifactProblem(relative, "unreadable", str(exc)))
            continue
        except json.JSONDecodeError as exc:
            problems.append(ArtifactProblem(relative, "invalid", str(exc)))
            continue
        if not isinstance(data, dict):
            problems.append(ArtifactProblem(relative, "invalid", "top-level is not an object"))
    return problems
