"""Deterministic starter-sync tracking issue schema and planning tests (Issue #423)."""

from __future__ import annotations

import pytest

from kaji_harness.errors import TrackingBodyError
from kaji_harness.starter_tracking import (
    TaskCompletion,
    TaskPlanInput,
    TrackingIssueBody,
    TrackingIssueObservation,
    TrackingPlanInput,
    TrackingTaskRow,
    build_task_plan,
    build_tracking_plan,
    parse_release_version,
    parse_tracking_issue_body,
    render_tracking_issue_body,
)

pytestmark = pytest.mark.small

SCHEMA_MARKER = "<!-- kaji-starter-sync: v1 -->"


def _body_text(
    *,
    starter_repo: str = "apokamo/kaji-starter-python",
    starter_path: str | None = "../kaji-starter-python",
    rows: list[tuple[str, str, str, str]] | None = None,
) -> str:
    if rows is None:
        rows = [("v0.20.3", "open", "-", "-")]
    lines = [SCHEMA_MARKER, f"starter_repo: {starter_repo}"]
    if starter_path is not None:
        lines.append(f"starter_path: {starter_path}")
    lines += [
        "",
        "## Sync tasks",
        "",
        "| target_kaji_release | status | batch | result |",
        "|---|---|---|---|",
    ]
    for target, status, batch, result in rows:
        lines.append(f"| {target} | {status} | {batch} | {result} |")
    return "\n".join(lines) + "\n"


# --- parse_release_version -------------------------------------------------


def test_parse_release_version_orders_double_digit_components_correctly() -> None:
    assert parse_release_version("v0.9.0") < parse_release_version("v0.10.0")
    assert parse_release_version("v0.20.0") == (0, 20, 0)


def test_parse_release_version_rejects_malformed_input() -> None:
    with pytest.raises(ValueError):
        parse_release_version("0.20.0")


# --- parse / render round trip ---------------------------------------------


def test_parse_render_round_trip_preserves_meaning() -> None:
    text = _body_text(
        rows=[
            ("v0.20.0", "done", "b1", "N/A"),
            ("v0.20.1", "done", "b1", "kaji-v0.20.1"),
            ("v0.20.2", "syncing", "b2", "-"),
            ("v0.20.3", "open", "-", "-"),
        ]
    )

    body = parse_tracking_issue_body(text)

    assert body.starter_repo == "apokamo/kaji-starter-python"
    assert body.starter_path == "../kaji-starter-python"
    assert [t.target_kaji_release for t in body.tasks] == [
        "v0.20.0",
        "v0.20.1",
        "v0.20.2",
        "v0.20.3",
    ]
    open_row = next(t for t in body.tasks if t.target_kaji_release == "v0.20.3")
    assert (open_row.status, open_row.batch, open_row.result) == ("open", "-", "-")
    syncing_row = next(t for t in body.tasks if t.target_kaji_release == "v0.20.2")
    assert (syncing_row.status, syncing_row.batch, syncing_row.result) == ("syncing", "b2", "-")
    done_row = next(t for t in body.tasks if t.target_kaji_release == "v0.20.1")
    assert (done_row.status, done_row.batch, done_row.result) == ("done", "b1", "kaji-v0.20.1")

    rendered = render_tracking_issue_body(body)
    assert rendered == text
    assert parse_tracking_issue_body(rendered) == body


def test_render_sorts_tasks_by_ascending_version_regardless_of_input_order() -> None:
    body = TrackingIssueBody(
        starter_repo="apokamo/kaji-starter-python",
        starter_path=None,
        tasks=(
            TrackingTaskRow(target_kaji_release="v0.20.10", status="open", batch="-", result="-"),
            TrackingTaskRow(target_kaji_release="v0.20.2", status="open", batch="-", result="-"),
        ),
    )

    rendered = render_tracking_issue_body(body)
    ordered = [line for line in rendered.splitlines() if line.startswith("| v0.20")]

    assert ordered == [
        "| v0.20.2 | open | - | - |",
        "| v0.20.10 | open | - | - |",
    ]
    assert "starter_path" not in rendered


