"""Tests for review_poll_entry shim (Issue #204 MF-3)."""

from __future__ import annotations

import subprocess
from typing import Any
from unittest.mock import patch

import pytest

from kaji_harness.scripts import review_poll_entry


@pytest.fixture
def base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAJI_PROVIDER_TYPE", "github")
    monkeypatch.setenv("KAJI_ISSUE_ID", "204")
    monkeypatch.setenv("KAJI_WORKTREE_DIR", "/tmp/worktree")
    monkeypatch.setenv("KAJI_GIT_REMOTE", "origin")
    monkeypatch.delenv("KAJI_PR_ID", raising=False)


@pytest.mark.small
class TestProviderGuard:
    def test_provider_not_github_returns_abort(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("KAJI_PROVIDER_TYPE", "local")
        rc = review_poll_entry.main()
        out = capsys.readouterr().out
        assert rc == 0
        assert "status: ABORT" in out
        assert "provider" in out.lower()


@pytest.mark.small
class TestRemoteUrlParse:
    @pytest.mark.parametrize(
        "url, expected",
        [
            ("git@github.com:owner/repo.git", ("owner", "repo")),
            ("git@github.com:owner/repo", ("owner", "repo")),
            ("https://github.com/owner/repo.git", ("owner", "repo")),
            ("https://github.com/owner/repo", ("owner", "repo")),
            ("https://github.com/owner/repo/", ("owner", "repo")),
        ],
    )
    def test_valid_urls(self, url: str, expected: tuple[str, str]) -> None:
        assert review_poll_entry.parse_remote_url(url) == expected

    @pytest.mark.parametrize("url", ["", "not a url", "ftp://x.y/a/b", "git@host"])
    def test_invalid_urls(self, url: str) -> None:
        with pytest.raises(ValueError):
            review_poll_entry.parse_remote_url(url)


def _fake_subprocess_run_factory(
    *, remote_url: str | None = "git@github.com:owner/repo.git"
) -> Any:
    """git remote get-url 用 subprocess.run の fake。"""

    def _fake(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["git", "remote"]:
            if remote_url is None:
                raise subprocess.CalledProcessError(1, args, output="", stderr="no such remote")
            return subprocess.CompletedProcess(args, 0, stdout=remote_url + "\n", stderr="")
        raise AssertionError(f"unexpected subprocess.run call: {args}")

    return _fake


@pytest.mark.small
class TestPrResolution:
    def test_pr_list_empty_returns_abort(
        self, base_env: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with (
            patch(
                "kaji_harness.scripts.review_poll_entry.subprocess.run",
                side_effect=_fake_subprocess_run_factory(),
            ),
            patch("kaji_harness.scripts.review_poll_entry._gh_json", return_value=None),
        ):
            rc = review_poll_entry.main()
        out = capsys.readouterr().out
        assert rc == 0
        assert "status: ABORT" in out
        assert "PR not resolved" in out

    def test_head_sha_empty_returns_abort(
        self, base_env: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        calls: list[Any] = []

        def fake_gh_json(args: list[str], cwd: str | None = None) -> Any:
            calls.append(args)
            # 1st call: pr list -> dict with number but empty headRefOid
            if "list" in args:
                return {"number": 99, "headRefName": "feat/x", "headRefOid": ""}
            # 2nd call: pr view --jq .headRefOid -> empty
            if "headRefOid" in args:
                return ""
            return None

        with (
            patch(
                "kaji_harness.scripts.review_poll_entry.subprocess.run",
                side_effect=_fake_subprocess_run_factory(),
            ),
            patch(
                "kaji_harness.scripts.review_poll_entry._gh_json",
                side_effect=fake_gh_json,
            ),
        ):
            rc = review_poll_entry.main()
        out = capsys.readouterr().out
        assert rc == 0
        assert "head_sha unavailable" in out

    def test_committed_at_empty_returns_abort(
        self, base_env: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def fake_gh_json(args: list[str], cwd: str | None = None) -> Any:
            if "list" in args:
                return {"number": 99, "headRefOid": "abc123"}
            # committedDate jq returns empty
            return ""

        with (
            patch(
                "kaji_harness.scripts.review_poll_entry.subprocess.run",
                side_effect=_fake_subprocess_run_factory(),
            ),
            patch(
                "kaji_harness.scripts.review_poll_entry._gh_json",
                side_effect=fake_gh_json,
            ),
        ):
            rc = review_poll_entry.main()
        out = capsys.readouterr().out
        assert rc == 0
        assert "head committed_at unavailable" in out

    def test_git_remote_failure_returns_abort(
        self, base_env: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with patch(
            "kaji_harness.scripts.review_poll_entry.subprocess.run",
            side_effect=_fake_subprocess_run_factory(remote_url=None),
        ):
            rc = review_poll_entry.main()
        out = capsys.readouterr().out
        assert rc == 0
        assert "remote url parse failure" in out


@pytest.mark.small
class TestArgvDelegation:
    def test_full_flow_delegates_argv_to_codex_review_poll(self, base_env: None) -> None:
        def fake_gh_json(args: list[str], cwd: str | None = None) -> Any:
            if "list" in args:
                return {"number": 42, "headRefOid": "deadbeef"}
            if "headRefOid" in args:
                return "deadbeef"
            return "2026-05-28T00:00:00Z"

        with (
            patch(
                "kaji_harness.scripts.review_poll_entry.subprocess.run",
                side_effect=_fake_subprocess_run_factory(),
            ),
            patch(
                "kaji_harness.scripts.review_poll_entry._gh_json",
                side_effect=fake_gh_json,
            ),
            patch(
                "kaji_harness.scripts.review_poll_entry.codex_review_poll.main",
                return_value=0,
            ) as mock_main,
        ):
            rc = review_poll_entry.main()
        assert rc == 0
        call_argv = mock_main.call_args[0][0]
        assert call_argv == [
            "--pr",
            "42",
            "--owner",
            "owner",
            "--repo",
            "repo",
            "--head-sha",
            "deadbeef",
            "--head-committed-at",
            "2026-05-28T00:00:00Z",
        ]

    def test_gh_called_process_error_propagates(self, base_env: None) -> None:
        def fake_gh_json(args: list[str], cwd: str | None = None) -> Any:
            raise subprocess.CalledProcessError(1, args, stderr="gh not found")

        with (
            patch(
                "kaji_harness.scripts.review_poll_entry.subprocess.run",
                side_effect=_fake_subprocess_run_factory(),
            ),
            patch(
                "kaji_harness.scripts.review_poll_entry._gh_json",
                side_effect=fake_gh_json,
            ),
        ):
            with pytest.raises(subprocess.CalledProcessError):
                review_poll_entry.main()
