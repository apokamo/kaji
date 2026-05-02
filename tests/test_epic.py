"""Tests for kaji_harness.epic — EPIC schema and validator.

Issue: #164
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from kaji_harness.epic import (
    EpicConfig,
    EpicMember,
    EpicValidationError,
    load_epic,
)


def _epic(*members: dict) -> EpicConfig:
    return EpicConfig(name="t", members=[EpicMember(**m) for m in members])


# ============================================================
# Small tests — validation rules
# ============================================================


class TestEpicValidationSmall:
    @pytest.mark.small
    def test_valid_simple_dag(self) -> None:
        epic = _epic({"issue": 1}, {"issue": 2, "depends_on": [1]})
        assert [m.issue for m in epic.members] == [1, 2]

    @pytest.mark.small
    def test_empty_members_rejected(self) -> None:
        with pytest.raises(EpicValidationError) as ei:
            EpicConfig(name="t", members=[])
        assert any("members must not be empty" in e for e in ei.value.errors)

    @pytest.mark.small
    def test_duplicate_issue_rejected(self) -> None:
        with pytest.raises(EpicValidationError) as ei:
            _epic({"issue": 1}, {"issue": 1})
        assert any("duplicate issue" in e for e in ei.value.errors)

    @pytest.mark.small
    def test_unknown_dependency_rejected(self) -> None:
        with pytest.raises(EpicValidationError) as ei:
            _epic({"issue": 1, "depends_on": [999]})
        assert any("999" in e and "not in members" in e for e in ei.value.errors)

    @pytest.mark.small
    def test_self_loop_rejected(self) -> None:
        with pytest.raises(EpicValidationError) as ei:
            _epic({"issue": 1, "depends_on": [1]})
        assert any("self-loop" in e for e in ei.value.errors)

    @pytest.mark.small
    def test_cycle_rejected(self) -> None:
        with pytest.raises(EpicValidationError) as ei:
            _epic(
                {"issue": 1, "depends_on": [2]},
                {"issue": 2, "depends_on": [1]},
            )
        assert any("cyclic dependency" in e for e in ei.value.errors)

    @pytest.mark.small
    def test_duplicate_merge_order_rejected(self) -> None:
        with pytest.raises(EpicValidationError) as ei:
            _epic(
                {"issue": 1, "merge_order": 1},
                {"issue": 2, "merge_order": 1},
            )
        assert any("duplicate merge_order" in e for e in ei.value.errors)

    @pytest.mark.small
    def test_parallel_group_with_direct_dependency_rejected(self) -> None:
        with pytest.raises(EpicValidationError) as ei:
            _epic(
                {"issue": 1, "parallel_group": "g"},
                {"issue": 2, "depends_on": [1], "parallel_group": "g"},
            )
        assert any("parallel_group" in e and "depends_on" in e for e in ei.value.errors)

    @pytest.mark.small
    def test_parallel_group_with_transitive_dependency_rejected(self) -> None:
        with pytest.raises(EpicValidationError) as ei:
            _epic(
                {"issue": 1, "parallel_group": "g"},
                {"issue": 2, "depends_on": [1]},
                {"issue": 3, "depends_on": [2], "parallel_group": "g"},
            )
        assert any("parallel_group" in e for e in ei.value.errors)

    @pytest.mark.small
    def test_merge_order_reverse_direction_rejected(self) -> None:
        # A=2 depends on B=1; merge_order(A)=1, merge_order(B)=2 → 逆転
        with pytest.raises(EpicValidationError) as ei:
            _epic(
                {"issue": 1, "merge_order": 2},
                {"issue": 2, "depends_on": [1], "merge_order": 1},
            )
        msg = "; ".join(ei.value.errors)
        assert "merge_order order conflicts with depends_on" in msg
        assert "2" in msg and "1" in msg

    @pytest.mark.small
    def test_merge_order_correct_direction_accepted(self) -> None:
        epic = _epic(
            {"issue": 1, "merge_order": 1},
            {"issue": 2, "depends_on": [1], "merge_order": 2},
        )
        assert epic.sorted_merge_order() == [1, 2]


class TestTopologicalOrderSmall:
    @pytest.mark.small
    def test_kahn_levels_when_no_explicit_group(self) -> None:
        epic = _epic(
            {"issue": 1},
            {"issue": 2, "depends_on": [1]},
            {"issue": 3, "depends_on": [1]},
            {"issue": 4, "depends_on": [2, 3]},
        )
        levels = epic.topological_order()
        assert levels == [[1], [2, 3], [4]]

    @pytest.mark.small
    def test_explicit_parallel_group_splits_level(self) -> None:
        epic = _epic(
            {"issue": 1},
            {"issue": 2, "depends_on": [1], "parallel_group": "frontend"},
            {"issue": 3, "depends_on": [1], "parallel_group": "backend"},
        )
        levels = epic.topological_order()
        # level 0 only has 1; level 1 splits into two groups
        assert levels[0] == [1]
        flattened = [i for lv in levels[1:] for i in lv]
        assert sorted(flattened) == [2, 3]
        # the two groups should be in separate levels (not co-located)
        assert all(len(lv) == 1 for lv in levels[1:])


class TestSortedMergeOrderSmall:
    @pytest.mark.small
    def test_explicit_then_topological_tail(self) -> None:
        epic = _epic(
            {"issue": 1, "merge_order": 1},
            {"issue": 2, "depends_on": [1], "merge_order": 2},
            {"issue": 3, "depends_on": [1]},
        )
        result = epic.sorted_merge_order()
        assert result[:2] == [1, 2]
        assert 3 in result


# ============================================================
# Small tests — load_epic / YAML
# ============================================================


class TestLoadEpicSmall:
    @pytest.mark.small
    def test_load_valid_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / "epic.yaml"
        path.write_text(
            textwrap.dedent(
                """
                name: rel
                description: x
                members:
                  - issue: 1
                  - issue: 2
                    depends_on: [1]
                """
            ).strip()
        )
        epic = load_epic(path)
        assert epic.name == "rel"
        assert len(epic.members) == 2

    @pytest.mark.small
    def test_load_invalid_yaml_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.yaml"
        path.write_text("name: x\nmembers: [\n")
        with pytest.raises(EpicValidationError):
            load_epic(path)

    @pytest.mark.small
    def test_load_non_mapping_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "list.yaml"
        path.write_text("- 1\n- 2\n")
        with pytest.raises(EpicValidationError) as ei:
            load_epic(path)
        assert any("YAML mapping" in e for e in ei.value.errors)