# --- parse: 異常系 -----------------------------------------------------------


def test_parse_rejects_missing_schema_marker() -> None:
    legacy = "target_kaji_release: v0.20.0\nstatus: PENDING\n"
    with pytest.raises(TrackingBodyError):
        parse_tracking_issue_body(legacy)


def test_parse_rejects_unknown_status() -> None:
    text = _body_text(rows=[("v0.20.0", "queued", "-", "-")])
    with pytest.raises(TrackingBodyError):
        parse_tracking_issue_body(text)


def test_parse_rejects_duplicate_target() -> None:
    text = _body_text(
        rows=[
            ("v0.20.0", "open", "-", "-"),
            ("v0.20.0", "open", "-", "-"),
        ]
    )
    with pytest.raises(TrackingBodyError):
        parse_tracking_issue_body(text)


def test_parse_rejects_malformed_version() -> None:
    text = _body_text(rows=[("0.20.0", "open", "-", "-")])
    with pytest.raises(TrackingBodyError):
        parse_tracking_issue_body(text)


def test_parse_rejects_missing_sync_tasks_section() -> None:
    text = f"{SCHEMA_MARKER}\nstarter_repo: apokamo/kaji-starter-python\n"
    with pytest.raises(TrackingBodyError):
        parse_tracking_issue_body(text)


def test_parse_rejects_done_row_with_dash_result() -> None:
    text = _body_text(rows=[("v0.20.0", "done", "b1", "-")])
    with pytest.raises(TrackingBodyError):
        parse_tracking_issue_body(text)


def test_parse_rejects_syncing_row_with_result() -> None:
    text = _body_text(rows=[("v0.20.0", "syncing", "b1", "N/A")])
    with pytest.raises(TrackingBodyError):
        parse_tracking_issue_body(text)


def test_parse_rejects_open_row_with_batch_id() -> None:
    text = _body_text(rows=[("v0.20.0", "open", "b1", "-")])
    with pytest.raises(TrackingBodyError):
        parse_tracking_issue_body(text)


def test_parse_rejects_malformed_batch_id() -> None:
    text = _body_text(rows=[("v0.20.0", "syncing", "batch-1", "-")])
    with pytest.raises(TrackingBodyError):
        parse_tracking_issue_body(text)


def test_parse_rejects_malformed_starter_repo() -> None:
    text = _body_text(starter_repo="not-a-repo")
    with pytest.raises(TrackingBodyError):
        parse_tracking_issue_body(text)


# --- build_tracking_plan: route 1-5 -----------------------------------------


def _tracking_input(**overrides: object) -> TrackingPlanInput:
    values: dict[str, object] = {
        "starter_repo": "apokamo/kaji-starter-python",
        "new_target": "v0.20.2",
        "open_tracking_issues": [],
        "selected_issue_id": None,
    }
    values.update(overrides)
    return TrackingPlanInput.model_validate(values)


def test_tracking_plan_route1_creates_when_no_open_issue_exists() -> None:
    plan = build_tracking_plan(_tracking_input())

    assert plan.route == 1
    assert plan.decision == "CREATE"
    assert plan.issue_id is None
    assert plan.required_labels == ["starter-sync"]
    assert plan.next_body is not None
    body = parse_tracking_issue_body(plan.next_body)
    assert [t.target_kaji_release for t in body.tasks] == ["v0.20.2"]
    assert body.tasks[0].status == "open"


