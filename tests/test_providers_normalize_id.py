"""Tests for kaji_harness.providers.normalize_id.

local provider 削除（#448）後は ``"<n>"`` / ``"gh:<n>"`` のみを受理する。
"""

from __future__ import annotations

import pytest

from kaji_harness.providers import normalize_id

pytestmark = pytest.mark.small


@pytest.mark.parametrize(("raw", "expected"), [("153", "153"), ("gh:153", "153"), ("1", "1")])
def test_accepts_numeric_and_gh_prefix(raw: str, expected: str) -> None:
    assert normalize_id(raw) == expected


@pytest.mark.parametrize("raw", ["local-pc1-3", "pc1-3", "0", "007", "gh:0", "gh:007", "#153"])
def test_rejects_other_forms(raw: str) -> None:
    with pytest.raises(ValueError, match="expected '<number>' or 'gh:<number>'"):
        normalize_id(raw)


def test_rejects_empty_string() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        normalize_id("")
