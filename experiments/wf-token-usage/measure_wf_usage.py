"""Measure token usage from kaji workflow artifacts and local agent transcripts."""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Annotated, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from kaji_harness.artifacts import resolve_artifacts_dir
from kaji_harness.config import KajiConfig

IssueId = Annotated[str, Field(pattern=r"^(?:[0-9]+|local-[a-z0-9][a-z0-9-]*)$")]
RunId = Annotated[str, Field(pattern=r"^[0-9]+$")]
StepId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]*$")]
JsonObject = dict[str, object]
WarningSink = Callable[[str], None]

QUALITY_STEP_IDS = frozenset({"review-code", "final-check"})
STEP_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")


class MeasurementError(RuntimeError):
    """A user-facing measurement error that should produce exit code 2."""


class MeasureQuery(BaseModel):
    """Validated path fragments accepted by the measurement CLI."""

    model_config = ConfigDict(extra="forbid")

    issue_ids: list[IssueId] = Field(min_length=1)
    run_id: RunId | None = None
    step_id: StepId | None = None


@dataclass(frozen=True)
class TimeInterval:
    """Inclusive UTC interval used to assign transcript events to an attempt."""

    started_at: datetime
    ended_at: datetime

    def __init__(self, started_at: str | datetime, ended_at: str | datetime) -> None:
        start = parse_timestamp(started_at) if isinstance(started_at, str) else started_at
        end = parse_timestamp(ended_at) if isinstance(ended_at, str) else ended_at
        if start > end:
            raise ValueError("started_at must not be after ended_at")
        object.__setattr__(self, "started_at", start)
        object.__setattr__(self, "ended_at", end)

    def contains(self, timestamp: datetime) -> bool:
        """Return whether timestamp is within the inclusive interval."""
        return self.started_at <= timestamp <= self.ended_at


@dataclass(frozen=True)
class UsageEvent:
    """Normalized Claude usage for one unique API message."""

    message_id: str
    timestamp: datetime
    output_tokens: int
    cache_read_tokens: int
    context_tokens: int


@dataclass(frozen=True)
class CodexUsageEvent:
    """One Codex cumulative token-count event."""

    timestamp: datetime
    total_output_tokens: int
    total_cache_read_tokens: int
    last_input_tokens: int


@dataclass(frozen=True)
class UsageSummary:
    """Usage metrics assigned to one workflow step attempt."""

    calls: int
    output_tokens: int
    cache_read_tokens: int
    max_context: int


@dataclass(frozen=True)
class MeasuredRecord:
    """One measured workflow step attempt."""

    issue: str
    run_id: str
    workflow: str
    step_id: str
    attempt: int
    agent: str | None
    model: str | None
    effort: str | None
    dispatch: str | None
    verdict: str | None
    wall_time_ms: int | None
    session_id: str | None
    interval: TimeInterval | None
    usage: UsageSummary | None = None
    missing_reason: str | None = None

    @classmethod
    def example(
        cls,
        *,
        issue: str,
        usage: UsageSummary | None = None,
        missing_reason: str | None = None,
        wall_time_ms: int | None = None,
    ) -> MeasuredRecord:
        """Build a compact record for pure aggregation tests."""
        return cls(
            issue=issue,
            run_id="260714000000",
            workflow="dev-thorough-fable",
            step_id="implement",
            attempt=1,
            agent="codex",
            model="gpt-test",
            effort="high",
            dispatch="agent",
            verdict="PASS",
            wall_time_ms=wall_time_ms,
            session_id="session",
            interval=None,
            usage=usage,
            missing_reason=missing_reason,
        )

    def to_json(self) -> JsonObject:
        """Return the public JSON record shape."""
        usage_status = "ok" if self.usage is not None else "missing"
        return {
            "issue": self.issue,
            "run_id": self.run_id,
            "workflow": self.workflow,
            "step_id": self.step_id,
            "attempt": self.attempt,
            "agent": self.agent,
            "model": self.model,
            "effort": self.effort,
            "dispatch": self.dispatch,
            "verdict": self.verdict,
            "wall_time_ms": self.wall_time_ms,
            "session_id": self.session_id,
            "usage_status": usage_status,
            "missing_reason": self.missing_reason,
            "calls": self.usage.calls if self.usage is not None else None,
            "output_tokens": self.usage.output_tokens if self.usage is not None else None,
            "cache_read_tokens": (self.usage.cache_read_tokens if self.usage is not None else None),
            "max_context": self.usage.max_context if self.usage is not None else None,
        }


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO 8601 timestamp, accepting the common ``Z`` suffix."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp lacks timezone: {value}")
    return parsed