def test_tracking_plan_route2_appends_without_disturbing_existing_rows() -> None:
    existing = _body_text(
        rows=[
            ("v0.20.0", "syncing", "b1", "-"),
        ]
    )
    plan = build_tracking_plan(
        _tracking_input(open_tracking_issues=[{"issue_id": 424, "body": existing}])
    )

    assert plan.route == 2
    assert plan.decision == "APPEND"
    assert plan.issue_id == 424
    assert plan.required_labels == []
    body = parse_tracking_issue_body(plan.next_body or "")
    assert [t.target_kaji_release for t in body.tasks] == ["v0.20.0", "v0.20.2"]
    existing_row = next(t for t in body.tasks if t.target_kaji_release == "v0.20.0")
    assert (existing_row.status, existing_row.batch) == ("syncing", "b1")
    new_row = next(t for t in body.tasks if t.target_kaji_release == "v0.20.2")
    assert (new_row.status, new_row.batch, new_row.result) == ("open", "-", "-")


def test_tracking_plan_route3_is_idempotent_when_target_already_listed() -> None:
    existing = _body_text(rows=[("v0.20.2", "open", "-", "-")])
    plan = build_tracking_plan(
        _tracking_input(open_tracking_issues=[{"issue_id": 424, "body": existing}])
    )

    assert plan.route == 3
    assert plan.decision == "IDEMPOTENT"
    assert plan.issue_id == 424
    assert plan.next_body is None


def test_tracking_plan_route4_aborts_on_multiple_candidates_without_selection() -> None:
    first = _body_text(rows=[("v0.20.0", "open", "-", "-")])
    second = _body_text(rows=[("v0.19.0", "open", "-", "-")])
    plan = build_tracking_plan(
        _tracking_input(
            open_tracking_issues=[
                {"issue_id": 424, "body": first},
                {"issue_id": 430, "body": second},
            ]
        )
    )

    assert plan.route == 4
    assert plan.decision == "ABORT"
    assert plan.issue_id is None
    assert plan.reason


def test_tracking_plan_route4_resolves_with_explicit_selection() -> None:
    first = _body_text(rows=[("v0.20.0", "open", "-", "-")])
    second = _body_text(rows=[("v0.19.0", "open", "-", "-")])
    plan = build_tracking_plan(
        _tracking_input(
            open_tracking_issues=[
                {"issue_id": 424, "body": first},
                {"issue_id": 430, "body": second},
            ],
            selected_issue_id=430,
        )
    )

    assert plan.route == 2
    assert plan.decision == "APPEND"
    assert plan.issue_id == 430


def test_tracking_plan_route5_aborts_when_selection_not_a_candidate() -> None:
    first = _body_text(rows=[("v0.20.0", "open", "-", "-")])
    plan = build_tracking_plan(
        _tracking_input(
            open_tracking_issues=[{"issue_id": 424, "body": first}],
            selected_issue_id=999,
        )
    )

    assert plan.route == 5
    assert plan.decision == "ABORT"


def test_tracking_plan_route5_aborts_when_candidate_body_fails_to_parse() -> None:
    broken = "starter_repo: apokamo/kaji-starter-python\nnot a schema body\n"
    plan = build_tracking_plan(
        _tracking_input(open_tracking_issues=[{"issue_id": 424, "body": broken}])
    )

    assert plan.route == 5
    assert plan.decision == "ABORT"


def test_tracking_plan_ignores_issues_for_other_starters() -> None:
    other = _body_text(starter_repo="apokamo/kaji-starter-typescript")
    plan = build_tracking_plan(
        _tracking_input(open_tracking_issues=[{"issue_id": 424, "body": other}])
    )

    assert plan.route == 1
    assert plan.decision == "CREATE"


def test_tracking_plan_ignores_legacy_schema_issues_without_starter_repo_line() -> None:
    legacy = "target_kaji_release: v0.16.0\nstatus: PENDING\n"
    plan = build_tracking_plan(
        _tracking_input(open_tracking_issues=[{"issue_id": 401, "body": legacy}])
    )

    assert plan.route == 1
    assert plan.decision == "CREATE"


# --- build_task_plan: route 1-7 ---------------------------------------------


def _task_input(**overrides: object) -> TaskPlanInput:
    values: dict[str, object] = {
        "issue_id": 424,
        "issue_state": "open",
        "body": _body_text(rows=[("v0.20.0", "open", "-", "-")]),
        "completion": None,
    }
    values.update(overrides)
    return TaskPlanInput.model_validate(values)


