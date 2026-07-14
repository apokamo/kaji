"""Tests for the workflow token usage measurement experiment."""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest
from pydantic import ValidationError

SCRIPT = Path(__file__).parents[1] / "experiments" / "wf-token-usage" / "measure_wf_usage.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("measure_wf_usage", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


wf = _load_script()


def _iso(second: int) -> str:
    return f"2026-07-14T12:00:{second:02d}+00:00"


def _claude_line(
    message_id: str,
    second: int,
    *,
    output: int,
    cache_read: int,
    input_tokens: int = 10,
    cache_creation: int = 0,
) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "timestamp": _iso(second),
            "message": {
                "id": message_id,
                "role": "assistant",
                "model": "fable",
                "usage": {
                    "output_tokens": output,
                    "cache_read_input_tokens": cache_read,
                    "input_tokens": input_tokens,
                    "cache_creation_input_tokens": cache_creation,
                },
            },
        }
    )


def _codex_line(
    second: int,
    *,
    total_output: int,
    total_cache: int,
    last_input: int,
    context_window: int = 258_400,
) -> str:
    return json.dumps(
        {
            "timestamp": _iso(second),
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": total_cache + 100,
                        "cached_input_tokens": total_cache,
                        "output_tokens": total_output,
                    },
                    "last_token_usage": {"input_tokens": last_input},
                    "model_context_window": context_window,
                },
            },
        }
    )


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _write_run_log(path: Path, events: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")


def _workflow_start(workflow: str = "dev-thorough-fable") -> dict[str, object]:
    return {"ts": _iso(0), "event": "workflow_start", "workflow": workflow, "issue": "325"}


def _step_start(
    step_id: str,
    attempt: int,
    agent: str,
    *,
    model: str = "model-1",
    effort: str = "high",
) -> dict[str, object]:
    return {
        "ts": _iso(1),
        "event": "step_start",
        "step_id": step_id,
        "attempt": attempt,
        "agent": agent,
        "model": model,
        "effort": effort,
        "dispatch": "agent",
    }


def _step_end(
    step_id: str,
    attempt: int,
    status: str,
    *,
    duration_ms: int = 1_000,
) -> dict[str, object]:
    return {
        "ts": _iso(50),
        "event": "step_end",
        "step_id": step_id,
        "attempt": attempt,
        "verdict": {"status": status},
        "duration_ms": duration_ms,
        "dispatch": "agent",
    }


def _result(
    step_id: str,
    attempt: int,
    session_id: str | None,
    start: int,
    end: int,
) -> dict[str, object]:
    return {
        "step_id": step_id,
        "attempt": attempt,
        "status": "PASS",
        "started_at": _iso(start),
        "ended_at": _iso(end),
        "duration_ms": (end - start) * 1_000,
        "session_id": session_id,
        "dispatch": "agent",
        "synthetic": False,
    }


@pytest.mark.small
def test_measure_query_accepts_supported_identifiers() -> None:
    query = wf.MeasureQuery(
        issue_ids=["325", "local-token-baseline"],
        run_id="260714232626",
        step_id="review-code_2",
    )

    assert query.issue_ids == ["325", "local-token-baseline"]
    assert query.run_id == "260714232626"
    assert query.step_id == "review-code_2"


@pytest.mark.small
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("issue_ids", ["../325"]),
        ("issue_ids", ["a/b"]),
        ("issue_ids", ["325\\"]),
        ("issue_ids", [""]),
        ("issue_ids", ["~x"]),
        ("run_id", "../260714232626"),
        ("step_id", "review/code"),
    ],
)
def test_measure_query_rejects_path_fragments(field: str, value: object) -> None:
    values: dict[str, object] = {"issue_ids": ["325"], "run_id": None, "step_id": None}
    values[field] = value

    with pytest.raises(ValidationError):
        wf.MeasureQuery.model_validate(values)


