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
    *,
    remote_url: str | None = "git@github.com:owner/repo.git",
    pr_list_stdout: str | None = '{"number": 42, "headRefName": "feat/x"}',
    head_ref_oid_stdout: str = "4c212ed7f6b886c110d116be714e134e99f79cf0\n",
    committed_date_stdout: str = "2026-05-28T01:58:57Z\n",
) -> Any:
    """subprocess.run の fake。

    `_gh_json` / `_gh_raw` の継ぎ目（生 CLI stdout → Python 値変換）を実際に
    通すため、CLI 呼び出しごとに **生 stdout 文字列** を返す。``pr view --jq``
    のスカラー抽出は gh/kaji が返すクォートなし生文字列をそのまま流す。
    """

    def _fake(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["git", "remote"]:
            if remote_url is None:
                raise subprocess.CalledProcessError(1, args, output="", stderr="no such remote")
            return subprocess.CompletedProcess(args, 0, stdout=remote_url + "\n", stderr="")
        if args[:3] == ["kaji", "pr", "list"]:
            if pr_list_stdout is None:
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(args, 0, stdout=pr_list_stdout, stderr="")
        if args[:3] == ["kaji", "pr", "view"]:
            jq = args[args.index("--jq") + 1]
            if jq == ".headRefOid":
                return subprocess.CompletedProcess(args, 0, stdout=head_ref_oid_stdout, stderr="")
            if jq == ".commits[-1].committedDate":
                return subprocess.CompletedProcess(args, 0, stdout=committed_date_stdout, stderr="")
        raise AssertionError(f"unexpected subprocess.run call: {args}")

    return _fake


@pytest.mark.small
class TestPrResolution:
    def test_pr_list_empty_returns_abort(
        self, base_env: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with patch(
            "kaji_harness.scripts.review_poll_entry.subprocess.run",
            side_effect=_fake_subprocess_run_factory(pr_list_stdout=None),
        ):
            rc = review_poll_entry.main()
        out = capsys.readouterr().out
        assert rc == 0
        assert "status: ABORT" in out
        assert "PR not resolved" in out

    def test_head_sha_empty_returns_abort(
        self, base_env: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # pr list に headRefOid を含めず、pr view --jq .headRefOid を空文字で返す。
        with patch(
            "kaji_harness.scripts.review_poll_entry.subprocess.run",
            side_effect=_fake_subprocess_run_factory(
                pr_list_stdout='{"number": 99, "headRefName": "feat/x"}',
                head_ref_oid_stdout="\n",
            ),
        ):
            rc = review_poll_entry.main()
        out = capsys.readouterr().out
        assert rc == 0
        assert "head_sha unavailable" in out

    def test_committed_at_empty_returns_abort(
        self, base_env: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with patch(
            "kaji_harness.scripts.review_poll_entry.subprocess.run",
            side_effect=_fake_subprocess_run_factory(
                pr_list_stdout='{"number": 99, "headRefOid": "abc123"}',
                committed_date_stdout="\n",
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
class TestScalarRawResolution:
    """`--jq` スカラー抽出は生文字列（クォートなし）で解決する契約をピン留めする。

    Issue #209 回帰防止: `_gh_json` が生 SHA / 生日付を json.loads して
    `JSONDecodeError` でクラッシュしていたバグの再現テスト。
    """

    def test_raw_sha_and_date_delegate_without_json_parse(self, base_env: None) -> None:
        # pr list に headRefOid を含めない → line 150 分岐に入り pr view --jq
        # .headRefOid（生 SHA）が必ず呼ばれる。committedDate も別経路で常に呼ばれる。
        with (
            patch(
                "kaji_harness.scripts.review_poll_entry.subprocess.run",
                side_effect=_fake_subprocess_run_factory(
                    pr_list_stdout='{"number": 42, "headRefName": "feat/x"}',
                    head_ref_oid_stdout="4c212ed7f6b886c110d116be714e134e99f79cf0\n",
                    committed_date_stdout="2026-05-28T01:58:57Z\n",
                ),
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
            "4c212ed7f6b886c110d116be714e134e99f79cf0",
            "--head-committed-at",
            "2026-05-28T01:58:57Z",
        ]

    def test_head_sha_from_pr_list_skips_pr_view(self, base_env: None) -> None:
        # pr list の JSON object から headRefOid を解決 → pr view --jq .headRefOid
        # はスキップされる従来分岐。.[0] object 抽出が JSON parse されデグレ無し。
        def _fake(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            if args[:2] == ["git", "remote"]:
                return subprocess.CompletedProcess(
                    args, 0, stdout="git@github.com:owner/repo.git\n", stderr=""
                )
            if args[:3] == ["kaji", "pr", "list"]:
                return subprocess.CompletedProcess(
                    args,
                    0,
                    stdout='{"number": 7, "headRefName": "feat/x", "headRefOid": "cafef00d"}',
                    stderr="",
                )
            if args[:3] == ["kaji", "pr", "view"]:
                jq = args[args.index("--jq") + 1]
                if jq == ".headRefOid":
                    raise AssertionError("pr view --jq .headRefOid must be skipped")
                return subprocess.CompletedProcess(
                    args, 0, stdout="2026-05-28T00:00:00Z\n", stderr=""
                )
            raise AssertionError(f"unexpected subprocess.run call: {args}")

        with (
            patch(
                "kaji_harness.scripts.review_poll_entry.subprocess.run",
                side_effect=_fake,
            ),
            patch(
                "kaji_harness.scripts.review_poll_entry.codex_review_poll.main",
                return_value=0,
            ) as mock_main,
        ):
            rc = review_poll_entry.main()
        assert rc == 0
        call_argv = mock_main.call_args[0][0]
        assert call_argv[:8] == [
            "--pr",
            "7",
            "--owner",
            "owner",
            "--repo",
            "repo",
            "--head-sha",
            "cafef00d",
        ]


@pytest.mark.small
class TestArgvDelegation:
    def test_full_flow_delegates_argv_to_codex_review_poll(self, base_env: None) -> None:
        with (
            patch(
                "kaji_harness.scripts.review_poll_entry.subprocess.run",
                side_effect=_fake_subprocess_run_factory(
                    pr_list_stdout='{"number": 42, "headRefOid": "deadbeef"}',
                    committed_date_stdout="2026-05-28T00:00:00Z\n",
                ),
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
        def _fake(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            if args[:2] == ["git", "remote"]:
                return subprocess.CompletedProcess(
                    args, 0, stdout="git@github.com:owner/repo.git\n", stderr=""
                )
            raise subprocess.CalledProcessError(1, args, stderr="gh not found")

        with patch(
            "kaji_harness.scripts.review_poll_entry.subprocess.run",
            side_effect=_fake,
        ):
            with pytest.raises(subprocess.CalledProcessError):
                review_poll_entry.main()