def test_task_plan_route1_continues_existing_batch_without_changing_body() -> None:
    body = _body_text(
        rows=[
            ("v0.20.0", "syncing", "b1", "-"),
            ("v0.20.1", "syncing", "b1", "-"),
            ("v0.20.2", "open", "-", "-"),
        ]
    )
    plan = build_task_plan(_task_input(body=body))

    assert plan.route == 1
    assert plan.decision == "SYNC"
    assert plan.batch == "b1"
    assert plan.active_target == "v0.20.1"
    assert plan.covered_targets == ["v0.20.0", "v0.20.1"]
    assert plan.coalesced is True
    assert plan.remaining_targets == ["v0.20.2"]
    assert plan.next_body is None


def test_task_plan_route1_covered_targets_survive_a_later_append() -> None:
    """指摘1回帰: 束ね集合が candidate 固定後・後続 APPEND 後も縮退しない。"""
    started = _body_text(
        rows=[
            ("v0.20.0", "open", "-", "-"),
            ("v0.20.1", "open", "-", "-"),
        ]
    )
    started_plan = build_task_plan(_task_input(body=started))
    assert started_plan.route == 2
    assert started_plan.covered_targets == ["v0.20.0", "v0.20.1"]

    with_append = parse_tracking_issue_body(started_plan.next_body or "")
    appended = TrackingIssueBody(
        starter_repo=with_append.starter_repo,
        starter_path=with_append.starter_path,
        tasks=(
            *with_append.tasks,
            TrackingTaskRow(target_kaji_release="v0.20.2", status="open", batch="-", result="-"),
        ),
    )
    appended_body = render_tracking_issue_body(appended)

    resumed_plan = build_task_plan(_task_input(body=appended_body))

    assert resumed_plan.route == 1
    assert resumed_plan.batch == started_plan.batch
    assert resumed_plan.covered_targets == ["v0.20.0", "v0.20.1"]
    assert resumed_plan.remaining_targets == ["v0.20.2"]
    assert resumed_plan.next_body is None


def test_task_plan_route2_starts_new_batch_covering_all_open_rows() -> None:
    body = _body_text(
        rows=[
            ("v0.20.0", "open", "-", "-"),
            ("v0.20.1", "open", "-", "-"),
        ]
    )
    plan = build_task_plan(_task_input(body=body))

    assert plan.route == 2
    assert plan.decision == "SYNC"
    assert plan.batch == "b1"
    assert plan.active_target == "v0.20.1"
    assert plan.covered_targets == ["v0.20.0", "v0.20.1"]
    assert plan.coalesced is True
    assert plan.remaining_targets == []
    updated = parse_tracking_issue_body(plan.next_body or "")
    assert all(t.status == "syncing" and t.batch == "b1" for t in updated.tasks)


def test_task_plan_route2_assigns_next_unused_batch_id() -> None:
    body = _body_text(
        rows=[
            ("v0.20.0", "done", "b1", "N/A"),
            ("v0.20.1", "done", "b1", "kaji-v0.20.1"),
            ("v0.20.2", "open", "-", "-"),
        ]
    )
    plan = build_task_plan(_task_input(body=body))

    assert plan.route == 2
    assert plan.batch == "b2"


def test_task_plan_route3_reports_closable_when_all_tasks_done() -> None:
    body = _body_text(rows=[("v0.20.0", "done", "b1", "N/A")])
    plan = build_task_plan(_task_input(body=body))

    assert plan.route == 3
    assert plan.decision == "CLOSABLE"
    assert plan.close_allowed is True
    assert plan.next_body is None