@pytest.mark.small
def test_dedupe_claude_usage_uses_last_unique_message_and_warns() -> None:
    lines = [
        _claude_line("a", 10, output=10, cache_read=100),
        _claude_line("a", 11, output=10, cache_read=100),
        _claude_line("b", 12, output=20, cache_read=200),
        _claude_line("b", 13, output=25, cache_read=250),
        json.dumps({"type": "user", "timestamp": _iso(14)}),
        "not-json",
    ]
    warnings: list[str] = []

    events = wf.dedupe_claude_usage(lines, warn=warnings.append)

    assert [event.message_id for event in events] == ["a", "b"]
    assert events[0].timestamp == datetime(2026, 7, 14, 12, 0, 11, tzinfo=UTC)
    assert events[1].output_tokens == 25
    assert events[1].cache_read_tokens == 250
    assert len(warnings) == 1
    assert "1 message" in warnings[0]


@pytest.mark.small
def test_aggregate_claude_usage_filters_interval_inclusively() -> None:
    events = wf.dedupe_claude_usage(
        [
            _claude_line("before", 9, output=1, cache_read=10),
            _claude_line("start", 10, output=2, cache_read=20, input_tokens=30, cache_creation=40),
            _claude_line("end", 20, output=3, cache_read=30, input_tokens=50),
            _claude_line("after", 21, output=4, cache_read=40),
        ]
    )

    usage = wf.aggregate_claude_usage(events, wf.TimeInterval(_iso(10), _iso(20)))

    assert usage == wf.UsageSummary(
        calls=2,
        output_tokens=5,
        cache_read_tokens=50,
        max_context=90,
    )
    assert wf.aggregate_claude_usage(events, wf.TimeInterval(_iso(30), _iso(40))) is None


@pytest.mark.small
def test_aggregate_codex_usage_uses_interval_cumulative_delta() -> None:
    lines = [
        _codex_line(9, total_output=1_000, total_cache=4_000, last_input=90),
        _codex_line(10, total_output=1_200, total_cache=4_500, last_input=120),
        _codex_line(20, total_output=1_500, total_cache=5_000, last_input=150),
        _codex_line(21, total_output=1_900, total_cache=5_500, last_input=180),
        "not-json",
    ]

    usage = wf.aggregate_codex_usage(lines, wf.TimeInterval(_iso(10), _iso(20)))

    assert usage == wf.UsageSummary(
        calls=2,
        output_tokens=500,
        cache_read_tokens=1_000,
        max_context=150,
    )
    assert wf.aggregate_codex_usage(lines, wf.TimeInterval(_iso(30), _iso(40))) is None


@pytest.mark.small
def test_aggregate_codex_usage_uses_zero_when_no_pre_interval_event() -> None:
    usage = wf.aggregate_codex_usage(
        [
            _codex_line(10, total_output=1_200, total_cache=4_500, last_input=120),
            _codex_line(20, total_output=1_500, total_cache=5_000, last_input=150),
        ],
        wf.TimeInterval(_iso(10), _iso(20)),
    )

    assert usage == wf.UsageSummary(
        calls=2,
        output_tokens=1_500,
        cache_read_tokens=5_000,
        max_context=150,
    )


@pytest.mark.small
def test_attach_usage_distinguishes_missing_reasons(tmp_path: Path) -> None:
    claude_root = tmp_path / "claude"
    codex_root = tmp_path / "codex"
    base = replace(
        wf.MeasuredRecord.example(issue="325"),
        agent="claude",
        interval=wf.TimeInterval(_iso(10), _iso(20)),
    )

    assert (
        wf.attach_usage(
            replace(base, session_id=None),
            claude_root=claude_root,
            codex_root=codex_root,
        ).missing_reason
        == "session_id_null"
    )
    assert (
        wf.attach_usage(
            replace(base, agent="gemini"),
            claude_root=claude_root,
            codex_root=codex_root,
        ).missing_reason
        == "provider_unsupported"
    )
    assert (
        wf.attach_usage(base, claude_root=claude_root, codex_root=codex_root).missing_reason
        == "transcript_not_found"
    )

    transcript = claude_root / "project/session.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("not-json\n", encoding="utf-8")
    assert (
        wf.attach_usage(base, claude_root=claude_root, codex_root=codex_root).missing_reason
        == "parse_error"
    )

    transcript.write_text(_claude_line("outside", 30, output=10, cache_read=100), encoding="utf-8")
    assert (
        wf.attach_usage(base, claude_root=claude_root, codex_root=codex_root).missing_reason
        == "no_usage_in_interval"
    )
    preset = replace(base, missing_reason="result_json_missing")
    assert (
        wf.attach_usage(preset, claude_root=claude_root, codex_root=codex_root).missing_reason
        == "result_json_missing"
    )