def _load_json_object(line: str) -> JsonObject | None:
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    return cast(JsonObject, value)


def _object(value: object) -> JsonObject | None:
    if not isinstance(value, dict):
        return None
    return cast(JsonObject, value)


def _integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _parse_claude_event(line: str) -> UsageEvent | None:
    entry = _load_json_object(line)
    if entry is None or entry.get("type") != "assistant":
        return None
    message = _object(entry.get("message"))
    if message is None or message.get("role") != "assistant":
        return None
    usage = _object(message.get("usage"))
    message_id = _string(message.get("id"))
    timestamp_raw = _string(entry.get("timestamp"))
    if usage is None or message_id is None or timestamp_raw is None:
        return None
    output = _integer(usage.get("output_tokens"))
    cache_read = _integer(usage.get("cache_read_input_tokens"))
    input_tokens = _integer(usage.get("input_tokens"))
    cache_creation = _integer(usage.get("cache_creation_input_tokens"))
    if output is None:
        return None
    try:
        timestamp = parse_timestamp(timestamp_raw)
    except ValueError:
        return None
    cache_read_value = cache_read or 0
    context = (input_tokens or 0) + cache_read_value + (cache_creation or 0)
    return UsageEvent(message_id, timestamp, output, cache_read_value, context)


def dedupe_claude_usage(
    lines: Iterable[str], *, warn: WarningSink | None = None
) -> list[UsageEvent]:
    """Normalize repeated Claude streaming rows to one final row per message ID."""
    events: dict[str, UsageEvent] = {}
    inconsistent_ids: set[str] = set()
    for line in lines:
        event = _parse_claude_event(line)
        if event is None:
            continue
        previous = events.get(event.message_id)
        if previous is not None and (
            previous.output_tokens,
            previous.cache_read_tokens,
            previous.context_tokens,
        ) != (event.output_tokens, event.cache_read_tokens, event.context_tokens):
            inconsistent_ids.add(event.message_id)
        events[event.message_id] = event
    if inconsistent_ids and warn is not None:
        warn(
            "Claude transcript contains inconsistent usage for "
            f"{len(inconsistent_ids)} message ID(s); using each final row"
        )
    return list(events.values())


def aggregate_claude_usage(
    events: Iterable[UsageEvent], interval: TimeInterval
) -> UsageSummary | None:
    """Aggregate normalized Claude calls inside an inclusive attempt interval."""
    selected = [event for event in events if interval.contains(event.timestamp)]
    if not selected:
        return None
    return UsageSummary(
        calls=len(selected),
        output_tokens=sum(event.output_tokens for event in selected),
        cache_read_tokens=sum(event.cache_read_tokens for event in selected),
        max_context=max(event.context_tokens for event in selected),
    )


def _parse_codex_events(lines: Iterable[str]) -> list[CodexUsageEvent]:
    events: list[CodexUsageEvent] = []
    for line in lines:
        entry = _load_json_object(line)
        if entry is None or entry.get("type") != "event_msg":
            continue
        payload = _object(entry.get("payload"))
        if payload is None or payload.get("type") != "token_count":
            continue
        info = _object(payload.get("info"))
        total = _object(info.get("total_token_usage")) if info is not None else None
        last = _object(info.get("last_token_usage")) if info is not None else None
        timestamp_raw = _string(entry.get("timestamp"))
        if info is None or total is None or last is None or timestamp_raw is None:
            continue
        output = _integer(total.get("output_tokens"))
        cache_read = _integer(total.get("cached_input_tokens"))
        last_input = _integer(last.get("input_tokens"))
        if output is None or cache_read is None or last_input is None:
            continue
        try:
            timestamp = parse_timestamp(timestamp_raw)
        except ValueError:
            continue
        events.append(CodexUsageEvent(timestamp, output, cache_read, last_input))
    return sorted(events, key=lambda event: event.timestamp)


