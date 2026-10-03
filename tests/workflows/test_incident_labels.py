"""incident ラベルを kaji 既定名へ揃えた状態の不変条件（Issue #457）。

``.github/labels.yml`` / ``.kaji/config.toml`` / incident-* skill が、#434 で決めた既定名
（``kaji:`` 接頭辞付き）と整合していることを、ファイル読み取りだけで検証する。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from kaji_harness.config import IncidentConfig, KajiConfig

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LABELS_PATH = REPO_ROOT / ".github" / "labels.yml"
SKILLS_DIR = REPO_ROOT / ".claude" / "skills"

EXPECTED_LABEL_COUNT = 29
DEFAULT_INCIDENT_LABELS = {
    "kaji:incident",
    "kaji:incident:investigating",
    "kaji:incident:mitigated",
    "kaji:incident:resolved",
    "kaji:incident:cause:internal",
    "kaji:incident:cause:upstream",
    "kaji:incident:cause:environment",
    "kaji:incident:cause:transient",
}
# 旧名（接頭辞なし）のラベルを引用符またはバッククォートで囲んだ表記。
LEGACY_LABEL_TOKEN = re.compile(r"""["`]incident(:[a-z]+)*["`]""")


def _label_names() -> list[str]:
    data = yaml.safe_load(LABELS_PATH.read_text(encoding="utf-8"))
    names = [entry["name"] for entry in data["labels"]]
    assert all(isinstance(name, str) for name in names)
    return names


@pytest.mark.medium
class TestLabelsYml:
    """``.github/labels.yml`` の incident ラベル定義。"""

    def test_label_count_and_uniqueness(self) -> None:
        names = _label_names()
        assert len(names) == EXPECTED_LABEL_COUNT
        assert len(set(names)) == len(names)

    def test_incident_labels_are_default_names(self) -> None:
        incident_names = {name for name in _label_names() if "incident" in name}
        assert incident_names == DEFAULT_INCIDENT_LABELS


@pytest.mark.medium
class TestConfigMatchesLabels:
    """kaji 自身の ``[incident]`` 設定とラベル定義の整合。"""

    def test_config_labels_are_defined_in_labels_yml(self) -> None:
        incident = KajiConfig.discover(REPO_ROOT).incident
        names = set(_label_names())
        for label in (
            incident.kind_label,
            incident.initial_status_label,
            incident.transient_label,
        ):
            assert label in names, f"{label!r} is not defined in .github/labels.yml"

    def test_kaji_runs_with_default_incident_config(self) -> None:
        assert KajiConfig.discover(REPO_ROOT).incident == IncidentConfig()


@pytest.mark.medium
class TestSkillsHaveNoFixedLabelName:
    """incident-* skill が固定の旧ラベル名を持たない。"""

    @pytest.mark.parametrize(
        "skill", ["incident-investigate", "incident-review", "incident-report"]
    )
    def test_no_legacy_label_token(self, skill: str) -> None:
        text = (SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")
        assert LEGACY_LABEL_TOKEN.findall(text) == []

    @pytest.mark.parametrize("skill", ["incident-investigate", "incident-review"])
    def test_step0_references_kind_label(self, skill: str) -> None:
        text = (SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")
        step0 = text.split("### Step 0", 1)[1].split("### Step 1", 1)[0]
        assert "KIND_LABEL" in step0