@pytest.mark.small
def test_summarize_series_excludes_missing_usage_from_token_statistics() -> None:
    records = [
        wf.MeasuredRecord.example(
            issue="325",
            usage=wf.UsageSummary(2, 20, 200, 2_000),
            wall_time_ms=1_000,
        ),
        wf.MeasuredRecord.example(
            issue="326",
            usage=wf.UsageSummary(4, 40, 400, 4_000),
            wall_time_ms=2_000,
        ),
        wf.MeasuredRecord.example(
            issue="327",
            missing_reason="transcript_not_found",
            wall_time_ms=3_000,
        ),
    ]

    summary = wf.summarize_series(records)[0]

    assert summary["n_records"] == 3
    assert summary["n_ok"] == 2
    assert summary["n_missing"] == 1
    assert summary["missing_reasons"] == {"transcript_not_found": 1}
    assert summary["output_tokens"] == {"total": 60, "median": 30.0}
    assert summary["calls"] == {"total": 6, "median": 3.0}
    assert summary["wall_time_ms"] == {"total": 6_000, "median": 2_000}


@pytest.mark.small
def test_summarize_quality_counts_retry_and_back_variants() -> None:
    quality = wf.summarize_quality(
        [
            _step_end("review-code", 1, "RETRY"),
            _step_end("review-code", 2, "BACK"),
            _step_end("review-code", 3, "BACK_DESIGN"),
            _step_end("review-code", 4, "PASS"),
            _step_end("final-check", 1, "BACK_IMPLEMENT"),
        ]
    )

    assert quality == [
        {
            "step_id": "final-check",
            "executions": 1,
            "retry": 0,
            "back": 1,
            "back_variants": {"BACK_IMPLEMENT": 1},
        },
        {
            "step_id": "review-code",
            "executions": 4,
            "retry": 1,
            "back": 2,
            "back_variants": {"BACK": 1, "BACK_DESIGN": 1},
        },
    ]


@pytest.mark.small
def test_json_contract_uses_null_for_missing_token_fields() -> None:
    record = wf.MeasuredRecord.example(
        issue="325", missing_reason="provider_unsupported", wall_time_ms=1_000
    )
    payload = wf.build_output(wf.MeasureQuery(issue_ids=["325"]), [record], quality_events=[])

    encoded = json.loads(wf.render_json(payload))

    assert set(encoded) == {"schema_version", "query", "records", "series", "quality"}
    assert encoded["schema_version"] == 1
    assert encoded["records"][0]["usage_status"] == "missing"
    assert encoded["records"][0]["missing_reason"] == "provider_unsupported"
    assert encoded["records"][0]["calls"] is None
    assert encoded["records"][0]["output_tokens"] is None
    assert encoded["records"][0]["cache_read_tokens"] is None
    assert encoded["records"][0]["max_context"] is None


@pytest.fixture
def artifact_fixture(tmp_path: Path) -> Iterator[tuple[Path, Path, Path]]:
    artifacts = tmp_path / "artifacts"
    claude_root = tmp_path / "claude"
    codex_root = tmp_path / "codex"
    yield artifacts, claude_root, codex_root


@pytest.mark.medium
def test_claude_resume_attempts_receive_only_interval_usage(
    artifact_fixture: tuple[Path, Path, Path],
) -> None:
    artifacts, claude_root, codex_root = artifact_fixture
    run_dir = artifacts / "325" / "runs" / "260714000001"
    session_id = "claude-session"
    events = [
        _workflow_start(),
        _step_start("design", 1, "claude", model="fable"),
        _step_end("design", 1, "PASS"),
        _step_start("fix-design", 1, "claude", model="fable"),
        _step_end("fix-design", 1, "PASS"),
    ]
    _write_run_log(run_dir / "run.log", events)
    _write_json(
        run_dir / "steps/design/attempt-001/result.json",
        _result("design", 1, session_id, 10, 20),
    )
    _write_json(
        run_dir / "steps/fix-design/attempt-001/result.json",
        _result("fix-design", 1, session_id, 30, 40),
    )
    transcript = claude_root / "project" / f"{session_id}.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        "\n".join(
            [
                _claude_line("design", 15, output=100, cache_read=1_000),
                _claude_line("design", 16, output=100, cache_read=1_000),
                _claude_line("fix", 35, output=200, cache_read=2_000),
                _claude_line("fix", 36, output=200, cache_read=2_000),
            ]
        ),
        encoding="utf-8",
    )

    payload = wf.measure(
        wf.MeasureQuery(issue_ids=["325"]),
        artifacts,
        claude_root=claude_root,
        codex_root=codex_root,
    )

    records = {record["step_id"]: record for record in payload["records"]}
    assert records["design"]["calls"] == 1
    assert records["design"]["output_tokens"] == 100
    assert records["fix-design"]["calls"] == 1
    assert records["fix-design"]["output_tokens"] == 200
    assert sum(record["output_tokens"] for record in records.values()) == 300