def aggregate_codex_usage(lines: Iterable[str], interval: TimeInterval) -> UsageSummary | None:
    """Calculate Codex cumulative deltas and call metrics for an attempt interval."""
    events = _parse_codex_events(lines)
    selected = [event for event in events if interval.contains(event.timestamp)]
    if not selected:
        return None
    before = [event for event in events if event.timestamp < interval.started_at]
    baseline_output = before[-1].total_output_tokens if before else 0
    baseline_cache = before[-1].total_cache_read_tokens if before else 0
    final = selected[-1]
    return UsageSummary(
        calls=len(selected),
        output_tokens=max(0, final.total_output_tokens - baseline_output),
        cache_read_tokens=max(0, final.total_cache_read_tokens - baseline_cache),
        max_context=max(event.last_input_tokens for event in selected),
    )


def _warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        _warn(f"cannot read {path}: {error}")
        return []


def find_transcript(session_id: str, root: Path) -> Path | None:
    """Find a Claude transcript by globally unique session ID."""
    return next(iter(sorted(root.rglob(f"{session_id}.jsonl"))), None)


def find_codex_rollout(session_id: str, root: Path) -> Path | None:
    """Find a Codex rollout by globally unique session ID."""
    return next(iter(sorted(root.rglob(f"*{session_id}.jsonl"))), None)


def attach_usage(record: MeasuredRecord, *, claude_root: Path, codex_root: Path) -> MeasuredRecord:
    """Attach provider-specific usage or a precise missing reason to a record."""
    if record.missing_reason is not None:
        return record
    if record.session_id is None:
        return _replace_usage(record, None, "session_id_null")
    if record.agent not in {"claude", "codex"}:
        return _replace_usage(record, None, "provider_unsupported")
    if record.interval is None:
        return _replace_usage(record, None, "result_json_invalid")

    if record.agent == "claude":
        transcript = find_transcript(record.session_id, claude_root)
        if transcript is None:
            return _replace_usage(record, None, "transcript_not_found")
        lines = _read_lines(transcript)
        events = dedupe_claude_usage(lines, warn=_warn)
        if not events:
            return _replace_usage(record, None, "parse_error")
        usage = aggregate_claude_usage(events, record.interval)
    else:
        transcript = find_codex_rollout(record.session_id, codex_root)
        if transcript is None:
            return _replace_usage(record, None, "transcript_not_found")
        lines = _read_lines(transcript)
        if not _parse_codex_events(lines):
            return _replace_usage(record, None, "parse_error")
        usage = aggregate_codex_usage(lines, record.interval)

    if usage is None:
        return _replace_usage(record, None, "no_usage_in_interval")
    return _replace_usage(record, usage, None)


def _replace_usage(
    record: MeasuredRecord, usage: UsageSummary | None, missing_reason: str | None
) -> MeasuredRecord:
    return replace(record, usage=usage, missing_reason=missing_reason)


def _safe_child(base: Path, *parts: str) -> Path:
    resolved_base = base.resolve()
    resolved = resolved_base.joinpath(*parts).resolve()
    if not resolved.is_relative_to(resolved_base):
        raise MeasurementError(f"path escapes artifacts_dir: {resolved}")
    return resolved


def _read_run_log(run_dir: Path) -> tuple[list[JsonObject], str | None]:
    path = run_dir / "run.log"
    if not path.is_file():
        _warn(f"skipping run without run.log: {run_dir}")
        return [], None
    events: list[JsonObject] = []
    workflow: str | None = None
    for line_number, line in enumerate(_read_lines(path), start=1):
        event = _load_json_object(line)
        if event is None:
            _warn(f"skipping invalid run.log line {path}:{line_number}")
            continue
        events.append(event)
        if event.get("event") == "workflow_start":
            workflow = _string(event.get("workflow"))
    return events, workflow


def _event_key(event: JsonObject) -> tuple[str, int] | None:
    step_id = _string(event.get("step_id"))
    attempt = _integer(event.get("attempt"))
    if (
        step_id is None
        or attempt is None
        or attempt < 1
        or STEP_ID_PATTERN.fullmatch(step_id) is None
    ):
        return None
    return step_id, attempt


