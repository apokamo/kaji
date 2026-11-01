"""Medium: pr-verify SKILL.md のレビュースレッド解決手順を静的に検証する（Issue #528）。

集約は agent が SKILL.md に従って行う手順であり kaji のコードではないため、手順の欠落を
キー文字列の存在確認で検出する（文面の言い回しに過度に依存しない）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.medium

_SKILL = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "pr-verify" / "SKILL.md"


@pytest.fixture(scope="module")
def text() -> str:
    return _SKILL.read_text(encoding="utf-8")


def test_calls_resolve_thread_with_root_comment_id(text: str) -> None:
    assert "kaji pr resolve-thread [pr_id] --comment [root_comment_id]" in text


def test_review_comments_fetch_includes_id_and_in_reply_to_id(text: str) -> None:
    fetch_lines = [ln for ln in text.splitlines() if "kaji pr review-comments [pr_id]" in ln]
    assert fetch_lines, "review-comments fetch command missing"
    assert any("id: .id" in ln and "in_reply_to_id: .in_reply_to_id" in ln for ln in fetch_lines)


def test_has_thread_resolution_step(text: str) -> None:
    assert "### Step 3.5: 確認済みスレッドの解決" in text


def test_thread_aggregation_rules(text: str) -> None:
    # 根コメント ID でスレッドごとにグループ化する
    assert "根コメント ID" in text
    assert "in_reply_to_id" in text
    # 1 件でも未解消・再反論・一部受け入れが残るスレッドは resolve しない
    for keyword in ("要再修正", "再反論", "一部受け入れ"):
        assert keyword in text
    assert "開いたまま" in text
    # 解決対象スレッドごとに 1 回だけ実行する
    assert "1 回だけ" in text


def test_failure_does_not_change_verdict(text: str) -> None:
    assert "スレッド解決の成否は status に影響しない" in text


def test_result_comment_has_thread_resolution_table(text: str) -> None:
    # Approve / Changes Requested の両テンプレートに表がある
    assert text.count("### レビュースレッドの解決") >= 2
    for result in ("resolved", "already_resolved", "失敗:", "未解決のまま:"):
        assert result in text


def test_completion_report_lists_unresolved_threads(text: str) -> None:
    report = text.split("### Step 5: 完了報告", 1)[1].split("## Verdict 出力", 1)[0]
    assert "解決できなかったスレッド" in report
