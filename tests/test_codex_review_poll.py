"""Tests for kaji_harness.scripts.codex_review_poll."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from kaji_harness.review_poll_evidence import EVIDENCE_FILENAME, load_evidence
from kaji_harness.scripts import codex_review_poll as mod
from kaji_harness.scripts.codex_review_poll import (
    BOT_ID,
    PollResult,
    _default_emit,
    classify,
    format_heartbeat,
    parse_review_summary,
    run_polling,
)
from kaji_harness.verdict import parse_verdict_block

FIXTURES = Path(__file__).parent / "fixtures" / "codex_review_poll"
HEAD = "abc123def4567890abc123def4567890abc123de"
# head commit committedDate (ISO8601 UTC). PR #181 head 実観測値 = 2026-05-24T08:05:07Z
HEAD_AT = "2026-05-24T08:05:07Z"
SHORT = HEAD[:7]
OTHER_HEAD = "f" * 40
OWNER, REPO, PR = "apokamo", "kaji", 182
PR_URL = f"https://github.com/{OWNER}/{REPO}/pull/{PR}"
# fixtures の +1 (id=1) は 08:25:28Z。summary の完了時刻はその前の秒に置く。
COMPLETED_AT = "2026-05-24T08:25:20.391219Z"
FIXED_NOW = datetime(2026, 5, 24, 9, 0, 0, tzinfo=UTC)


def _load(name: str) -> list[dict[str, Any]]:
    return json.loads((FIXTURES / name).read_text())


def _summary_body(
    *,
    short: str | None = SHORT,
    status: str = "✅ **Completed**",
    completed_at: str | None = COMPLETED_AT,
    marker: bool = True,
    review_name: str = "📝 **Code Review**",
) -> str:
    """bot の review summary comment 本文（実 comment の構造を再現する）。"""
    time_part = (
        f' <relative-time datetime="{completed_at}">{completed_at}</relative-time>'
        if completed_at is not None
        else ""
    )
    commit_cell = f"`{short}`" if short is not None else "-"
    head = "<!-- codex-pull-request-review-summary -->\n\n" if marker else ""
    return (
        f"{head}## Codex Review Summary\n\n"
        "| Review | Status | Commit | Review trigger |\n"
        "| --- | --- | --- | --- |\n"
        f"| {review_name} | {status}{time_part} | {commit_cell} | PR opened |\n"
    )


def _summary_comment(
    *,
    comment_id: int = 501,
    updated_at: str = "2026-05-24T08:25:21Z",
    user_id: int = BOT_ID,
    **body_kwargs: Any,
) -> dict[str, Any]:
    return {
        "id": comment_id,
        "html_url": f"{PR_URL}#issuecomment-{comment_id}",
        "user": {"id": user_id, "login": "chatgpt-codex-connector[bot]"},
        "created_at": "2026-05-24T08:10:00Z",
        "updated_at": updated_at,
        "body": _summary_body(**body_kwargs),
    }


def _plus_one(created_at: str = "2026-05-24T08:25:28Z", *, reaction_id: int = 1) -> dict[str, Any]:
    return {
        "id": reaction_id,
        "user": {"id": BOT_ID, "login": "chatgpt-codex-connector[bot]"},
        "content": "+1",
        "created_at": created_at,
    }


# --- parse_review_summary (Small) -------------------------------------------


@pytest.mark.small
class TestParseReviewSummary:
    def test_real_pr163_comment_is_parsed(self) -> None:
        comments = _load("comments_review_summary_pr163.json")
        summary = parse_review_summary(comments, BOT_ID)
        assert summary is not None
        assert summary.comment_id == 5577710898
        assert summary.url.endswith("#issuecomment-5577710898")
        assert summary.commit_short_sha == "91d11b1"
        assert summary.status == "Completed"
        assert summary.completed_at == "2026-09-08T01:33:32.391219Z"
        assert summary.updated_at == "2026-09-08T01:33:33Z"

    def test_synthetic_comment_is_parsed(self) -> None:
        summary = parse_review_summary([_summary_comment()], BOT_ID)
        assert summary is not None
        assert summary.commit_short_sha == SHORT
        assert summary.completed_at == COMPLETED_AT

    def test_leading_whitespace_before_marker_is_accepted(self) -> None:
        comment = _summary_comment()
        comment["body"] = "\n  " + comment["body"]
        assert parse_review_summary([comment], BOT_ID) is not None

    def test_untrusted_user_comment_is_ignored(self) -> None:
        assert parse_review_summary([_summary_comment(user_id=1)], BOT_ID) is None

    def test_comment_without_marker_is_ignored(self) -> None:
        assert parse_review_summary([_summary_comment(marker=False)], BOT_ID) is None

    def test_no_code_review_row_returns_none(self) -> None:
        assert (
            parse_review_summary([_summary_comment(review_name="Security Review")], BOT_ID) is None
        )

    def test_two_code_review_rows_return_none(self) -> None:
        comment = _summary_comment()
        comment["body"] += "| 📝 **Code Review** | ✅ **Completed** | `1234567` | again |\n"
        assert parse_review_summary([comment], BOT_ID) is None

    @pytest.mark.parametrize("short", ["91d11b", "ZZZZZZZ", None, "91D11B1"])
    def test_invalid_commit_cell_returns_none(self, short: str | None) -> None:
        assert parse_review_summary([_summary_comment(short=short)], BOT_ID) is None

    def test_in_progress_status_is_not_completed(self) -> None:
        summary = parse_review_summary(
            [_summary_comment(status="⏳ **In progress**", completed_at=None)], BOT_ID
        )
        assert summary is not None
        assert summary.status != "Completed"
        assert summary.completed_at is None

    def test_completed_without_datetime_has_no_completed_at(self) -> None:
        summary = parse_review_summary([_summary_comment(completed_at=None)], BOT_ID)
        assert summary is not None
        assert summary.status == "Completed"
        assert summary.completed_at is None

    def test_latest_updated_summary_wins(self) -> None:
        old = _summary_comment(comment_id=1, updated_at="2026-05-24T08:00:00Z", short="1111111")
        new = _summary_comment(comment_id=2, updated_at="2026-05-24T08:25:21Z")
        for ordering in ([old, new], [new, old]):
            summary = parse_review_summary(ordering, BOT_ID)
            assert summary is not None and summary.comment_id == 2

    def test_tied_updated_at_is_ambiguous(self) -> None:
        a = _summary_comment(comment_id=1)
        b = _summary_comment(comment_id=2)
        assert parse_review_summary([a, b], BOT_ID) is None


# --- classify (Small) -------------------------------------------------------


@pytest.mark.small
class TestClassify:
    def test_plus_one_without_summary_is_not_pass(self) -> None:
        # reaction は SHA を持たない。summary なしの +1 単独では PASS にしない（Issue #429）
        reactions = _load("reactions_plus_one.json")
        assert classify(reactions, [], HEAD, HEAD_AT).state == "init"

    def test_summary_completed_with_plus_one_returns_done_pass(self) -> None:
        reactions = _load("reactions_plus_one.json")
        result = classify(reactions, [], HEAD, HEAD_AT, comments=[_summary_comment()])
        assert result.state == "done_pass"
        assert result.approval is not None
        assert result.approval.reaction_id == 1
        assert result.approval.reaction_created_at == "2026-05-24T08:25:28Z"
        assert result.approval.summary.comment_id == 501
        assert result.approval.summary.commit_short_sha == SHORT

    def test_old_plus_one_with_old_head_summary_is_not_pass(self) -> None:
        # 古い +1 が残り、新 head の commit 時刻より後（+1.created_at >= head_committed_at は成立）。
        # summary は旧 head の短縮 SHA で Completed → 新 head の承認ではない
        reactions = _load("reactions_plus_one.json")
        comments = [_summary_comment(short="1111111")]
        assert classify(reactions, [], HEAD, HEAD_AT, comments=comments).state == "init"

    def test_summary_in_progress_for_new_head_is_not_pass(self) -> None:
        reactions = _load("reactions_plus_one.json")
        comments = [_summary_comment(status="⏳ **In progress**", completed_at=None)]
        assert classify(reactions, [], HEAD, HEAD_AT, comments=comments).state == "init"

    def test_plus_one_before_completion_is_not_pass(self) -> None:
        reactions = [_plus_one("2026-05-24T08:25:19Z")]
        comments = [_summary_comment()]
        assert classify(reactions, [], HEAD, HEAD_AT, comments=comments).state == "init"

    @pytest.mark.parametrize(
        ("reaction_at", "expected"),
        [
            ("2026-05-24T08:25:19Z", "init"),  # 前の秒
            ("2026-05-24T08:25:20Z", "init"),  # 同じ秒: 順序不明なので PASS にしない
            ("2026-05-24T08:25:21Z", "done_pass"),  # 次の秒
            ("2026-05-24T08:25:28Z", "done_pass"),  # 実測に近い値
        ],
    )
    def test_same_second_boundary(self, reaction_at: str, expected: str) -> None:
        result = classify(
            [_plus_one(reaction_at)], [], HEAD, HEAD_AT, comments=[_summary_comment()]
        )
        assert result.state == expected

    def test_completed_at_on_exact_second_with_same_second_plus_one_is_not_pass(self) -> None:
        comments = [_summary_comment(completed_at="2026-05-24T08:25:20.000000Z")]
        result = classify([_plus_one("2026-05-24T08:25:20Z")], [], HEAD, HEAD_AT, comments=comments)
        assert result.state == "init"

    def test_plus_one_older_than_head_commit_is_not_pass(self) -> None:
        # summary の完了が head commit より前（= 別 commit 向けの完了）でも commit 時刻ガードは残る
        comments = [_summary_comment(completed_at="2026-05-24T06:00:00Z")]
        result = classify([_plus_one("2026-05-24T07:00:00Z")], [], HEAD, HEAD_AT, comments=comments)
        assert result.state == "init"

    def test_unparsable_times_are_not_pass(self) -> None:
        comments = [_summary_comment(completed_at="not-a-time")]
        assert classify([_plus_one()], [], HEAD, HEAD_AT, comments=comments).state == "init"
        assert (
            classify([_plus_one("garbage")], [], HEAD, HEAD_AT, comments=[_summary_comment()]).state
            == "init"
        )

    def test_stale_plus_one_only_keeps_state(self) -> None:
        # +1.created_at (07:00:00Z) < head_committed_at (08:05:07Z) -> freshness guard
        reactions = _load("reactions_plus_one_stale.json")
        result = classify(reactions, [], HEAD, HEAD_AT, comments=[_summary_comment()])
        assert result.state == "init"

    def test_fresh_and_stale_plus_one_returns_done_pass_with_fresh_reaction(self) -> None:
        reactions = _load("reactions_plus_one_fresh_and_stale.json")
        result = classify(reactions, [], HEAD, HEAD_AT, comments=[_summary_comment()])
        assert result.state == "done_pass"
        assert result.approval is not None and result.approval.reaction_id == 4

    def test_eyes_only_returns_in_progress(self) -> None:
        reactions = _load("reactions_eyes.json")
        assert classify(reactions, [], HEAD, HEAD_AT).state == "in_progress"

    def test_eyes_gone_with_summary_and_plus_one_returns_done_pass(self) -> None:
        reactions = _load("reactions_plus_one.json")
        result = classify(
            reactions, [], HEAD, HEAD_AT, comments=[_summary_comment()], prev_state="in_progress"
        )
        assert result.state == "done_pass"

    def test_untrusted_plus_one_with_summary_is_not_pass(self) -> None:
        reactions = [
            {
                "id": 99,
                "user": {"id": 1, "login": "apokamo"},
                "content": "+1",
                "created_at": "2026-05-24T09:00:00Z",
            }
        ]
        result = classify(reactions, [], HEAD, HEAD_AT, comments=[_summary_comment()])
        assert result.state == "init"

    def test_untrusted_summary_with_plus_one_is_not_pass(self) -> None:
        reactions = _load("reactions_plus_one.json")
        comments = [_summary_comment(user_id=1)]
        assert classify(reactions, [], HEAD, HEAD_AT, comments=comments).state == "init"

    def test_commented_review_on_current_head_returns_done_retry(self) -> None:
        # PR #176 シナリオ: reactions 0 件 + 現在 head の COMMENTED review
        reviews = _load("reviews_commented_current_head.json")
        assert classify([], reviews, HEAD, HEAD_AT).state == "done_retry"

    def test_commented_review_body_with_leading_newline_detected(self) -> None:
        # body 先頭改行ありケース
        reviews = _load("reviews_commented_current_head.json")
        assert reviews[0]["body"].startswith("\n")
        assert classify([], reviews, HEAD, HEAD_AT).state == "done_retry"

    def test_commented_review_on_old_head_is_ignored(self) -> None:
        reviews = _load("reviews_commented_old_head.json")
        result = classify([], reviews, HEAD, HEAD_AT)
        assert result.state == "init"

    def test_commented_review_on_old_head_does_not_block_pass(self) -> None:
        reviews = _load("reviews_commented_old_head.json")
        result = classify(
            _load("reactions_plus_one.json"), reviews, HEAD, HEAD_AT, comments=[_summary_comment()]
        )
        assert result.state == "done_pass"

    def test_retry_takes_priority_over_pass(self) -> None:
        reactions = _load("reactions_plus_one.json")
        reviews = _load("reviews_commented_current_head.json")
        result = classify(reactions, reviews, HEAD, HEAD_AT, comments=[_summary_comment()])
        assert result.state == "done_retry"

    def test_ambiguous_bot_review_on_head_blocks_pass(self) -> None:
        # marker なしの bot review が current head にある → 未解決の指摘があり得るので PASS にしない
        reviews = [
            {
                "id": 12,
                "user": {"id": BOT_ID, "login": "chatgpt-codex-connector[bot]"},
                "state": "COMMENTED",
                "commit_id": HEAD,
                "body": "Some other review body without the marker",
            }
        ]
        result = classify(
            _load("reactions_plus_one.json"), reviews, HEAD, HEAD_AT, comments=[_summary_comment()]
        )
        assert result.state == "init"
        assert "ambiguous bot review on head" in result.reason

    def test_non_bot_reactions_ignored(self) -> None:
        reactions = [
            {
                "id": 99,
                "user": {"id": 1, "login": "apokamo"},
                "content": "+1",
                "created_at": "2026-05-24T09:00:00Z",
            },
        ]
        assert classify(reactions, [], HEAD, HEAD_AT).state == "init"

    def test_login_match_but_id_mismatch_ignored(self) -> None:
        # bot rename / re-deploy 想定
        reactions = [
            {
                "id": 99,
                "user": {"id": 99999, "login": "chatgpt-codex-connector[bot]"},
                "content": "+1",
                "created_at": "2026-05-24T09:00:00Z",
            },
        ]
        assert classify(reactions, [], HEAD, HEAD_AT).state == "init"

    def test_empty_response_keeps_prev_state(self) -> None:
        assert classify([], [], HEAD, HEAD_AT, prev_state="init").state == "init"
        assert classify([], [], HEAD, HEAD_AT, prev_state="in_progress").state == "in_progress"

    def test_bot_heart_reaction_ignored(self) -> None:
        reactions = [
            {
                "id": 99,
                "user": {"id": BOT_ID, "login": "chatgpt-codex-connector[bot]"},
                "content": "heart",
                "created_at": "2026-05-24T09:00:00Z",
            },
        ]
        assert classify(reactions, [], HEAD, HEAD_AT).state == "init"

    def test_bot_review_with_non_codex_body_ignored(self) -> None:
        reviews = [
            {
                "id": 12,
                "user": {"id": BOT_ID, "login": "chatgpt-codex-connector[bot]"},
                "state": "COMMENTED",
                "commit_id": HEAD,
                "body": "Some other review body without the marker",
            }
        ]
        assert classify([], reviews, HEAD, HEAD_AT).state == "init"

    def test_bot_approved_review_does_not_trigger_retry(self) -> None:
        reviews = [
            {
                "id": 13,
                "user": {"id": BOT_ID, "login": "chatgpt-codex-connector[bot]"},
                "state": "APPROVED",
                "commit_id": HEAD,
                "body": "### 💡 Codex Review\n\nLGTM",
            }
        ]
        assert classify([], reviews, HEAD, HEAD_AT).state == "init"


# --- run_polling (Medium) ---------------------------------------------------


class _FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, secs: float) -> None:
        self.t += secs


def _snap(
    reactions: list[dict[str, Any]] | None = None,
    reviews: list[dict[str, Any]] | None = None,
    comments: list[dict[str, Any]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    return {"reactions": reactions or [], "reviews": reviews or [], "comments": comments or []}


def _pass_snap() -> dict[str, list[dict[str, Any]]]:
    """summary Completed + その後の +1（PASS 候補になる 1 poll 分の状態）。"""
    return _snap(_load("reactions_plus_one.json"), comments=[_summary_comment()])


def _pr_object(head: str = HEAD, state: str = "open") -> dict[str, Any]:
    return {"head": {"sha": head}, "state": state, "html_url": PR_URL}


class _FakeGh:
    """`_gh_api` / `_gh_api_object` の fake。path の末尾で取得対象を判別する。

    `snapshots` は poll ごとの reactions / reviews / comments（reactions 取得で次へ進み、
    尽きたら最後を繰り返す）。`pull_heads` は `pulls/{n}` を取得する呼び出しごとの head
    （poll 中・確定直前・確定直後の順。尽きたら最後を繰り返す）。
    """

    def __init__(
        self,
        snapshots: list[dict[str, list[dict[str, Any]]]],
        *,
        pull_heads: list[str] | None = None,
        pull_state: str = "open",
        commits: list[dict[str, Any]] | None = None,
        fail_first: dict[str, int] | None = None,
    ) -> None:
        self.snapshots = snapshots
        self.pull_heads = pull_heads or [HEAD]
        self.pull_state = pull_state
        self.commits = commits if commits is not None else [{"sha": HEAD}]
        self.fail_first = dict(fail_first or {})
        self.snapshot_idx = -1
        self.pull_calls = 0
        self.calls: list[str] = []

    def _maybe_fail(self, kind: str, path: str) -> None:
        if self.fail_first.get(kind, 0) > 0:
            self.fail_first[kind] -= 1
            raise subprocess.CalledProcessError(1, ["gh", "api", path])

    def _snapshot(self) -> dict[str, list[dict[str, Any]]]:
        return self.snapshots[min(max(self.snapshot_idx, 0), len(self.snapshots) - 1)]

    def api(self, path: str) -> list[dict[str, Any]]:
        kind = path.rsplit("/", 1)[1]
        self.calls.append(kind)
        self._maybe_fail(kind, path)
        if kind == "reactions":
            self.snapshot_idx += 1
            return self._snapshot()["reactions"]
        if kind == "reviews":
            return self._snapshot()["reviews"]
        if kind == "comments":
            return self._snapshot()["comments"]
        if kind == "commits":
            return self.commits
        raise AssertionError(f"unexpected list path: {path}")

    def api_object(self, path: str) -> dict[str, Any]:
        assert path.endswith(f"/pulls/{PR}"), path
        self.calls.append("pulls")
        self._maybe_fail("pulls", path)
        head = self.pull_heads[min(self.pull_calls, len(self.pull_heads) - 1)]
        self.pull_calls += 1
        return _pr_object(head, self.pull_state)


def _install(monkeypatch: pytest.MonkeyPatch, fake: _FakeGh) -> _FakeGh:
    monkeypatch.setattr(mod, "_gh_api", fake.api)
    monkeypatch.setattr(mod, "_gh_api_object", fake.api_object)
    return fake


def _run(evidence_path: Path | None, **kwargs: Any) -> PollResult:
    clock = _FakeClock()
    params: dict[str, Any] = {
        "pr_number": PR,
        "owner": OWNER,
        "repo": REPO,
        "head_sha": HEAD,
        "head_committed_at": HEAD_AT,
        "poll_interval_sec": 10,
        "no_reaction_timeout_sec": 60,
        "in_progress_timeout_sec": 1800,
        "eyes_grace_sec": 10,
        "now": clock.now,
        "sleep": clock.sleep,
        "evidence_path": evidence_path,
        "utcnow": lambda: FIXED_NOW,
    }
    params.update(kwargs)
    return run_polling(**params)


@pytest.fixture
def evidence_path(tmp_path: Path) -> Path:
    return tmp_path / "attempt-001" / EVIDENCE_FILENAME


@pytest.mark.medium
class TestRunPolling:
    def _polling(
        self,
        monkeypatch: pytest.MonkeyPatch,
        evidence_path: Path,
        snapshots: list[dict[str, list[dict[str, Any]]]],
        **fake_kwargs: Any,
    ) -> PollResult:
        _install(monkeypatch, _FakeGh(snapshots, **fake_kwargs))
        return _run(evidence_path)

    def test_timeout_no_signals_returns_done_fallback(
        self, monkeypatch: pytest.MonkeyPatch, evidence_path: Path
    ) -> None:
        assert self._polling(monkeypatch, evidence_path, [_snap()]).state == "done_fallback"
        assert not evidence_path.exists()

    def test_stale_plus_one_only_returns_done_fallback(
        self, monkeypatch: pytest.MonkeyPatch, evidence_path: Path
    ) -> None:
        # freshness guard: stale +1 のみだと PASS せず timeout で fallback
        snap = _snap(_load("reactions_plus_one_stale.json"), comments=[_summary_comment()])
        assert self._polling(monkeypatch, evidence_path, [snap]).state == "done_fallback"

    def test_plus_one_without_summary_returns_done_fallback(
        self, monkeypatch: pytest.MonkeyPatch, evidence_path: Path
    ) -> None:
        # SHA を持たない +1 単独は承認にならない（Issue #429）。timeout で fallback へ縮退する
        snap = _snap(_load("reactions_plus_one.json"))
        assert self._polling(monkeypatch, evidence_path, [snap]).state == "done_fallback"
        assert not evidence_path.exists()

    def test_same_second_plus_one_only_degrades_to_done_fallback(
        self, monkeypatch: pytest.MonkeyPatch, evidence_path: Path
    ) -> None:
        # 完了と同じ秒の +1 は順序を証明できないので PASS にせず、既存 timeout で縮退する
        snap = _snap([_plus_one("2026-05-24T08:25:20Z")], comments=[_summary_comment()])
        assert self._polling(monkeypatch, evidence_path, [snap]).state == "done_fallback"
        assert not evidence_path.exists()

    def test_initial_pass_returns_done_pass_and_writes_evidence(
        self, monkeypatch: pytest.MonkeyPatch, evidence_path: Path
    ) -> None:
        # PR #181 シナリオ: 起動時すでに summary Completed + +1 あり
        result = self._polling(monkeypatch, evidence_path, [_pass_snap()])
        assert result.state == "done_pass"
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        assert evidence["result"] == "PASS"
        assert evidence["provider"] == "github"
        assert evidence["repository"] == {"owner": OWNER, "name": REPO}
        assert evidence["pull_request"] == {"number": PR, "url": PR_URL}
        assert evidence["reviewed_head"] == {"sha": HEAD, "committed_at": HEAD_AT}
        assert evidence["approval"]["bot"]["id"] == BOT_ID
        assert evidence["approval"]["reaction"]["id"] == 1
        assert evidence["approval"]["reaction"]["api_path"] == (
            f"repos/{OWNER}/{REPO}/issues/{PR}/reactions"
        )
        assert evidence["approval"]["review_summary"]["comment_id"] == 501
        assert evidence["approval"]["review_summary"]["completed_at"] == COMPLETED_AT
        assert evidence["checks"] == {
            "head_sha_at_start": HEAD,
            "head_sha_before_decision": HEAD,
            "head_sha_after_decision": HEAD,
            "short_sha_matching_pr_commits": 1,
            "current_head_bot_reviews": 0,
        }
        assert evidence["fetched_at"] == evidence["decided_at"] == "2026-05-24T09:00:00Z"
        # producer の model と同じ不変条件で再読込できる
        assert load_evidence(evidence_path).reviewed_head.sha == HEAD

    def test_pass_result_carries_human_readable_evidence(
        self, monkeypatch: pytest.MonkeyPatch, evidence_path: Path
    ) -> None:
        result = self._polling(monkeypatch, evidence_path, [_pass_snap()])
        assert f"pr={OWNER}/{REPO}#{PR}" in result.reason
        assert f"head_sha={HEAD}" in result.reason
        assert "reaction_id=1" in result.reason
        assert f"summary_comment={PR_URL}#issuecomment-501" in result.reason
        assert f"evidence_path={evidence_path.resolve()}" in result.reason

    def test_in_progress_then_pass(
        self, monkeypatch: pytest.MonkeyPatch, evidence_path: Path
    ) -> None:
        eyes = _snap(_load("reactions_eyes.json"))
        result = self._polling(monkeypatch, evidence_path, [eyes, eyes, _pass_snap()])
        assert result.state == "done_pass"
        assert evidence_path.exists()

    def test_in_progress_then_retry(
        self, monkeypatch: pytest.MonkeyPatch, evidence_path: Path
    ) -> None:
        eyes = _snap(_load("reactions_eyes.json"))
        review = _snap(reviews=_load("reviews_commented_current_head.json"))
        result = self._polling(monkeypatch, evidence_path, [eyes, eyes, review])
        assert result.state == "done_retry"
        assert not evidence_path.exists()

    def test_immediate_retry_pr_176_scenario(
        self, monkeypatch: pytest.MonkeyPatch, evidence_path: Path
    ) -> None:
        # 起動時点で reactions 0 件 + 現在 head 向け COMMENTED review が既に存在
        review = _snap(reviews=_load("reviews_commented_current_head.json"))
        assert self._polling(monkeypatch, evidence_path, [review]).state == "done_retry"

    def test_api_failures_three_times_aborts(
        self, monkeypatch: pytest.MonkeyPatch, evidence_path: Path
    ) -> None:
        def always_fail(path: str) -> Any:
            raise subprocess.CalledProcessError(1, ["gh", "api", path])

        monkeypatch.setattr(mod, "_gh_api", always_fail)
        monkeypatch.setattr(mod, "_gh_api_object", always_fail)
        assert _run(evidence_path).state == "done_abort"

    def test_in_progress_timeout_cap_aborts(
        self, monkeypatch: pytest.MonkeyPatch, evidence_path: Path
    ) -> None:
        # eyes が出続けて結論が出ない → IN_PROGRESS_TIMEOUT_SEC で abort
        _install(monkeypatch, _FakeGh([_snap(_load("reactions_eyes.json"))]))
        assert _run(evidence_path, in_progress_timeout_sec=30).state == "done_abort"


@pytest.mark.medium
class TestHeadBinding:
    """polling 中・確定の前後で head が動いたら PASS にせず、証跡も作らない（完了条件 5）。"""

    @pytest.mark.parametrize(
        ("pull_heads", "label"),
        [
            ([OTHER_HEAD], "first poll"),
            ([HEAD, OTHER_HEAD], "before decision"),
            ([HEAD, HEAD, OTHER_HEAD], "after decision"),
        ],
    )
    def test_head_change_aborts_without_evidence(
        self,
        monkeypatch: pytest.MonkeyPatch,
        evidence_path: Path,
        pull_heads: list[str],
        label: str,
    ) -> None:
        _install(monkeypatch, _FakeGh([_pass_snap()], pull_heads=pull_heads))
        result = _run(evidence_path)
        assert result.state == "done_abort", label
        assert "head changed" in result.reason
        assert not evidence_path.exists()

    def test_head_change_midway_through_polling_aborts(
        self, monkeypatch: pytest.MonkeyPatch, evidence_path: Path
    ) -> None:
        eyes = _snap(_load("reactions_eyes.json"))
        _install(
            monkeypatch,
            _FakeGh([eyes, eyes, _pass_snap()], pull_heads=[HEAD, HEAD, OTHER_HEAD]),
        )
        result = _run(evidence_path)
        assert result.state == "done_abort"
        assert "head changed" in result.reason
        assert not evidence_path.exists()

    def test_closed_pr_aborts(self, monkeypatch: pytest.MonkeyPatch, evidence_path: Path) -> None:
        _install(monkeypatch, _FakeGh([_pass_snap()], pull_state="closed"))
        result = _run(evidence_path)
        assert result.state == "done_abort"
        assert "not open" in result.reason
        assert not evidence_path.exists()

    @pytest.mark.parametrize(
        "commits",
        [
            [{"sha": HEAD}, {"sha": HEAD[:7] + "0" * 33}],  # 同じ短縮 SHA が 2 件
            [{"sha": OTHER_HEAD}],  # 一致 0 件
            [{"sha": HEAD[:7] + "9" * 33}],  # 一致した commit が head ではない
        ],
        ids=["two_matches", "no_match", "match_is_not_head"],
    )
    def test_short_sha_mapping_ambiguity_aborts(
        self,
        monkeypatch: pytest.MonkeyPatch,
        evidence_path: Path,
        commits: list[dict[str, Any]],
    ) -> None:
        _install(monkeypatch, _FakeGh([_pass_snap()], commits=commits))
        result = _run(evidence_path)
        assert result.state == "done_abort"
        assert "SHA mapping ambiguous" in result.reason
        assert not evidence_path.exists()


@pytest.mark.medium
class TestEvidenceUnavailable:
    def test_missing_evidence_path_aborts_instead_of_pass(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(monkeypatch, _FakeGh([_pass_snap()]))
        result = _run(None)
        assert result.state == "done_abort"
        assert "evidence unavailable" in result.reason

    def test_write_failure_aborts_instead_of_pass(
        self, monkeypatch: pytest.MonkeyPatch, evidence_path: Path
    ) -> None:
        _install(monkeypatch, _FakeGh([_pass_snap()]))

        def boom(path: Path, evidence: Any) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(mod, "save_evidence", boom)
        result = _run(evidence_path)
        assert result.state == "done_abort"
        assert "evidence unavailable" in result.reason

    def test_validation_failure_aborts_instead_of_pass(
        self, monkeypatch: pytest.MonkeyPatch, evidence_path: Path
    ) -> None:
        # 時計が reaction より前 → I9 (reaction.created_at <= fetched_at) 違反。書き込まない
        _install(monkeypatch, _FakeGh([_pass_snap()]))
        result = _run(evidence_path, utcnow=lambda: datetime(2026, 1, 1, tzinfo=UTC))
        assert result.state == "done_abort"
        assert "evidence unavailable" in result.reason
        assert not evidence_path.exists()


@pytest.mark.medium
class TestProviderFailures:
    @pytest.mark.parametrize("kind", ["pulls", "reactions", "reviews", "comments"])
    def test_two_failures_recover(
        self, monkeypatch: pytest.MonkeyPatch, evidence_path: Path, kind: str
    ) -> None:
        _install(monkeypatch, _FakeGh([_pass_snap()], fail_first={kind: 2}))
        assert _run(evidence_path).state == "done_pass"

    @pytest.mark.parametrize("kind", ["pulls", "reactions", "reviews", "comments"])
    def test_three_consecutive_failures_abort(
        self, monkeypatch: pytest.MonkeyPatch, evidence_path: Path, kind: str
    ) -> None:
        _install(monkeypatch, _FakeGh([_pass_snap()], fail_first={kind: 3}))
        result = _run(evidence_path)
        assert result.state == "done_abort"
        assert "gh api failed 3 times" in result.reason
        assert not evidence_path.exists()

    def test_commits_fetch_failure_in_finalization_recovers(
        self, monkeypatch: pytest.MonkeyPatch, evidence_path: Path
    ) -> None:
        _install(monkeypatch, _FakeGh([_pass_snap()], fail_first={"commits": 2}))
        assert _run(evidence_path).state == "done_pass"

    def test_persistent_commits_failure_aborts_instead_of_looping(
        self, monkeypatch: pytest.MonkeyPatch, evidence_path: Path
    ) -> None:
        # 確定確認の取得失敗も連続失敗カウンタに合算される（poll 本体の成功で帳消しにしない）
        _install(monkeypatch, _FakeGh([_pass_snap()], fail_first={"commits": 99}))
        result = _run(evidence_path)
        assert result.state == "done_abort"
        assert "gh api failed 3 times" in result.reason
        assert not evidence_path.exists()


# --- _gh_api / _gh_api_object / pagination (Medium) -------------------------


def _fake_subprocess(
    routes: dict[str, Any], calls: list[list[str]]
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """`gh api` の fake。`--slurp` 付きなら routes[kind] を「ページ配列」として返す。"""

    def _fake(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        assert args[:2] == ["gh", "api"]
        path = args[-1]
        kind = "pulls" if path.endswith(f"/pulls/{PR}") else path.rsplit("/", 1)[1]
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(routes[kind]), stderr="")

    return _fake


@pytest.mark.medium
class TestGhApi:
    def test_api_uses_paginate_and_slurp_and_flattens_pages(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(
            mod.subprocess,
            "run",
            _fake_subprocess({"reactions": [[{"id": 1}], [{"id": 2}]]}, calls),
        )
        assert mod._gh_api(f"repos/{OWNER}/{REPO}/issues/{PR}/reactions") == [{"id": 1}, {"id": 2}]
        assert "--paginate" in calls[0] and "--slurp" in calls[0]

    def test_api_rejects_non_list_page(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(mod.subprocess, "run", _fake_subprocess({"reactions": [{"id": 1}]}, []))
        with pytest.raises(ValueError):
            mod._gh_api(f"repos/{OWNER}/{REPO}/issues/{PR}/reactions")

    def test_api_rejects_non_list_outer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(mod.subprocess, "run", _fake_subprocess({"reactions": {"id": 1}}, []))
        with pytest.raises(ValueError):
            mod._gh_api(f"repos/{OWNER}/{REPO}/issues/{PR}/reactions")

    def test_api_object_returns_dict_without_paginate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(mod.subprocess, "run", _fake_subprocess({"pulls": _pr_object()}, calls))
        assert mod._gh_api_object(f"repos/{OWNER}/{REPO}/pulls/{PR}")["state"] == "open"
        assert "--paginate" not in calls[0]

    def test_api_object_rejects_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(mod.subprocess, "run", _fake_subprocess({"pulls": [1]}, []))
        with pytest.raises(ValueError):
            mod._gh_api_object(f"repos/{OWNER}/{REPO}/pulls/{PR}")


@pytest.mark.medium
class TestPaginationEndToEnd:
    """2 ページ目にしかないシグナルも `run_polling` が拾う（完了条件 7）。"""

    def _install_pages(
        self, monkeypatch: pytest.MonkeyPatch, routes: dict[str, Any]
    ) -> list[list[str]]:
        calls: list[list[str]] = []
        full = {"pulls": _pr_object(), "commits": [[{"sha": HEAD}]], **routes}
        monkeypatch.setattr(mod.subprocess, "run", _fake_subprocess(full, calls))
        return calls

    def test_current_head_review_on_second_page_is_retry(
        self, monkeypatch: pytest.MonkeyPatch, evidence_path: Path
    ) -> None:
        self._install_pages(
            monkeypatch,
            {
                "reactions": [[]],
                "reviews": [
                    _load("reviews_commented_old_head.json"),
                    _load("reviews_commented_current_head.json"),
                ],
                "comments": [[]],
            },
        )
        assert _run(evidence_path).state == "done_retry"

    def test_summary_on_second_page_passes_and_untrusted_reaction_is_ignored(
        self, monkeypatch: pytest.MonkeyPatch, evidence_path: Path
    ) -> None:
        untrusted = {
            "id": 77,
            "user": {"id": 1, "login": "apokamo"},
            "content": "+1",
            "created_at": "2026-05-24T09:30:00Z",
        }
        old_summary = _summary_comment(
            comment_id=1, updated_at="2026-05-24T08:00:00Z", short="1111111"
        )
        calls = self._install_pages(
            monkeypatch,
            {
                "reactions": [[untrusted], _load("reactions_plus_one.json")],
                "reviews": [[]],
                "comments": [[old_summary], [_summary_comment()]],
            },
        )
        result = _run(evidence_path)
        assert result.state == "done_pass"
        assert load_evidence(evidence_path).approval.reaction.id == 1
        gh_list_calls = [c for c in calls if not c[-1].endswith(f"/pulls/{PR}")]
        assert gh_list_calls and all("--paginate" in c and "--slurp" in c for c in gh_list_calls)


# --- heartbeat in run_polling (Medium) --------------------------------------


@pytest.mark.medium
class TestHeartbeat:
    def _run_hb(
        self,
        monkeypatch: pytest.MonkeyPatch,
        evidence_path: Path,
        snapshots: list[dict[str, list[dict[str, Any]]]],
    ) -> tuple[PollResult, list[str]]:
        _install(monkeypatch, _FakeGh(snapshots))
        emitted: list[str] = []
        result = _run(evidence_path, pr_number=PR, emit_progress=emitted.append)
        return result, emitted

    def test_heartbeat_emitted_each_nonterminal_poll(
        self, monkeypatch: pytest.MonkeyPatch, evidence_path: Path
    ) -> None:
        eyes = _snap(_load("reactions_eyes.json"))
        # poll1 eyes(in_progress), poll2 eyes(in_progress), poll3 summary+plus_one(done_pass)
        result, emitted = self._run_hb(monkeypatch, evidence_path, [eyes, eyes, _pass_snap()])
        assert result.state == "done_pass"
        # 非 terminal poll は 2 回 → heartbeat も 2 行（terminal poll では出さない）
        assert len(emitted) == 2
        for line in emitted:
            assert f"PR #{PR}" in line
            assert "---" not in line

    def test_heartbeat_elapsed_monotonic_increasing(
        self, monkeypatch: pytest.MonkeyPatch, evidence_path: Path
    ) -> None:
        eyes = _snap(_load("reactions_eyes.json"))
        _result, emitted = self._run_hb(
            monkeypatch, evidence_path, [eyes, eyes, eyes, _pass_snap()]
        )
        elapsed_vals = [int(re.search(r"elapsed=(\d+)s", ln).group(1)) for ln in emitted]  # type: ignore[union-attr]
        assert elapsed_vals == sorted(elapsed_vals)
        assert len(set(elapsed_vals)) == len(elapsed_vals)  # 単調増加（重複なし）

    def test_state_unchanged_when_emitter_always_raises(
        self, monkeypatch: pytest.MonkeyPatch, evidence_path: Path
    ) -> None:
        eyes = _snap(_load("reactions_eyes.json"))
        seq = [eyes, eyes, _pass_snap()]

        def boom(_line: str) -> None:
            raise BrokenPipeError("pipe closed")

        # 例外を投げる emitter でも結論は heartbeat 無しと同一でなければならない
        baseline, _ = self._run_hb(monkeypatch, evidence_path, seq)
        _install(monkeypatch, _FakeGh(seq))
        with_raise = _run(evidence_path, emit_progress=boom)
        assert with_raise.state == baseline.state == "done_pass"

    def test_heartbeat_emitted_on_api_failure_retry(
        self, monkeypatch: pytest.MonkeyPatch, evidence_path: Path
    ) -> None:
        # 1 回目の poll で gh api が失敗 → retry 待機中に heartbeat が出ること。
        # 2 回目で PASS 条件が揃い done_pass で終了（terminal は heartbeat 無し）。
        _install(monkeypatch, _FakeGh([_pass_snap()], fail_first={"pulls": 1}))
        emitted: list[str] = []
        result = _run(evidence_path, api_failure_limit=3, emit_progress=emitted.append)
        assert result.state == "done_pass"
        # API failure retry の sleep 直前に heartbeat が 1 行出る（transient error 可視化）
        retry_lines = [ln for ln in emitted if "api_retry:1/3" in ln]
        assert len(retry_lines) == 1
        for line in emitted:
            assert f"PR #{PR}" in line
            assert "---" not in line

    def test_heartbeat_emitted_on_eyes_lost_grace(
        self, monkeypatch: pytest.MonkeyPatch, evidence_path: Path
    ) -> None:
        # eyes 観測 → eyes 消失（grace 待機）→ 終了 の遷移を classify を差し替えて強制し、
        # grace 待機の sleep 直前に heartbeat が出ることを固定する。
        seq = [
            PollResult("in_progress", "eyes"),  # poll1: in_progress
            PollResult("init", "eyes lost"),  # poll2: eyes 消失 → grace 待機
            PollResult("done_retry", "bot review"),  # poll3: 終了
        ]
        idx = {"i": 0}

        def fake_classify(*_args: Any, **_kwargs: Any) -> PollResult:
            r = seq[min(idx["i"], len(seq) - 1)]
            idx["i"] += 1
            return r

        _install(monkeypatch, _FakeGh([_snap()]))
        monkeypatch.setattr(mod, "classify", fake_classify)
        emitted: list[str] = []
        result = _run(evidence_path, emit_progress=emitted.append)
        assert result.state == "done_retry"
        grace_lines = [ln for ln in emitted if "eyes_lost_grace" in ln]
        assert len(grace_lines) == 1
        for line in emitted:
            assert f"PR #{PR}" in line
            assert "---" not in line


# --- emit_verdict (Small) ---------------------------------------------------


@pytest.mark.small
class TestEmitVerdict:
    def test_pass_verdict(self) -> None:
        out = mod.emit_verdict(PollResult("done_pass", "bot +1"), "next")
        assert "status: PASS" in out
        assert "---VERDICT---" in out and "---END_VERDICT---" in out
        assert "trusted bot approval bound to PR head SHA" in out

    def test_retry_verdict(self) -> None:
        out = mod.emit_verdict(PollResult("done_retry", "bot review"), "fix it")
        assert "status: RETRY" in out

    def test_fallback_verdict(self) -> None:
        out = mod.emit_verdict(PollResult("done_fallback", "timeout"), "fallback")
        assert "status: BACK_FALLBACK" in out
        assert "suggestion:" in out

    def test_abort_verdict(self) -> None:
        out = mod.emit_verdict(PollResult("done_abort", "api failed"), "check")
        assert "status: ABORT" in out
        assert "suggestion:" in out

    @pytest.mark.parametrize(
        ("state", "status"),
        [
            ("done_pass", "PASS"),
            ("done_retry", "RETRY"),
            ("done_fallback", "BACK_FALLBACK"),
            ("done_abort", "ABORT"),
        ],
    )
    def test_four_field_contract_is_parseable(self, state: Any, status: str) -> None:
        out = mod.emit_verdict(PollResult(state, "line1\nline2"), "next")
        verdict = parse_verdict_block(out, {"PASS", "RETRY", "BACK_FALLBACK", "ABORT"})
        assert verdict is not None
        assert verdict.status == status
        assert verdict.reason and verdict.evidence and verdict.suggestion
        assert "line1" in verdict.evidence and "line2" in verdict.evidence


# --- format_heartbeat (Small) -----------------------------------------------


@pytest.mark.small
class TestFormatHeartbeat:
    def test_contains_required_elements(self) -> None:
        line = format_heartbeat(
            elapsed_sec=12.7,
            pr_number=176,
            head_sha=HEAD,
            state="in_progress",
            remaining_sec=1788.0,
        )
        assert "PR #176" in line
        assert f"head={HEAD[:7]}" in line
        assert "elapsed=12s" in line  # int 切り捨て
        assert "remaining=1788s" in line
        assert "state=in_progress" in line

    def test_does_not_contain_verdict_markers(self) -> None:
        line = format_heartbeat(
            elapsed_sec=0,
            pr_number=1,
            head_sha=HEAD,
            state="init",
            remaining_sec=60,
        )
        assert "---VERDICT---" not in line
        assert "---END_VERDICT---" not in line
        assert "---" not in line

    def test_negative_remaining_clamped_to_zero(self) -> None:
        line = format_heartbeat(
            elapsed_sec=120,
            pr_number=1,
            head_sha=HEAD,
            state="init",
            remaining_sec=-5,
        )
        assert "remaining=0s" in line


# --- _default_emit flush / failure isolation (Medium) -----------------------


class _RecordingStream:
    def __init__(self, *, fail: bool = False) -> None:
        self.writes: list[str] = []
        self.flushes = 0
        self.fail = fail

    def write(self, data: str) -> int:
        if self.fail:
            raise BrokenPipeError("write failed")
        self.writes.append(data)
        return len(data)

    def flush(self) -> None:
        if self.fail:
            raise OSError("flush failed")
        self.flushes += 1


@pytest.mark.medium
class TestDefaultEmit:
    def test_writes_and_flushes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stream = _RecordingStream()
        monkeypatch.setattr("sys.stdout", stream)
        _default_emit("heartbeat line")
        assert stream.writes == ["heartbeat line\n"]
        assert stream.flushes == 1

    def test_broken_pipe_is_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stream = _RecordingStream(fail=True)
        monkeypatch.setattr("sys.stdout", stream)
        # 例外を送出せず return すること
        _default_emit("heartbeat line")


# --- verdict parse non-destruction (Medium) ---------------------------------


@pytest.mark.medium
class TestVerdictParseNonDestruction:
    def test_heartbeat_lines_do_not_break_verdict_parse(self) -> None:
        valid = {"PASS", "RETRY", "BACK_FALLBACK", "ABORT"}
        verdict_block = mod.emit_verdict(
            PollResult("done_fallback", "no reaction"), "run fallback review"
        )
        heartbeats = "\n".join(
            format_heartbeat(
                elapsed_sec=i * 10,
                pr_number=176,
                head_sha=HEAD,
                state="init",
                remaining_sec=60 - i * 10,
            )
            for i in range(3)
        )
        combined = heartbeats + "\n" + verdict_block
        with_hb = parse_verdict_block(combined, valid)
        without_hb = parse_verdict_block(verdict_block, valid)
        assert with_hb is not None and without_hb is not None
        assert with_hb.status == without_hb.status == "BACK_FALLBACK"


# --- main (Small / Medium) --------------------------------------------------


def _main_argv(head_sha: str = HEAD, *extra: str) -> list[str]:
    return [
        "--pr",
        str(PR),
        "--owner",
        OWNER,
        "--repo",
        REPO,
        "--head-sha",
        head_sha,
        "--head-committed-at",
        HEAD_AT,
        *extra,
    ]


@pytest.mark.small
class TestMainHeadShaValidation:
    @pytest.mark.parametrize(
        "sha",
        ["abc123", HEAD[:39], HEAD + "0", HEAD.upper(), "g" * 40, ""],
        ids=["short", "39_chars", "41_chars", "uppercase", "non_hex", "empty"],
    )
    def test_invalid_head_sha_aborts_without_polling(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        sha: str,
    ) -> None:
        def must_not_poll(*_a: Any, **_k: Any) -> PollResult:
            raise AssertionError("run_polling must not be called with an invalid head sha")

        monkeypatch.setattr(mod, "run_polling", must_not_poll)
        assert mod.main(_main_argv(sha)) == 0
        out = capsys.readouterr().out
        assert "status: ABORT" in out
        assert "head sha" in out.lower()


class _OrderSpy:
    """stdout への verdict 書き込みの瞬間に、証跡ファイルが既にあるかを記録する。"""

    def __init__(self, evidence_path: Path) -> None:
        self.evidence_path = evidence_path
        self.text = ""
        self.evidence_existed_at_write: bool | None = None

    def write(self, data: str) -> int:
        if "---VERDICT---" in data and self.evidence_existed_at_write is None:
            self.evidence_existed_at_write = self.evidence_path.exists()
        self.text += data
        return len(data)

    def flush(self) -> None:
        return None


@pytest.mark.medium
class TestMainPass:
    def test_pass_writes_evidence_before_emitting_verdict(
        self, monkeypatch: pytest.MonkeyPatch, evidence_path: Path
    ) -> None:
        _install(monkeypatch, _FakeGh([_pass_snap()]))
        spy = _OrderSpy(evidence_path)
        monkeypatch.setattr(mod.sys, "stdout", spy)
        rc = mod.main(_main_argv(HEAD, "--evidence-path", str(evidence_path)))
        assert rc == 0
        assert spy.evidence_existed_at_write is True
        verdict = parse_verdict_block(spy.text, {"PASS", "RETRY", "BACK_FALLBACK", "ABORT"})
        assert verdict is not None and verdict.status == "PASS"
        assert f"pr={OWNER}/{REPO}#{PR}" in verdict.evidence
        assert f"head_sha={HEAD}" in verdict.evidence
        assert f"evidence_path={evidence_path.resolve()}" in verdict.evidence
        assert "created_at >= head_committed_at" not in verdict.reason

    def test_pass_condition_without_evidence_path_aborts(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _install(monkeypatch, _FakeGh([_pass_snap()]))
        assert mod.main(_main_argv()) == 0
        out = capsys.readouterr().out
        assert "status: ABORT" in out
        assert "evidence unavailable" in out
        assert "status: PASS" not in out

    def test_non_pass_verdicts_leave_no_evidence(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        evidence_path: Path,
    ) -> None:
        review = _snap(reviews=_load("reviews_commented_current_head.json"))
        _install(monkeypatch, _FakeGh([review]))
        assert mod.main(_main_argv(HEAD, "--evidence-path", str(evidence_path))) == 0
        assert "status: RETRY" in capsys.readouterr().out
        assert not evidence_path.exists()


# --- Issue #473: codex-review-state:v1 / receipt 付き review 本文 -----------------

KAMO2_HEAD = "dc8eb9cb83b0a48ca51218d919288ae14297120f"
KAMO2_PASS_HEAD = "af4b02c1891368cc369c3111464fbeb0b5996e3f"


@pytest.mark.small
class TestKamo2Reproduction:
    def test_pr1837_receipt_review_with_state_is_retry(self) -> None:
        # Issue #473 再現: 修正前は ambiguous bot review で in_progress のまま
        result = classify(
            [],
            _load("kamo2_pr1837_reviews.json"),
            KAMO2_HEAD,
            "2026-10-16T17:40:00Z",
            comments=_load("kamo2_pr1837_comments.json"),
            prev_state="in_progress",
        )
        assert result.state == "done_retry"
        assert result.signal == "state"

    def test_pr1837_receipt_review_without_state_is_legacy_retry(self) -> None:
        reviews = _load("kamo2_pr1837_reviews.json")
        assert reviews[0]["body"].startswith("<!-- codex-review-receipt:v1 -->")
        result = classify([], reviews, KAMO2_HEAD, "2026-10-16T17:40:00Z", prev_state="in_progress")
        assert result.state == "done_retry"
        assert result.signal == "legacy"

    def test_pr1834_state_without_findings_and_later_plus_one_is_pass(self) -> None:
        result = classify(
            _load("kamo2_pr1834_reactions.json"),
            [],
            KAMO2_PASS_HEAD,
            "2026-10-16T10:54:31Z",
            comments=_load("kamo2_pr1834_comments.json"),
        )
        assert result.state == "done_pass"
        assert result.signal == "state"
        assert result.approval is not None and result.approval.review_state is not None
        assert result.approval.reaction_id == 573522841


STATE_UPDATED_AT = "2026-05-24T08:25:21Z"


def _entry(
    sha: str | None = HEAD, *, findings: bool | None = False, status: str | None = "completed"
) -> dict[str, Any]:
    return {
        "commit_sha": sha,
        "has_any_findings": findings,
        "has_unresolved_findings": findings,
        "status": status,
    }


_NULL_ENTRY: dict[str, Any] = _entry(None, findings=None, status=None)


def _state_marker(
    code: dict[str, Any] | None = None,
    security: dict[str, Any] | None = None,
    *,
    version: str = "v1",
    payload: str | None = None,
) -> str:
    if payload is None:
        payload = json.dumps(
            {
                "code_review": code if code is not None else _entry(),
                "security_review": security if security is not None else _NULL_ENTRY,
            },
            separators=(",", ":"),
        )
    return f"<!-- codex-review-state:{version} {payload} -->"


def _state_comment(
    marker: str | None = None,
    *,
    comment_id: int = 601,
    updated_at: str = STATE_UPDATED_AT,
    user_id: int = BOT_ID,
) -> dict[str, Any]:
    marker = marker if marker is not None else _state_marker()
    comment = _summary_comment(comment_id=comment_id, updated_at=updated_at, user_id=user_id)
    head, rest = comment["body"].split("\n", 1)
    comment["body"] = f"{head}\n{marker}\n{rest}"
    return comment


def _bot_review(body: str, *, commit_id: str = HEAD, state: str = "COMMENTED") -> dict[str, Any]:
    return {
        "id": 77,
        "user": {"id": BOT_ID, "login": "chatgpt-codex-connector[bot]"},
        "state": state,
        "commit_id": commit_id,
        "body": body,
    }


_RECEIPT_BODY = "<!-- codex-review-receipt:v1 -->\n\n\n### 💡 Codex Review\n\nsuggestions"


@pytest.mark.small
class TestLegacyReviewBody:
    @pytest.mark.parametrize(
        "body",
        [
            _RECEIPT_BODY,
            "\n### 💡 Codex Review\n\nold format",
            "<!-- a -->\n<!-- b -->  \n### 💡 Codex Review",
        ],
        ids=["receipt", "no_comment", "multiple_comments"],
    )
    def test_codex_review_heading_after_leading_comments_is_retry(self, body: str) -> None:
        result = classify([], [_bot_review(body)], HEAD, HEAD_AT)
        assert result.state == "done_retry"
        assert result.signal == "legacy"

    @pytest.mark.parametrize(
        "body",
        ["<!-- unclosed\n### 💡 Codex Review", "intro\n\n### 💡 Codex Review"],
        ids=["unclosed_comment", "marker_not_leading"],
    )
    def test_heading_not_at_start_is_ambiguous(self, body: str) -> None:
        result = classify(
            _load("reactions_plus_one.json"),
            [_bot_review(body)],
            HEAD,
            HEAD_AT,
            comments=[_summary_comment()],
        )
        assert result.state == "init"
        assert "ambiguous" in result.reason

    def test_receipt_review_on_other_head_is_not_retry(self) -> None:
        result = classify([], [_bot_review(_RECEIPT_BODY, commit_id=OTHER_HEAD)], HEAD, HEAD_AT)
        assert result.state == "init"

    def test_legacy_pass_is_marked_legacy(self) -> None:
        result = classify(
            _load("reactions_plus_one.json"), [], HEAD, HEAD_AT, comments=[_summary_comment()]
        )
        assert result.state == "done_pass"
        assert result.signal == "legacy"


@pytest.mark.small
class TestParseReviewState:
    def test_valid_state_is_parsed(self) -> None:
        state = mod.parse_review_state([_state_comment()], BOT_ID)
        assert isinstance(state, mod.ReviewState)
        assert state.comment_id == 601
        assert state.updated_at == STATE_UPDATED_AT
        assert state.code_review.commit_sha == HEAD
        assert state.security_review.status is None

    def test_absent_state_returns_none(self) -> None:
        assert mod.parse_review_state([_summary_comment()], BOT_ID) is None

    @pytest.mark.parametrize(
        "marker",
        [
            _state_marker(version="v2"),
            _state_marker() + "\n" + _state_marker(),
            _state_marker(payload='{"code_review":'),
            _state_marker(payload=json.dumps({"security_review": _NULL_ENTRY})),
            _state_marker(_entry(None)),
            _state_marker(_entry(findings=None)),
            _state_marker(_entry("ABC")),
            _state_marker(_entry(findings=1)),  # type: ignore[arg-type]
            "<!-- codex-review-state:v1-->",
        ],
        ids=[
            "unknown_version",
            "two_markers",
            "broken_json",
            "code_review_missing",
            "completed_without_sha",
            "completed_without_findings",
            "sha_not_hex",
            "findings_not_bool",
            "malformed_marker",
        ],
    )
    def test_unusable_state_is_invalid(self, marker: str) -> None:
        state = mod.parse_review_state([_state_comment(marker)], BOT_ID)
        assert isinstance(state, mod.InvalidReviewState)

    def test_unknown_extra_keys_are_ignored(self) -> None:
        payload = json.dumps(
            {"code_review": {**_entry(), "x": 1}, "security_review": _NULL_ENTRY, "y": {}}
        )
        state = mod.parse_review_state([_state_comment(_state_marker(payload=payload))], BOT_ID)
        assert isinstance(state, mod.ReviewState)

    def test_untrusted_comment_state_is_ignored(self) -> None:
        assert mod.parse_review_state([_state_comment(user_id=1)], BOT_ID) is None

    def test_tied_updated_at_with_state_is_invalid(self) -> None:
        comments = [_state_comment(comment_id=1), _summary_comment(comment_id=2)]
        comments[1]["updated_at"] = STATE_UPDATED_AT
        assert isinstance(mod.parse_review_state(comments, BOT_ID), mod.InvalidReviewState)

    def test_tied_updated_at_without_state_is_absent(self) -> None:
        comments = [_summary_comment(comment_id=1), _summary_comment(comment_id=2)]
        assert mod.parse_review_state(comments, BOT_ID) is None


def _fresh_plus_one() -> list[dict[str, Any]]:
    return [_plus_one("2026-05-24T08:25:22Z")]


@pytest.mark.small
class TestClassifyState:
    def _classify(
        self,
        code: dict[str, Any] | None = None,
        security: dict[str, Any] | None = None,
        *,
        reactions: list[dict[str, Any]] | None = None,
        reviews: list[dict[str, Any]] | None = None,
    ) -> PollResult:
        return classify(
            _fresh_plus_one() if reactions is None else reactions,
            reviews or [],
            HEAD,
            HEAD_AT,
            comments=[_state_comment(_state_marker(code, security))],
        )

    def test_findings_on_head_is_retry(self) -> None:
        result = self._classify(_entry(findings=True), reactions=[])
        assert (result.state, result.signal) == ("done_retry", "state")

    def test_no_findings_with_later_plus_one_is_pass(self) -> None:
        result = self._classify()
        assert (result.state, result.signal) == ("done_pass", "state")
        assert result.approval is not None and result.approval.summary is None

    @pytest.mark.parametrize(
        "reactions",
        [
            [],
            [_plus_one(STATE_UPDATED_AT)],
            [_plus_one("2026-05-24T08:25:20Z")],
            [_plus_one("2026-05-24T08:05:06Z")],
        ],
        ids=["no_plus_one", "same_second", "before_update", "before_head_commit"],
    )
    def test_plus_one_not_after_state_is_not_pass(self, reactions: list[dict[str, Any]]) -> None:
        assert self._classify(reactions=reactions).state == "init"

    def test_plus_one_before_head_commit_is_not_pass(self) -> None:
        result = classify(
            [_plus_one("2026-05-24T08:25:22Z")],
            [],
            HEAD,
            "2026-05-24T08:30:00Z",
            comments=[_state_comment()],
        )
        assert result.state == "init"

    @pytest.mark.parametrize("findings", [True, False])
    def test_state_for_other_head_is_not_terminal(self, findings: bool) -> None:
        assert self._classify(_entry(OTHER_HEAD, findings=findings)).state == "init"

    @pytest.mark.parametrize("status", ["in_progress", "queued"])
    def test_unobserved_status_is_not_terminal(self, status: str) -> None:
        for findings in (True, False, None):
            assert self._classify(_entry(findings=findings, status=status)).state == "init"

    def test_security_review_pending_on_head_blocks_pass(self) -> None:
        security = _entry(HEAD, findings=None, status="in_progress")
        assert self._classify(security=security).state == "init"

    def test_security_review_pending_without_sha_blocks_pass(self) -> None:
        security = _entry(None, findings=None, status="in_progress")
        assert self._classify(security=security).state == "init"

    def test_security_review_findings_on_head_is_retry(self) -> None:
        result = self._classify(security=_entry(findings=True), reactions=[])
        assert (result.state, result.signal) == ("done_retry", "state")

    def test_security_review_completed_without_findings_allows_pass(self) -> None:
        assert self._classify(security=_entry()).state == "done_pass"

    def test_security_review_on_other_head_is_ignored(self) -> None:
        for findings in (True, False):
            security = _entry(OTHER_HEAD, findings=findings)
            assert self._classify(security=security).state == "done_pass"

    def test_bot_review_on_head_blocks_state_pass(self) -> None:
        reviews = [_bot_review(_RECEIPT_BODY)]
        assert self._classify(reviews=reviews).state == "init"

    def test_state_ignores_legacy_review_body(self) -> None:
        # state が head 未完了なら、旧経路の review 本文では RETRY にしない
        reviews = [_bot_review(_RECEIPT_BODY)]
        result = self._classify(_entry(OTHER_HEAD), reviews=reviews, reactions=[])
        assert result.state == "init"

    def test_state_ignores_legacy_summary_table(self) -> None:
        # 表は head・Completed を示すが、state が head を完了扱いしないので PASS しない
        result = self._classify(_entry(OTHER_HEAD), reactions=_load("reactions_plus_one.json"))
        assert result.state == "init"

    def test_invalid_state_is_immediate_fallback(self) -> None:
        result = classify(
            _fresh_plus_one(),
            [],
            HEAD,
            HEAD_AT,
            comments=[_state_comment(_state_marker(version="v2"))],
        )
        assert (result.state, result.signal) == ("done_fallback", "state")
        assert "codex-review-state" in result.reason

    def test_eyes_keeps_in_progress_on_state_path(self) -> None:
        result = self._classify(_entry(OTHER_HEAD), reactions=_load("reactions_eyes.json"))
        assert result.state == "in_progress"


@pytest.mark.small
class TestVerdictReasons:
    def test_fallback_reason_covers_state_parse_failure(self) -> None:
        out = mod.emit_verdict(PollResult("done_fallback", "state broken", signal="state"), "x")
        assert "status: BACK_FALLBACK" in out
        assert "codex-review-state" in out and "NO_REACTION_TIMEOUT_SEC" in out

    def test_signal_does_not_change_verdict_format(self) -> None:
        plain = mod.emit_verdict(PollResult("done_retry", "r"), "s")
        tagged = mod.emit_verdict(PollResult("done_retry", "r", signal="state"), "s")
        assert plain == tagged


def _state_pass_snap() -> dict[str, list[dict[str, Any]]]:
    return _snap(_fresh_plus_one(), comments=[_state_comment()])


@pytest.mark.medium
class TestRunPollingStatePath:
    def _polling(
        self,
        monkeypatch: pytest.MonkeyPatch,
        evidence_path: Path,
        snapshots: list[dict[str, list[dict[str, Any]]]],
        warnings: list[str],
        heartbeats: list[str] | None = None,
    ) -> tuple[PollResult, _FakeGh]:
        fake = _install(monkeypatch, _FakeGh(snapshots))
        result = _run(
            evidence_path,
            emit_warning=warnings.append,
            emit_progress=(heartbeats if heartbeats is not None else []).append,
        )
        return result, fake

    def test_state_pass_writes_v2_evidence_without_commits_lookup(
        self, monkeypatch: pytest.MonkeyPatch, evidence_path: Path
    ) -> None:
        warnings: list[str] = []
        result, fake = self._polling(monkeypatch, evidence_path, [_state_pass_snap()], warnings)
        assert result.state == "done_pass"
        assert "commits" not in fake.calls
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        assert evidence["schema_version"] == 2
        assert evidence["approval"]["review_state"] == {
            "comment_id": 601,
            "url": f"{PR_URL}#issuecomment-601",
            "comment_updated_at": STATE_UPDATED_AT,
            "state_version": "v1",
            "code_review": {"commit_sha": HEAD, "status": "completed", "has_any_findings": False},
            "security_review": {"commit_sha": None, "status": None, "has_any_findings": None},
        }
        assert "short_sha_matching_pr_commits" not in evidence["checks"]
        assert load_evidence(evidence_path).schema_version == 2
        assert warnings == []

    def test_legacy_pass_keeps_v1_evidence_and_warns_once(
        self, monkeypatch: pytest.MonkeyPatch, evidence_path: Path
    ) -> None:
        warnings: list[str] = []
        heartbeats: list[str] = []
        eyes = _snap(_load("reactions_eyes.json"))
        result, fake = self._polling(
            monkeypatch, evidence_path, [eyes, _pass_snap()], warnings, heartbeats
        )
        assert result.state == "done_pass"
        assert "commits" in fake.calls
        assert load_evidence(evidence_path).schema_version == 1
        assert warnings == [mod.LEGACY_DEPRECATION_WARNING]
        assert heartbeats and all("deprecation" not in line for line in heartbeats)
        assert "deprecation" not in result.reason

    def test_legacy_retry_warns_and_state_retry_does_not(
        self, monkeypatch: pytest.MonkeyPatch, evidence_path: Path
    ) -> None:
        legacy: list[str] = []
        snap = _snap(reviews=[_bot_review(_RECEIPT_BODY)])
        assert self._polling(monkeypatch, evidence_path, [snap], legacy)[0].state == "done_retry"
        assert legacy == [mod.LEGACY_DEPRECATION_WARNING]

        state: list[str] = []
        snap = _snap(comments=[_state_comment(_state_marker(_entry(findings=True)))])
        assert self._polling(monkeypatch, evidence_path, [snap], state)[0].state == "done_retry"
        assert state == []

    def test_invalid_state_falls_back_on_first_poll(
        self, monkeypatch: pytest.MonkeyPatch, evidence_path: Path
    ) -> None:
        warnings: list[str] = []
        heartbeats: list[str] = []
        snap = _snap(comments=[_state_comment(_state_marker(version="v2"))])
        result, fake = self._polling(monkeypatch, evidence_path, [snap], warnings, heartbeats)
        assert result.state == "done_fallback"
        assert fake.snapshot_idx == 0
        assert heartbeats == []
        assert not evidence_path.exists()

    def test_pr1837_state_retry_after_eyes(
        self, monkeypatch: pytest.MonkeyPatch, evidence_path: Path
    ) -> None:
        warnings: list[str] = []
        eyes = _snap(_load("reactions_eyes.json"))
        review = _snap(
            reviews=_load("kamo2_pr1837_reviews.json"),
            comments=_load("kamo2_pr1837_comments.json"),
        )
        fake = _install(monkeypatch, _FakeGh([eyes, review], pull_heads=[KAMO2_HEAD]))
        result = _run(evidence_path, head_sha=KAMO2_HEAD, emit_warning=warnings.append)
        assert fake.snapshot_idx == 1
        assert (result.state, result.signal) == ("done_retry", "state")
        assert warnings == []


@pytest.mark.medium
class TestDefaultWarn:
    def test_writes_to_stderr_not_stdout(self, capsys: pytest.CaptureFixture[str]) -> None:
        mod._default_warn("warn line")
        captured = capsys.readouterr()
        assert captured.err == "warn line\n"
        assert captured.out == ""

    def test_os_error_is_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(mod.sys, "stderr", _RecordingStream(fail=True))
        mod._default_warn("x")