def test_task_plan_route4_completes_batch_with_published_tag() -> None:
    body = _body_text(
        rows=[
            ("v0.20.0", "syncing", "b1", "-"),
            ("v0.20.1", "syncing", "b1", "-"),
            ("v0.20.2", "open", "-", "-"),
        ]
    )
    completion = {
        "batch": "b1",
        "completed_targets": ["v0.20.0", "v0.20.1"],
        "published_tag": "kaji-v0.20.1",
    }
    plan = build_task_plan(_task_input(body=body, completion=completion))

    assert plan.route == 4
    assert plan.decision == "COMPLETED"
    assert plan.batch == "b1"
    assert plan.active_target == "v0.20.1"
    assert plan.covered_targets == ["v0.20.0", "v0.20.1"]
    assert plan.remaining_targets == ["v0.20.2"]
    assert plan.close_allowed is False
    updates = {u.kaji_release: u for u in plan.state_table_updates}
    assert updates["v0.20.1"].status == "PASS"
    assert updates["v0.20.1"].starter_release == "kaji-v0.20.1"
    assert updates["v0.20.0"].status == "N/A"
    assert updates["v0.20.0"].starter_release is None
    assert "kaji-v0.20.1" in (updates["v0.20.0"].reason or "")
    for update in plan.state_table_updates:
        assert update.tracking_issue == 424

    updated = parse_tracking_issue_body(plan.next_body or "")
    done_rows = {t.target_kaji_release: t for t in updated.tasks if t.batch == "b1"}
    assert done_rows["v0.20.1"].status == "done"
    assert done_rows["v0.20.1"].result == "kaji-v0.20.1"
    assert done_rows["v0.20.0"].result == "N/A"


def test_task_plan_route4_completes_batch_with_no_change() -> None:
    body = _body_text(rows=[("v0.20.0", "syncing", "b1", "-")])
    completion = {"batch": "b1", "completed_targets": ["v0.20.0"], "published_tag": None}
    plan = build_task_plan(_task_input(body=body, completion=completion))

    assert plan.route == 4
    updates = plan.state_table_updates
    assert len(updates) == 1
    assert updates[0].status == "N/A"
    assert updates[0].starter_release is None
    assert "変更なし" in (updates[0].reason or "")


def test_task_plan_route5_reapplies_completion_idempotently() -> None:
    """指摘2回帰: 状態表更新の部分失敗後、同じ completion を再実行しても同一の結果になる。"""
    body = _body_text(
        rows=[
            ("v0.20.0", "syncing", "b1", "-"),
            ("v0.20.1", "syncing", "b1", "-"),
        ]
    )
    completion = {
        "batch": "b1",
        "completed_targets": ["v0.20.0", "v0.20.1"],
        "published_tag": "kaji-v0.20.1",
    }
    first = build_task_plan(_task_input(body=body, completion=completion))
    applied_body = first.next_body
    assert applied_body is not None

    second = build_task_plan(_task_input(body=applied_body, completion=completion))

    assert second.route == 5
    assert second.decision == "COMPLETED"
    assert second.next_body is None
    assert second.state_table_updates == first.state_table_updates


def test_task_plan_completion_to_promotion_sequence_never_aborts() -> None:
    """指摘2回帰: 完了済み batch の存在が後続 batch の昇格を妨げない。"""
    body = _body_text(
        rows=[
            ("v0.20.0", "syncing", "b1", "-"),
            ("v0.20.1", "syncing", "b1", "-"),
            ("v0.20.2", "open", "-", "-"),
        ]
    )
    completion = {
        "batch": "b1",
        "completed_targets": ["v0.20.0", "v0.20.1"],
        "published_tag": "kaji-v0.20.1",
    }
    completed = build_task_plan(_task_input(body=body, completion=completion))
    assert completed.decision == "COMPLETED"

    # partial state-table-update failure: re-run route 5 before promoting.
    replayed = build_task_plan(_task_input(body=completed.next_body, completion=completion))
    assert replayed.route == 5
    assert replayed.state_table_updates == completed.state_table_updates

    promoted = build_task_plan(_task_input(body=completed.next_body))
    assert promoted.route == 2
    assert promoted.batch == "b2"
    assert promoted.covered_targets == ["v0.20.2"]

    all_done = parse_tracking_issue_body(promoted.next_body or "")
    finished_text = render_tracking_issue_body(
        TrackingIssueBody(
            starter_repo=all_done.starter_repo,
            starter_path=all_done.starter_path,
            tasks=tuple(
                TrackingTaskRow(
                    target_kaji_release=t.target_kaji_release,
                    status="done",
                    batch=t.batch,
                    result="N/A" if t.target_kaji_release != "v0.20.2" else "kaji-v0.20.2",
                )
                if t.status == "syncing"
                else t
                for t in all_done.tasks
            ),
        )
    )
    final_plan = build_task_plan(_task_input(body=finished_text))
    assert final_plan.route == 3
    assert final_plan.close_allowed is True