def _read_result(run_dir: Path, step_id: str, attempt: int) -> tuple[JsonObject | None, str | None]:
    result_path = _safe_child(run_dir, "steps", step_id, f"attempt-{attempt:03d}", "result.json")
    if not result_path.is_file():
        _warn(f"missing result.json: {result_path}")
        return None, "result_json_missing"
    try:
        value = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        _warn(f"invalid result.json {result_path}: {error}")
        return None, "result_json_invalid"
    if not isinstance(value, dict):
        _warn(f"invalid result.json {result_path}: top level must be an object")
        return None, "result_json_invalid"
    return cast(JsonObject, value), None


def _end_metadata(event: JsonObject | None) -> tuple[str | None, int | None]:
    if event is None:
        return None, None
    verdict = _object(event.get("verdict"))
    status = _string(verdict.get("status")) if verdict is not None else None
    return status, _integer(event.get("duration_ms"))


def _record_from_events(
    *,
    issue: str,
    run_id: str,
    workflow: str,
    run_dir: Path,
    start: JsonObject,
    end: JsonObject | None,
) -> MeasuredRecord | None:
    key = _event_key(start)
    if key is None:
        _warn(f"skipping invalid step_start in {run_dir / 'run.log'}")
        return None
    step_id, attempt = key
    result, missing_reason = _read_result(run_dir, step_id, attempt)
    end_status, end_duration = _end_metadata(end)
    if result is None:
        return MeasuredRecord(
            issue=issue,
            run_id=run_id,
            workflow=workflow,
            step_id=step_id,
            attempt=attempt,
            agent=_string(start.get("agent")),
            model=_string(start.get("model")),
            effort=_string(start.get("effort")),
            dispatch=_string(start.get("dispatch")),
            verdict=end_status,
            wall_time_ms=end_duration,
            session_id=None,
            interval=None,
            missing_reason=missing_reason,
        )

    started_at = _string(result.get("started_at"))
    ended_at = _string(result.get("ended_at"))
    try:
        interval = (
            TimeInterval(started_at, ended_at)
            if started_at is not None and ended_at is not None
            else None
        )
    except ValueError:
        interval = None
    if interval is None:
        missing_reason = "result_json_invalid"
        _warn(f"invalid result.json interval for {step_id} attempt {attempt} in {run_dir}")
    result_status = _string(result.get("status"))
    result_duration = _integer(result.get("duration_ms"))
    return MeasuredRecord(
        issue=issue,
        run_id=run_id,
        workflow=workflow,
        step_id=step_id,
        attempt=attempt,
        agent=_string(start.get("agent")),
        model=_string(start.get("model")),
        effort=_string(start.get("effort")),
        dispatch=_string(result.get("dispatch")) or _string(start.get("dispatch")),
        verdict=end_status or result_status,
        wall_time_ms=result_duration if result_duration is not None else end_duration,
        session_id=_string(result.get("session_id")),
        interval=interval,
        missing_reason=missing_reason,
    )


def iter_step_records(
    issue: str, run_dir: Path, *, step_filter: str | None = None
) -> tuple[list[MeasuredRecord], list[JsonObject]]:
    """Join run.log step events with attempt result.json files."""
    events, workflow = _read_run_log(run_dir)
    if not events:
        return [], []
    workflow_name = workflow or "unknown"
    ends = {
        key: event
        for event in events
        if event.get("event") == "step_end" and (key := _event_key(event)) is not None
    }
    records: list[MeasuredRecord] = []
    for start in events:
        if start.get("event") != "step_start":
            continue
        key = _event_key(start)
        if key is None or (step_filter is not None and key[0] != step_filter):
            continue
        record = _record_from_events(
            issue=issue,
            run_id=run_dir.name,
            workflow=workflow_name,
            run_dir=run_dir,
            start=start,
            end=ends.get(key),
        )
        if record is not None:
            records.append(record)
    quality_events = [event for event in events if event.get("event") == "step_end"]
    return records, quality_events


def _median(values: Sequence[int]) -> int | float:
    return statistics.median(values)