@pytest.mark.medium
def test_codex_resume_attempts_use_non_overlapping_cumulative_deltas(
    artifact_fixture: tuple[Path, Path, Path],
) -> None:
    artifacts, claude_root, codex_root = artifact_fixture
    run_dir = artifacts / "325" / "runs" / "260714000002"
    session_id = "codex-session"
    events = [
        _workflow_start(),
        _step_start("implement", 1, "codex", model="gpt-test"),
        _step_end("implement", 1, "PASS"),
        _step_start("fix-code", 1, "codex", model="gpt-test"),
        _step_end("fix-code", 1, "PASS"),
    ]
    _write_run_log(run_dir / "run.log", events)
    _write_json(
        run_dir / "steps/implement/attempt-001/result.json",
        _result("implement", 1, session_id, 10, 20),
    )
    _write_json(
        run_dir / "steps/fix-code/attempt-001/result.json",
        _result("fix-code", 1, session_id, 30, 40),
    )
    transcript = codex_root / "2026/07/14" / f"rollout-x-{session_id}.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        "\n".join(
            [
                _codex_line(5, total_output=50, total_cache=500, last_input=50),
                _codex_line(15, total_output=150, total_cache=1_500, last_input=100),
                _codex_line(25, total_output=150, total_cache=1_500, last_input=100),
                _codex_line(35, total_output=350, total_cache=3_500, last_input=200),
            ]
        ),
        encoding="utf-8",
    )

    payload = wf.measure(
        wf.MeasureQuery(issue_ids=["325"]),
        artifacts,
        claude_root=claude_root,
        codex_root=codex_root,
    )

    records = {record["step_id"]: record for record in payload["records"]}
    assert records["implement"]["output_tokens"] == 100
    assert records["fix-code"]["output_tokens"] == 200
    assert sum(record["output_tokens"] for record in records.values()) == 300


