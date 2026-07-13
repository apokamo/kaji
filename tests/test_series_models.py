"""Small tests for sequential series schema and pure decisions."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from kaji_harness.providers.github import GitHubProvider
from kaji_harness.series import (
    SeriesConfig,
    SeriesMember,
    evaluate_member_gate,
    series_fingerprint,
)

pytestmark = pytest.mark.small


def _config(**overrides: object) -> SeriesConfig:
    data: dict[str, object] = {
        "id": "release-series",
        "strategy": "sequential",
        "members": [
            {"issue": 10, "workflow": ".kaji/wf/dev.yaml"},
            {"issue": 11, "workflow": ".kaji/wf/docs.yaml"},
        ],
        "on_failure": "stop",
    }
    data.update(overrides)
    return SeriesConfig.model_validate(data)


@pytest.mark.parametrize(
    ("overrides", "error_fragment"),
    [
        ({"id": "Bad_ID"}, "id"),
        ({"members": []}, "members"),
        ({"members": [{"issue": 0, "workflow": "x.yaml"}]}, "issue"),
        ({"members": [{"issue": "10", "workflow": "x.yaml"}]}, "issue"),
        (
            {
                "members": [
                    {"issue": 10, "workflow": "a.yaml"},
                    {"issue": 10, "workflow": "b.yaml"},
                ]
            },
            "duplicate",
        ),
        ({"strategy": "parallel"}, "strategy"),
        ({"on_failure": "continue"}, "on_failure"),
        ({"description": "not allowed"}, "description"),
    ],
)
def test_series_config_rejects_invalid_input(
    overrides: dict[str, object], error_fragment: str
) -> None:
    with pytest.raises(ValidationError, match=error_fragment):
        _config(**overrides)


def test_validation_aggregates_multiple_field_errors() -> None:
    with pytest.raises(ValidationError) as exc_info:
        SeriesConfig.model_validate(
            {
                "id": "Bad_ID",
                "strategy": "parallel",
                "members": [],
                "on_failure": "continue",
            }
        )
    assert exc_info.value.error_count() >= 4


def test_parent_issue_does_not_change_member_semantics() -> None:
    without_parent = _config()
    with_parent = _config(parent_issue=291)
    assert without_parent.members == with_parent.members
    assert with_parent.parent_issue == 291


def test_fingerprint_is_canonical_and_order_sensitive() -> None:
    config = _config()
    same = SeriesConfig.model_validate_json(config.model_dump_json())
    reversed_config = config.model_copy(update={"members": list(reversed(config.members))})
    changed_workflow = config.model_copy(
        update={
            "members": [
                SeriesMember(issue=10, workflow=".kaji/wf/dev-thorough.yaml"),
                config.members[1],
            ]
        }
    )
    assert series_fingerprint(config) == series_fingerprint(same)
    assert series_fingerprint(config).startswith("sha256:")
    assert series_fingerprint(config) != series_fingerprint(reversed_config)
    assert series_fingerprint(config) != series_fingerprint(changed_workflow)


@pytest.mark.parametrize(
    ("exit_code", "state", "reason", "success", "gate"),
    [
        (0, "closed", "completed", True, "closed_completed"),
        (1, "closed", "completed", False, "exit:1"),
        (0, "open", "", False, "mismatch:open/"),
        (0, "closed", "not_planned", False, "mismatch:closed/not_planned"),
        (0, "closed", "duplicate", False, "mismatch:closed/duplicate"),
        (0, "closed", "reopened", False, "mismatch:closed/reopened"),
        (0, "closed", "future_value", False, "mismatch:closed/future_value"),
    ],
)
def test_member_gate_allowlists_only_closed_completed(
    exit_code: int, state: str, reason: str, success: bool, gate: str
) -> None:
    result = evaluate_member_gate(exit_code, state, reason)
    assert result.success is success
    assert result.gate == gate


def test_github_provider_normalizes_state_reason(tmp_path: Path) -> None:
    provider = GitHubProvider(repo="owner/name", repo_root=tmp_path)
    payload = {
        "number": 313,
        "title": "series",
        "body": "",
        "state": "CLOSED",
        "stateReason": "DUPLICATE",
        "labels": [],
        "comments": [],
    }
    completed = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=json.dumps(payload), stderr=""
    )
    with (
        patch("kaji_harness.providers.github.shutil.which", return_value="/usr/bin/gh"),
        patch("kaji_harness.providers.github.subprocess.run", return_value=completed),
    ):
        issue = provider.view_issue("313")
    assert issue.state_reason == "duplicate"


@pytest.mark.parametrize(
    ("raw_reason", "expected"),
    [
        ("COMPLETED", "completed"),
        ("NOT_PLANNED", "not_planned"),
        ("DUPLICATE", "duplicate"),
        ("REOPENED", "reopened"),
        (None, ""),
    ],
)
def test_github_state_reason_value_range_is_normalized(
    raw_reason: str | None, expected: str
) -> None:
    issue = GitHubProvider._parse_issue_payload(
        {
            "number": 313,
            "title": "series",
            "state": "CLOSED",
            "stateReason": raw_reason,
        }
    )
    assert issue.state_reason == expected


def test_issue_state_reason_defaults_to_empty() -> None:
    from kaji_harness.providers import Issue

    assert Issue(id="1", title="t", body="", state="open").state_reason == ""