def summarize_series(records: Iterable[MeasuredRecord]) -> list[JsonObject]:
    """Group records by comparable workflow, step, provider, model, and effort series."""
    grouped: dict[tuple[str, str, str | None, str | None, str | None], list[MeasuredRecord]] = (
        defaultdict(list)
    )
    for record in records:
        grouped[
            (record.workflow, record.step_id, record.agent, record.model, record.effort)
        ].append(record)

    summaries: list[JsonObject] = []
    for key in sorted(grouped, key=lambda item: tuple(value or "" for value in item)):
        series_records = grouped[key]
        usable = [record for record in series_records if record.usage is not None]
        missing = Counter(
            record.missing_reason
            for record in series_records
            if record.usage is None and record.missing_reason is not None
        )
        wall_times = [
            record.wall_time_ms for record in series_records if record.wall_time_ms is not None
        ]
        summary: JsonObject = {
            "key": {
                "workflow": key[0],
                "step_id": key[1],
                "agent": key[2],
                "model": key[3],
                "effort": key[4],
            },
            "n_records": len(series_records),
            "n_ok": len(usable),
            "n_missing": len(series_records) - len(usable),
            "missing_reasons": dict(sorted(missing.items())),
            "output_tokens": _sum_median(
                [record.usage.output_tokens for record in usable if record.usage is not None]
            ),
            "calls": _sum_median(
                [record.usage.calls for record in usable if record.usage is not None]
            ),
            "cache_read_tokens": _sum_median(
                [record.usage.cache_read_tokens for record in usable if record.usage is not None]
            ),
            "max_context": _max_median(
                [record.usage.max_context for record in usable if record.usage is not None]
            ),
            "wall_time_ms": _sum_median(wall_times),
        }
        summaries.append(summary)
    return summaries


def _sum_median(values: list[int]) -> JsonObject | None:
    if not values:
        return None
    return {"total": sum(values), "median": _median(values)}


def _max_median(values: list[int]) -> JsonObject | None:
    if not values:
        return None
    return {"max": max(values), "median": _median(values)}


def summarize_quality(events: Iterable[JsonObject]) -> list[JsonObject]:
    """Count RETRY and BACK verdicts for review-code and final-check executions."""
    statuses: dict[str, list[str]] = defaultdict(list)
    for event in events:
        step_id = _string(event.get("step_id"))
        verdict = _object(event.get("verdict"))
        status = _string(verdict.get("status")) if verdict is not None else None
        if step_id in QUALITY_STEP_IDS and status is not None:
            statuses[step_id].append(status)
    result: list[JsonObject] = []
    for step_id in sorted(statuses):
        step_statuses = statuses[step_id]
        back_variants = Counter(status for status in step_statuses if status.startswith("BACK"))
        result.append(
            {
                "step_id": step_id,
                "executions": len(step_statuses),
                "retry": sum(status == "RETRY" for status in step_statuses),
                "back": sum(status.startswith("BACK") for status in step_statuses),
                "back_variants": dict(sorted(back_variants.items())),
            }
        )
    return result


def build_output(
    query: MeasureQuery,
    records: list[MeasuredRecord],
    quality_events: Iterable[JsonObject],
) -> JsonObject:
    """Build the single internal output structure used by both renderers."""
    return {
        "schema_version": 1,
        "query": query.model_dump(),
        "records": [record.to_json() for record in records],
        "series": summarize_series(records),
        "quality": summarize_quality(quality_events),
    }


def measure(
    query: MeasureQuery,
    artifacts_dir: Path,
    *,
    claude_root: Path | None = None,
    codex_root: Path | None = None,
) -> JsonObject:
    """Collect, normalize, and aggregate all records selected by query."""
    artifacts = artifacts_dir.resolve()
    if not artifacts.is_dir():
        raise MeasurementError(f"artifacts_dir does not exist: {artifacts}")
    claude_base = claude_root or Path.home() / ".claude" / "projects"
    codex_base = codex_root or Path.home() / ".codex" / "sessions"
    records: list[MeasuredRecord] = []
    quality_events: list[JsonObject] = []
    for issue in query.issue_ids:
        issue_dir = _safe_child(artifacts, issue)
        runs_dir = _safe_child(issue_dir, "runs")
        if not runs_dir.is_dir():
            raise MeasurementError(f"issue runs directory does not exist: {runs_dir}")
        if query.run_id is not None:
            run_dirs = [_safe_child(runs_dir, query.run_id)]
            if not run_dirs[0].is_dir():
                raise MeasurementError(f"run directory does not exist: {run_dirs[0]}")
        else:
            run_dirs = sorted(
                _safe_child(runs_dir, path.name) for path in runs_dir.iterdir() if path.is_dir()
            )
        for run_dir in run_dirs:
            run_records, run_quality = iter_step_records(issue, run_dir, step_filter=query.step_id)
            records.extend(
                attach_usage(record, claude_root=claude_base, codex_root=codex_base)
                for record in run_records
            )
            quality_events.extend(run_quality)
    records.sort(key=lambda record: (record.issue, record.run_id, record.step_id, record.attempt))
    return build_output(query, records, quality_events)


