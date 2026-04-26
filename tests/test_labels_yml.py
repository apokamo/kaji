"""Tests for .github/labels.yml — declarative label catalogue validation.

このテストは labels.yml の機械的妥当性のみを検証する。意味的な妥当性
（例: type:* と meta の分類が正しいか）は CODEOWNERS / レビューで担保する。

設計: docs/rfc/github-labels-standardization.md / draft/design/issue-154-labels-standardization.md
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

LABELS_YML = Path(__file__).resolve().parent.parent / ".github" / "labels.yml"
COLOR_RE = re.compile(r"^[0-9a-f]{6}$")


@pytest.fixture(scope="module")
def labels_config() -> dict[str, Any]:
    with LABELS_YML.open(encoding="utf-8") as f:
        config = yaml.safe_load(f)
    assert isinstance(config, dict), "labels.yml must parse to a mapping"
    return config


@pytest.mark.small
def test_labels_yml_is_valid(labels_config: dict[str, Any]) -> None:
    """labels.yml の機械的妥当性: parse / 必須フィールド / hex 形式 / 名前重複なし。"""
    labels = labels_config.get("labels")
    assert isinstance(labels, list) and labels, "labels must be a non-empty list"

    seen: set[str] = set()
    for index, label in enumerate(labels):
        assert isinstance(label, dict), f"label at index {index} must be a mapping"

        name = label.get("name")
        color = label.get("color")
        description = label.get("description")

        assert isinstance(name, str) and name, f"label at index {index} missing name"
        assert isinstance(color, str) and color, f"label '{name}' missing color"
        assert isinstance(description, str) and description, f"label '{name}' missing description"

        assert COLOR_RE.match(color), (
            f"label '{name}' has invalid color '{color}' (must be 6-char lowercase hex without '#')"
        )

        assert name not in seen, f"duplicate label name: '{name}'"
        seen.add(name)
