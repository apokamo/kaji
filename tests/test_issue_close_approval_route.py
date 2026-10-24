"""issue-close SKILL.md の承認の正本（起動経路別）の記述を検証する（Issue #523）。

review-poll が PASS 以外で ``review`` / ``pr-verify`` の PASS を経て close に到達した経路
（Step 2.5 の ``MODE=skip``）で、close が self-PR の空 ``reviewDecision`` を未承認と読んで
ABORT しないよう、次を固定する。

- 「いつ使うか」が GitHub の Approve を必須条件として読める表現を持たない
- 承認の正本が起動経路で決まり、self-PR の空 ``reviewDecision`` が正常であると明記されている
- Step 2.5.1 の ``MODE=skip`` で close が承認を再判定しないと明記されている
- ``reviewDecision`` / ``reviews`` / ``kaji-review`` マーカーを取得・判定する手順がない
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_SKILL = Path(__file__).resolve().parent.parent / ".claude/skills/issue-close/SKILL.md"
_CODE_BLOCK_RE = re.compile(r"```[^\n]*\n(.*?)\n```", re.DOTALL)


def _skill_text() -> str:
    return _SKILL.read_text(encoding="utf-8")


def _between(text: str, start: str, end: str) -> str:
    """``start`` 見出しから ``end`` 見出しの直前までを返す。"""
    begin = text.index(start)
    return text[begin : text.index(end, begin)]


def _when_to_use() -> str:
    return _between(_skill_text(), "## いつ使うか", "## 入力")


def _step_2_5_1() -> str:
    return _between(_skill_text(), "#### 2.5.1 適用判定", "#### 2.5.2")


def _table_row(section: str, first_cell_prefix: str) -> str:
    rows = [line for line in section.splitlines() if line.startswith(f"| {first_cell_prefix}")]
    assert len(rows) == 1, f"expected exactly one row starting with {first_cell_prefix!r}: {rows}"
    return rows[0]


@pytest.mark.small
def test_when_to_use_does_not_require_github_approve() -> None:
    assert "Approve" not in _when_to_use()


@pytest.mark.small
def test_when_to_use_defines_approval_source_per_route() -> None:
    section = _when_to_use()
    for phrase in (
        "承認の正本",
        "review-poll",
        "Step 2.5",
        "`review` / `pr-verify` の PASS",
        "手動起動",
        "起動した人間",
    ):
        assert phrase in section, phrase


@pytest.mark.small
def test_when_to_use_states_empty_review_decision_is_normal_for_self_pr() -> None:
    section = _when_to_use()
    assert "self-PR" in section
    assert "`reviewDecision` が空" in section
    assert "`reviews` が 0 件" in section
    assert "未承認を意味しない" in section


@pytest.mark.small
@pytest.mark.parametrize(
    "first_cell_prefix",
    [
        'exit 0 で `.source == "artifact"` かつ `.status` が PASS 以外',
        "exit 4",
    ],
)
def test_step_2_5_1_skip_rows_do_not_rejudge_approval(first_cell_prefix: str) -> None:
    row = _table_row(_step_2_5_1(), first_cell_prefix)
    assert "`MODE=skip`" in row
    assert "再判定しない" in row


@pytest.mark.small
def test_step_2_5_1_skip_excludes_github_review_state_from_merge_decision() -> None:
    section = _step_2_5_1()
    assert "`MODE=skip` で close は承認を再判定しない" in section
    for signal in ("`reviewDecision`", "`reviews`", "`kaji-review` マーカー"):
        assert signal in section, signal
    assert "merge 可否の根拠にも ABORT 理由にもしない" in section


@pytest.mark.small
def test_no_code_block_fetches_github_review_state() -> None:
    for block in _CODE_BLOCK_RE.findall(_skill_text()):
        assert "reviewDecision" not in block
        assert "kaji-review" not in block


@pytest.mark.small
def test_merge_commands_are_unchanged() -> None:
    step_3 = _between(_skill_text(), "### Step 3: PRのマージ", "### Step 4:")
    assert 'kaji pr merge [branch_name] --match-head-commit "$REVIEWED_SHA"' in step_3
    assert re.search(r"^kaji pr merge \[branch_name\]$", step_3, re.MULTILINE)
