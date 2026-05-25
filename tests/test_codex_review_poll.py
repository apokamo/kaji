"""Tests for kaji_harness.scripts.codex_review_poll."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from kaji_harness.scripts import codex_review_poll as mod
from kaji_harness.scripts.codex_review_poll import (
    BOT_ID,
    PollResult,
    classify,
    run_polling,
)

FIXTURES = Path(__file__).parent / "fixtures" / "codex_review_poll"
HEAD = "abc123def4567890abc123def4567890abc123de"
# head commit committedDate (ISO8601 UTC). PR #181 head 実観測値 = 2026-05-24T08:05:07Z
HEAD_AT = "2026-05-24T08:05:07Z"


def _load(name: str) -> list[dict[str, Any]]:
    return json.loads((FIXTURES / name).read_text())


# --- classify (Small) -------------------------------------------------------


@pytest.mark.small
class TestClassify:
    def test_fresh_plus_one_only_returns_done_pass(self) -> None:
        # +1.created_at (08:25:28Z) >= head_committed_at (08:05:07Z)
        reactions = _load("reactions_plus_one.json")
        assert classify(reactions, [], HEAD, HEAD_AT).state == "done_pass"

    def test_stale_plus_one_only_keeps_state(self) -> None:
        # +1.created_at (07:00:00Z) < head_committed_at (08:05:07Z) -> freshness guard
        reactions = _load("reactions_plus_one_stale.json")
        assert classify(reactions, [], HEAD, HEAD_AT).state == "init"

    def test_fresh_and_stale_plus_one_returns_done_pass(self) -> None:
        # fresh が 1 件でもあれば PASS
        reactions = _load("reactions_plus_one_fresh_and_stale.json")
        assert classify(reactions, [], HEAD, HEAD_AT).state == "done_pass"

    def test_eyes_only_returns_in_progress(self) -> None:
        reactions = _load("reactions_eyes.json")
        assert classify(reactions, [], HEAD, HEAD_AT).state == "in_progress"

    def test_eyes_gone_with_fresh_plus_one_returns_done_pass(self) -> None:
        reactions = _load("reactions_plus_one.json")
        assert classify(reactions, [], HEAD, HEAD_AT, prev_state="in_progress").state == "done_pass"

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

    def test_retry_takes_priority_over_plus_one(self) -> None:
        reactions = _load("reactions_plus_one.json")
        reviews = _load("reviews_commented_current_head.json")
        assert classify(reactions, reviews, HEAD, HEAD_AT).state == "done_retry"

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


def _gh_responses(
    monkeypatch: pytest.MonkeyPatch,
    sequence: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]],
) -> None:
    """Stub _gh_api to return reactions/reviews from a per-poll sequence.

    Each entry produces 2 calls (reactions then reviews). When the sequence is
    exhausted the last entry is repeated.
    """
    state = {"idx": 0, "leg": 0}  # leg 0 = reactions, 1 = reviews

    def fake_gh_api(path: str) -> list[dict[str, Any]]:
        i = min(state["idx"], len(sequence) - 1)
        reactions, reviews = sequence[i]
        if state["leg"] == 0:
            state["leg"] = 1
            return reactions
        state["leg"] = 0
        state["idx"] += 1
        return reviews

    monkeypatch.setattr(mod, "_gh_api", fake_gh_api)


@pytest.mark.medium
class TestRunPolling:
    def _polling(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sequence: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]],
        **kwargs: Any,
    ) -> PollResult:
        _gh_responses(monkeypatch, sequence)
        clock = _FakeClock()
        return run_polling(
            pr_number=182,
            owner="apokamo",
            repo="kaji",
            head_sha=HEAD,
            head_committed_at=HEAD_AT,
            poll_interval_sec=10,
            no_reaction_timeout_sec=60,
            in_progress_timeout_sec=1800,
            eyes_grace_sec=10,
            now=clock.now,
            sleep=clock.sleep,
            **kwargs,
        )

    def test_timeout_no_signals_returns_done_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = self._polling(monkeypatch, [([], [])])
        assert result.state == "done_fallback"

    def test_stale_plus_one_only_returns_done_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # freshness guard: stale +1 のみだと PASS せず timeout で fallback
        stale = _load("reactions_plus_one_stale.json")
        result = self._polling(monkeypatch, [(stale, [])])
        assert result.state == "done_fallback"

    def test_initial_fresh_plus_one_returns_done_pass(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # PR #181 シナリオ: 起動時すでに fresh +1 あり
        fresh = _load("reactions_plus_one.json")
        result = self._polling(monkeypatch, [(fresh, [])])
        assert result.state == "done_pass"

    def test_in_progress_then_pass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        eyes = _load("reactions_eyes.json")
        plus_one = _load("reactions_plus_one.json")
        result = self._polling(
            monkeypatch,
            [(eyes, []), (eyes, []), (plus_one, [])],
        )
        assert result.state == "done_pass"

    def test_in_progress_then_retry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        eyes = _load("reactions_eyes.json")
        review = _load("reviews_commented_current_head.json")
        result = self._polling(
            monkeypatch,
            [(eyes, []), (eyes, []), ([], review)],
        )
        assert result.state == "done_retry"

    def test_immediate_retry_pr_176_scenario(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 起動時点で reactions 0 件 + 現在 head 向け COMMENTED review が既に存在
        review = _load("reviews_commented_current_head.json")
        result = self._polling(monkeypatch, [([], review)])
        assert result.state == "done_retry"

    def test_api_failures_three_times_aborts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def always_fail(path: str) -> list[dict[str, Any]]:
            raise subprocess.CalledProcessError(1, ["gh", "api", path])

        monkeypatch.setattr(mod, "_gh_api", always_fail)
        clock = _FakeClock()
        result = run_polling(
            pr_number=182,
            owner="apokamo",
            repo="kaji",
            head_sha=HEAD,
            head_committed_at=HEAD_AT,
            poll_interval_sec=10,
            now=clock.now,
            sleep=clock.sleep,
        )
        assert result.state == "done_abort"

    def test_in_progress_timeout_cap_aborts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # eyes が出続けて結論が出ない → IN_PROGRESS_TIMEOUT_SEC で abort
        eyes = _load("reactions_eyes.json")
        _gh_responses(monkeypatch, [(eyes, [])])
        clock = _FakeClock()
        result = run_polling(
            pr_number=182,
            owner="apokamo",
            repo="kaji",
            head_sha=HEAD,
            head_committed_at=HEAD_AT,
            poll_interval_sec=10,
            no_reaction_timeout_sec=60,
            in_progress_timeout_sec=30,
            eyes_grace_sec=10,
            now=clock.now,
            sleep=clock.sleep,
        )
        assert result.state == "done_abort"


# --- emit_verdict (Small) ---------------------------------------------------


@pytest.mark.small
class TestEmitVerdict:
    def test_pass_verdict(self) -> None:
        out = mod.emit_verdict(PollResult("done_pass", "bot +1"), "next")
        assert "status: PASS" in out
        assert "---VERDICT---" in out and "---END_VERDICT---" in out

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
