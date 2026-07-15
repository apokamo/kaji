"""Verdict marker metadata contract tests for starter cross-skill handoff."""

from __future__ import annotations

import pytest

from kaji_harness.providers.markers import (
    build_kaji_verdict_marker,
    parse_kaji_verdict_marker,
    resolve_verdict_marker,
)

pytestmark = pytest.mark.small


def test_build_marker_sorts_validated_metadata() -> None:
    marker = build_kaji_verdict_marker(
        "review-starter-update",
        "PASS",
        {"target": "v0.16.0", "candidate": "abc123", "base": "def456"},
    )

    assert marker == (
        "<!-- kaji-verdict: step=review-starter-update status=PASS "
        "base=def456 candidate=abc123 target=v0.16.0 -->"
    )


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        ({"Bad-Key": "value"}, "metadata key"),
        ({"target": "v1.0.0 -->"}, "metadata value"),
        ({"target": ""}, "metadata value"),
    ],
)
def test_build_marker_rejects_invalid_metadata(metadata: dict[str, str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        build_kaji_verdict_marker("review-starter-update", "PASS", metadata)


def test_parse_marker_supports_metadata_and_legacy_marker() -> None:
    marker = parse_kaji_verdict_marker(
        "<!-- kaji-verdict: step=review-starter-update status=PASS "
        "base=def456 candidate=abc123 target=v0.16.0 -->"
    )
    legacy = parse_kaji_verdict_marker("<!-- kaji-verdict: step=review-code status=RETRY -->")

    assert marker is not None
    assert marker.step == "review-starter-update"
    assert marker.status == "PASS"
    assert marker.meta == {
        "base": "def456",
        "candidate": "abc123",
        "target": "v0.16.0",
    }
    assert legacy is not None
    assert legacy.meta == {}


@pytest.mark.parametrize(
    "line",
    [
        "<!-- kaji-verdict: step=review-starter-update status=PASS bad -->",
        "<!-- kaji-verdict: step=review-starter-update status=PASS target=x target=y -->",
        "prefix <!-- kaji-verdict: step=review-starter-update status=PASS -->",
    ],
)
def test_parse_marker_fails_closed(line: str) -> None:
    assert parse_kaji_verdict_marker(line) is None


def test_resolve_cli_metadata_requires_verdict_flags() -> None:
    with pytest.raises(ValueError, match="specified together"):
        resolve_verdict_marker(None, None, ["target=v0.16.0"])

    marker = resolve_verdict_marker(
        "review-starter-update",
        "PASS",
        ["candidate=abc123", "target=v0.16.0", "base=def456"],
    )
    assert marker is not None
    assert "base=def456 candidate=abc123 target=v0.16.0" in marker


def test_resolve_cli_metadata_rejects_duplicate_keys() -> None:
    with pytest.raises(ValueError, match="duplicate verdict metadata key"):
        resolve_verdict_marker(
            "review-starter-update",
            "PASS",
            ["target=v0.16.0", "target=v0.17.0"],
        )