@pytest.mark.parametrize(
    "completion",
    [
        {"batch": "b1", "completed_targets": ["v0.20.0"], "published_tag": "kaji-v0.20.0"},
        {
            "batch": "b1",
            "completed_targets": ["v0.20.0", "v0.20.1", "v0.20.99"],
            "published_tag": "kaji-v0.20.1",
        },
        {"batch": "b1", "completed_targets": ["v0.30.0"], "published_tag": "kaji-v0.30.0"},
        {"batch": "b9", "completed_targets": ["v0.20.0", "v0.20.1"], "published_tag": None},
    ],
)
def test_task_plan_route6_aborts_on_set_mismatch(completion: dict[str, object]) -> None:
    body = _body_text(
        rows=[
            ("v0.20.0", "syncing", "b1", "-"),
            ("v0.20.1", "syncing", "b1", "-"),
        ]
    )
    plan = build_task_plan(_task_input(body=body, completion=completion))

    assert plan.route == 6
    assert plan.decision == "ABORT"
    assert plan.reason


def test_task_plan_route6_aborts_when_done_batch_result_mismatches_recomputed_value() -> None:
    body = _body_text(
        rows=[
            ("v0.20.0", "done", "b1", "N/A"),
            ("v0.20.1", "done", "b1", "N/A"),
        ]
    )
    completion = {
        "batch": "b1",
        "completed_targets": ["v0.20.0", "v0.20.1"],
        "published_tag": "kaji-v0.20.1",
    }
    plan = build_task_plan(_task_input(body=body, completion=completion))

    assert plan.route == 6
    assert plan.decision == "ABORT"


@pytest.mark.parametrize(
    ("body_rows", "issue_state"),
    [
        ([("v0.20.0", "syncing", "b1", "-"), ("v0.20.1", "syncing", "b2", "-")], "open"),
        ([("v0.20.0", "open", "-", "-")], "closed"),
        ([("v0.20.0", "syncing", "b1", "-")], "closed"),
        (
            [
                ("v0.20.0", "done", "b1", "kaji-v0.20.0"),
                ("v0.20.1", "done", "b1", "kaji-v0.20.1"),
            ],
            "open",
        ),
    ],
)
def test_task_plan_route7_aborts_on_observation_contradictions(
    body_rows: list[tuple[str, str, str, str]], issue_state: str
) -> None:
    body = _body_text(rows=body_rows)
    plan = build_task_plan(_task_input(body=body, issue_state=issue_state))

    assert plan.route == 7
    assert plan.decision == "ABORT"
    assert plan.reason


def test_task_plan_route7_aborts_when_body_fails_to_parse() -> None:
    plan = build_task_plan(_task_input(body="not a v1 tracking body"))

    assert plan.route == 7
    assert plan.decision == "ABORT"


# --- TaskCompletion / TrackingIssueObservation validation -------------------


def test_task_completion_rejects_invalid_batch_id() -> None:
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        TaskCompletion.model_validate(
            {"batch": "batch-1", "completed_targets": ["v0.20.0"], "published_tag": None}
        )


def test_tracking_issue_observation_requires_issue_id_and_body() -> None:
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        TrackingIssueObservation.model_validate({"issue_id": 1})