@pytest.mark.medium
def test_missing_and_invalid_result_files_become_visible_records(
    artifact_fixture: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    artifacts, claude_root, codex_root = artifact_fixture
    run_dir = artifacts / "325" / "runs" / "260714000003"
    _write_run_log(
        run_dir / "run.log",
        [
            _workflow_start(),
            _step_start("design", 1, "claude"),
            _step_end("design", 1, "PASS", duration_ms=1_234),
            _step_start("implement", 1, "codex"),
            _step_end("implement", 1, "RETRY", duration_ms=2_345),
        ],
    )
    invalid = run_dir / "steps/implement/attempt-001/result.json"
    invalid.parent.mkdir(parents=True)
    invalid.write_text("{invalid", encoding="utf-8")

    payload = wf.measure(
        wf.MeasureQuery(issue_ids=["325"]),
        artifacts,
        claude_root=claude_root,
        codex_root=codex_root,
    )

    records = {record["step_id"]: record for record in payload["records"]}
    assert records["design"]["missing_reason"] == "result_json_missing"
    assert records["design"]["wall_time_ms"] == 1_234
    assert records["implement"]["missing_reason"] == "result_json_invalid"
    assert records["implement"]["verdict"] == "RETRY"
    assert "result.json" in capsys.readouterr().err


@pytest.mark.medium
def test_cli_discovers_configured_artifacts_dir_and_renders_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    config = repo / ".kaji/config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        """
[paths]
artifacts_dir = "custom-artifacts"
skill_dir = ".claude/skills"

[execution]
default_timeout = 30
""".strip(),
        encoding="utf-8",
    )
    run_dir = repo / "custom-artifacts/325/runs/260714000004"
    _write_run_log(
        run_dir / "run.log",
        [
            _workflow_start(),
            _step_start("synthetic", 1, "gemini"),
            _step_end("synthetic", 1, "PASS"),
        ],
    )
    _write_json(
        run_dir / "steps/synthetic/attempt-001/result.json",
        _result("synthetic", 1, None, 10, 20),
    )
    monkeypatch.chdir(repo)

    exit_code = wf.main(["325", "--format", "json"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["query"]["issue_ids"] == ["325"]
    assert output["records"][0]["missing_reason"] == "session_id_null"


@pytest.mark.medium
def test_cli_rejects_traversal_before_config_or_artifact_scan(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = wf.main(["../325"])

    assert exit_code == 2
    assert "issue_ids" in capsys.readouterr().err


@pytest.mark.medium
def test_run_and_step_filters_and_missing_paths(
    artifact_fixture: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    artifacts, claude_root, codex_root = artifact_fixture
    run_dir = artifacts / "325/runs/260714000005"
    _write_run_log(
        run_dir / "run.log",
        [
            _workflow_start(),
            _step_start("design", 1, "gemini"),
            _step_end("design", 1, "PASS"),
            _step_start("implement", 1, "gemini"),
            _step_end("implement", 1, "PASS"),
        ],
    )
    for step in ("design", "implement"):
        _write_json(
            run_dir / f"steps/{step}/attempt-001/result.json",
            _result(step, 1, "gemini-session", 10, 20),
        )

    payload = wf.measure(
        wf.MeasureQuery(issue_ids=["325"], run_id="260714000005", step_id="implement"),
        artifacts,
        claude_root=claude_root,
        codex_root=codex_root,
    )

    assert [record["step_id"] for record in payload["records"]] == ["implement"]
    with pytest.raises(wf.MeasurementError):
        wf.measure(
            wf.MeasureQuery(issue_ids=["999"]),
            artifacts,
            claude_root=claude_root,
            codex_root=codex_root,
        )
    assert capsys.readouterr().err == ""


@pytest.mark.medium
def test_run_without_run_log_is_skipped_with_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    records, quality = wf.iter_step_records("325", tmp_path / "260714000006")

    assert records == []
    assert quality == []
    assert "without run.log" in capsys.readouterr().err


@pytest.mark.medium
def test_table_and_json_are_derived_from_same_counts() -> None:
    records = [
        wf.MeasuredRecord.example(issue="325", usage=wf.UsageSummary(1, 10, 100, 1_000)),
        wf.MeasuredRecord.example(issue="326", missing_reason="parse_error"),
        replace(
            wf.MeasuredRecord.example(issue="327", usage=wf.UsageSummary(2, 20, 200, 2_000)),
            agent="claude",
            model="fable",
        ),
    ]
    quality_events = [
        _step_end("review-code", 1, "RETRY"),
        _step_end("review-code", 2, "PASS"),
    ]
    payload = wf.build_output(wf.MeasureQuery(issue_ids=["325", "326"]), records, quality_events)

    table = wf.render_table(payload)
    encoded = json.loads(wf.render_json(payload))

    header = table.splitlines()[0]
    assert "\tdispatch\t" in header
    assert table.index("\tclaude\tfable\t") < table.index("\tcodex\tgpt-test\t")
    assert table.index("/claude/fable/") < table.index("/codex/gpt-test/")
    assert "n_ok=1 n_missing=1" in table
    assert "calls={'total': 1, 'median': 1}" in table
    assert "output_tokens={'total': 10, 'median': 10}" in table
    assert "cache_read_tokens={'total': 100, 'median': 100}" in table
    assert "max_context={'max': 1000, 'median': 1000}" in table
    assert "wall_time_ms=" in table
    assert "review-code executions=2 retry=1 back=0 retry_rate=0.500 back_rate=0.000" in table
    assert sum(series["n_ok"] for series in encoded["series"]) == 2
    assert sum(series["n_missing"] for series in encoded["series"]) == 1
    assert encoded["quality"][0]["executions"] == 2