def render_json(payload: JsonObject) -> str:
    """Render the versioned machine-readable representation."""
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False)


def render_table(payload: JsonObject) -> str:
    """Render attempt rows followed by series and quality summaries."""
    records = sorted(
        cast(list[JsonObject], payload["records"]),
        key=lambda record: (
            str(record["workflow"]),
            str(record["step_id"]),
            str(record["agent"] or ""),
            str(record["model"] or ""),
            str(record["effort"] or ""),
            str(record["issue"]),
            str(record["run_id"]),
            cast(int, record["attempt"]),
        ),
    )
    series = cast(list[JsonObject], payload["series"])
    quality = cast(list[JsonObject], payload["quality"])
    lines = [
        "issue\trun_id\tstep\tattempt\tagent\tmodel\teffort\tdispatch\tverdict\t"
        "wall_time_s\tcalls\toutput_tokens\tcache_read\tmax_context\tusage_status"
    ]
    for record in records:
        wall_time = record["wall_time_ms"]
        wall_seconds = f"{cast(int, wall_time) / 1000:.3f}" if wall_time is not None else "-"
        status = cast(str, record["usage_status"])
        if status == "missing":
            status = f"missing:{record['missing_reason']}"
        lines.append(
            "\t".join(
                str(value) if value is not None else "-"
                for value in (
                    record["issue"],
                    record["run_id"],
                    record["step_id"],
                    record["attempt"],
                    record["agent"],
                    record["model"],
                    record["effort"],
                    record["dispatch"],
                    record["verdict"],
                    wall_seconds,
                    record["calls"],
                    record["output_tokens"],
                    record["cache_read_tokens"],
                    record["max_context"],
                    status,
                )
            )
        )
    lines.append("")
    lines.append("Series summaries:")
    for item in series:
        key = cast(JsonObject, item["key"])
        lines.append(
            f"{key['workflow']}/{key['step_id']}/{key['agent']}/{key['model']}/{key['effort']} "
            f"n_records={item['n_records']} n_ok={item['n_ok']} "
            f"n_missing={item['n_missing']} missing_reasons={item['missing_reasons']} "
            f"calls={item['calls']} output_tokens={item['output_tokens']} "
            f"cache_read_tokens={item['cache_read_tokens']} "
            f"max_context={item['max_context']} wall_time_ms={item['wall_time_ms']}"
        )
    lines.append("")
    lines.append("Quality:")
    for item in quality:
        executions = cast(int, item["executions"])
        retry_rate = cast(int, item["retry"]) / executions
        back_rate = cast(int, item["back"]) / executions
        lines.append(
            f"{item['step_id']} executions={item['executions']} retry={item['retry']} "
            f"back={item['back']} retry_rate={retry_rate:.3f} back_rate={back_rate:.3f} "
            f"variants={item['back_variants']}"
        )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("issue_ids", nargs="+")
    parser.add_argument("--run", dest="run_id")
    parser.add_argument("--step", dest="step_id")
    parser.add_argument("--format", choices=("table", "json"), default="table")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the measurement CLI and return a process exit code."""
    args = _parser().parse_args(argv)
    try:
        query = MeasureQuery(
            issue_ids=args.issue_ids,
            run_id=args.run_id,
            step_id=args.step_id,
        )
    except ValidationError as error:
        print(f"input validation failed: {error}", file=sys.stderr)
        return 2
    try:
        config = KajiConfig.discover()
        artifacts_dir = resolve_artifacts_dir(config)
        payload = measure(query, artifacts_dir)
    except (MeasurementError, OSError) as error:
        print(f"measurement failed: {error}", file=sys.stderr)
        return 2
    print(render_json(payload) if args.format == "json" else render_table(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
