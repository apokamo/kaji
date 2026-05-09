"""Issue local-pc5090-6: kaji issue / kaji pr の GitLab dispatcher Medium テスト。

``glab`` CLI を実呼びせず ``subprocess.run`` を mock してロジック（sub 名 / flag
rewrite、merge guard、未対応 sub の reject、shape 変換、marker 注入、note → approve
シーケンス、branch → IID 解決）を検証する。
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kaji_harness.cli_main import _handle_issue, _handle_pr


def _write_gitlab_repo(tmp_path: Path) -> Path:
    """``provider.type='gitlab'`` の最小 .kaji/config.toml を持つ repo を作る。"""
    repo = tmp_path / "repo"
    (repo / ".kaji").mkdir(parents=True)
    (repo / ".kaji" / "config.toml").write_text(
        '[paths]\nartifacts_dir = ".kaji-artifacts"\nskill_dir = ".claude/skills"\n\n'
        "[execution]\ndefault_timeout = 1800\n\n"
        '[provider]\ntype = "gitlab"\n\n'
        '[provider.gitlab]\nrepo = "g/p"\n'
    )
    return repo


def _ok(stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr=stderr)


# ============================================================
# kaji issue (GitLab)
# ============================================================


@pytest.mark.medium
class TestGitLabIssueDispatch:
    def test_create_forwards_with_repo_and_hostname(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _write_gitlab_repo(tmp_path)
        monkeypatch.chdir(repo)
        with (
            patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/glab"),
            patch("kaji_harness.cli_main.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            rc = _handle_issue(["create", "--title", "T", "--body", "B"])
        assert rc == 0
        cmd = mock_run.call_args[0][0]
        # glab --hostname gitlab.com issue create --title T --description B --repo g/p
        assert cmd[0] == "glab"
        assert "--hostname" in cmd and "gitlab.com" in cmd
        assert "create" in cmd
        # --body → --description 変換
        assert "--description" in cmd
        assert "--body" not in cmd
        # --repo 末尾注入
        assert "--repo" in cmd and "g/p" in cmd

    def test_edit_maps_to_update_and_description(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _write_gitlab_repo(tmp_path)
        monkeypatch.chdir(repo)
        with (
            patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/glab"),
            patch("kaji_harness.cli_main.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            rc = _handle_issue(["edit", "42", "--body", "new body"])
        assert rc == 0
        cmd = mock_run.call_args[0][0]
        # edit → update
        assert "update" in cmd
        assert "edit" not in cmd
        # --body → --description
        assert "--description" in cmd
        assert "new body" in cmd
        # id は 42 のまま
        assert "42" in cmd

    def test_comment_maps_to_note_and_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _write_gitlab_repo(tmp_path)
        monkeypatch.chdir(repo)
        with (
            patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/glab"),
            patch("kaji_harness.cli_main.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            rc = _handle_issue(["comment", "42", "--body", "hi"])
        assert rc == 0
        cmd = mock_run.call_args[0][0]
        assert "note" in cmd
        assert "comment" not in cmd
        assert "--message" in cmd
        assert "hi" in cmd

    def test_gl_prefix_id_is_unwrapped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _write_gitlab_repo(tmp_path)
        monkeypatch.chdir(repo)
        with (
            patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/glab"),
            patch("kaji_harness.cli_main.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            rc = _handle_issue(["close", "gl:42"])
        assert rc == 0
        cmd = mock_run.call_args[0][0]
        # gl:42 → 42 に剥がされて glab に渡る
        assert "42" in cmd
        assert "gl:42" not in cmd

    def test_unsupported_sub_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = _write_gitlab_repo(tmp_path)
        monkeypatch.chdir(repo)
        with patch("kaji_harness.cli_main.subprocess.run") as mock_run:
            rc = _handle_issue(["transfer", "42"])
        assert rc == 2
        mock_run.assert_not_called()
        assert "not supported" in capsys.readouterr().err

    def test_commit_flag_is_silently_stripped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _write_gitlab_repo(tmp_path)
        monkeypatch.chdir(repo)
        with (
            patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/glab"),
            patch("kaji_harness.cli_main.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            rc = _handle_issue(["comment", "42", "--body", "hi", "--commit"])
        assert rc == 0
        cmd = mock_run.call_args[0][0]
        assert "--commit" not in cmd

    def test_view_with_json_normalizes_via_provider(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = _write_gitlab_repo(tmp_path)
        monkeypatch.chdir(repo)
        # provider.view_issue → glab api を 2 回叩く（issue payload + notes）
        issue_payload = {
            "iid": 42,
            "title": "T",
            "description": "B",
            "state": "opened",
            "labels": ["bug"],
        }
        outputs = iter([_ok(stdout=json.dumps(issue_payload)), _ok(stdout="[]")])
        with (
            patch("kaji_harness.providers.gitlab.shutil.which", return_value="/usr/bin/glab"),
            patch(
                "kaji_harness.providers.gitlab.subprocess.run",
                side_effect=lambda *a, **kw: next(outputs),
            ),
        ):
            rc = _handle_issue(["view", "42", "--json", "title,body"])
        assert rc == 0
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload == {"title": "T", "body": "B"}


# ============================================================
# kaji pr (GitLab)
# ============================================================


@pytest.mark.medium
class TestGitLabPrUnsupportedSub:
    def test_approvers_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = _write_gitlab_repo(tmp_path)
        monkeypatch.chdir(repo)
        with patch("kaji_harness.cli_main.subprocess.run") as mock_run:
            rc = _handle_pr(["approvers", "42"])
        assert rc == 2
        mock_run.assert_not_called()
        assert "not supported" in capsys.readouterr().err

    def test_subscribe_rejected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = _write_gitlab_repo(tmp_path)
        monkeypatch.chdir(repo)
        with patch("kaji_harness.cli_main.subprocess.run") as mock_run:
            rc = _handle_pr(["subscribe", "42"])
        assert rc == 2
        mock_run.assert_not_called()


@pytest.mark.medium
class TestGitLabPrMerge:
    def test_squash_rejected_before_subprocess(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = _write_gitlab_repo(tmp_path)
        monkeypatch.chdir(repo)
        with patch("kaji_harness.cli_main.subprocess.run") as mock_run:
            rc = _handle_pr(["merge", "42", "--squash"])
        assert rc == 2
        mock_run.assert_not_called()
        assert "no-ff" in capsys.readouterr().err.lower()

    def test_rebase_rejected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = _write_gitlab_repo(tmp_path)
        monkeypatch.chdir(repo)
        with patch("kaji_harness.cli_main.subprocess.run") as mock_run:
            rc = _handle_pr(["merge", "42", "--rebase"])
        assert rc == 2
        mock_run.assert_not_called()

    def test_iid_passes_through(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = _write_gitlab_repo(tmp_path)
        monkeypatch.chdir(repo)
        with (
            patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/glab"),
            patch("kaji_harness.cli_main.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            rc = _handle_pr(["merge", "42"])
        assert rc == 0
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "glab"
        assert "merge" in cmd
        assert "42" in cmd

    def test_branch_resolves_to_iid(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = _write_gitlab_repo(tmp_path)
        monkeypatch.chdir(repo)
        # provider.resolve_mr_iid_from_branch → glab api list で 1 件
        # 続けて _forward_to_glab → glab mr merge <iid>
        api_payload = json.dumps([{"iid": 99, "title": "x"}])
        calls: list[list[str]] = []

        def _side_effect(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(list(cmd))
            if "api" in cmd:
                return _ok(stdout=api_payload)
            return _ok()

        with (
            patch("kaji_harness.providers.gitlab.shutil.which", return_value="/usr/bin/glab"),
            patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/glab"),
            patch("subprocess.run", side_effect=_side_effect),
        ):
            rc = _handle_pr(["merge", "feat/local-pc5090-6"])
        assert rc == 0
        # 最後の呼出が glab mr merge 99 になっていること
        last = calls[-1]
        assert "99" in last
        assert "merge" in last
        assert "feat/local-pc5090-6" not in last


@pytest.mark.medium
class TestGitLabPrCreate:
    def test_base_to_target_branch(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = _write_gitlab_repo(tmp_path)
        monkeypatch.chdir(repo)
        with (
            patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/glab"),
            patch("kaji_harness.cli_main.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            rc = _handle_pr(["create", "--title", "T", "--body", "B", "--base", "main"])
        assert rc == 0
        cmd = mock_run.call_args[0][0]
        assert "--target-branch" in cmd
        assert "main" in cmd
        assert "--base" not in cmd
        assert "--description" in cmd
        assert "--body" not in cmd
        # 非対話実行を保証する --yes 注入（skill が prompt 待ちで stuck しないため）
        assert "--yes" in cmd


@pytest.mark.medium
class TestGitLabIssueCreateYes:
    def test_issue_create_injects_yes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _write_gitlab_repo(tmp_path)
        monkeypatch.chdir(repo)
        with (
            patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/glab"),
            patch("kaji_harness.cli_main.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            rc = _handle_issue(["create", "--title", "T", "--body", "B"])
        assert rc == 0
        cmd = mock_run.call_args[0][0]
        # 非対話実行のため --yes が注入される
        assert "--yes" in cmd

    def test_issue_create_does_not_double_inject_yes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """user が --yes を指定済なら kaji 側で重複注入しない。"""
        repo = _write_gitlab_repo(tmp_path)
        monkeypatch.chdir(repo)
        with (
            patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/glab"),
            patch("kaji_harness.cli_main.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            rc = _handle_issue(["create", "--title", "T", "--body", "B", "--yes"])
        assert rc == 0
        cmd = mock_run.call_args[0][0]
        assert cmd.count("--yes") == 1


@pytest.mark.medium
class TestGitLabPrList:
    def test_head_to_source_branch_and_search(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = _write_gitlab_repo(tmp_path)
        monkeypatch.chdir(repo)
        # provider.list_mrs_payload → glab api projects/.../merge_requests?...
        list_payload = json.dumps([{"iid": 1, "state": "opened", "title": "x"}])
        with (
            patch("kaji_harness.providers.gitlab.shutil.which", return_value="/usr/bin/glab"),
            patch(
                "kaji_harness.providers.gitlab.subprocess.run",
                return_value=_ok(stdout=list_payload),
            ) as mock_run,
        ):
            rc = _handle_pr(
                [
                    "list",
                    "--head",
                    "feat/x",
                    "--search",
                    "foo",
                    "--json",
                    "number,state",
                ]
            )
        assert rc == 0
        cmd = mock_run.call_args[0][0]
        # glab api projects/g%2Fp/merge_requests?...&source_branch=feat%2Fx&search=foo
        assert "api" in cmd
        endpoint = next(c for c in cmd if "merge_requests" in c)
        assert "source_branch=feat%2Fx" in endpoint
        assert "search=foo" in endpoint
        assert "state=opened" in endpoint
        out = capsys.readouterr().out
        items = json.loads(out)
        assert items == [{"number": 1, "state": "OPEN"}]


@pytest.mark.medium
class TestGitLabPrReview:
    def test_approve_sequence_note_then_approve(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _write_gitlab_repo(tmp_path)
        monkeypatch.chdir(repo)
        with (
            patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/glab"),
            patch("kaji_harness.cli_main.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            rc = _handle_pr(["review", "42", "--approve", "--body", "LGTM"])
        assert rc == 0
        # 2 calls: note + approve
        assert mock_run.call_count == 2
        first = mock_run.call_args_list[0][0][0]
        second = mock_run.call_args_list[1][0][0]
        assert "note" in first
        assert "--message" in first
        # marker 付き body
        msg_idx = first.index("--message")
        marked = first[msg_idx + 1]
        assert marked.startswith("<!-- kaji-review: state=APPROVED -->")
        assert "LGTM" in marked
        assert "approve" in second
        assert "42" in second

    def test_request_changes_skips_revoke_when_not_approved(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _write_gitlab_repo(tmp_path)
        monkeypatch.chdir(repo)
        approvals_payload = json.dumps({"user_has_approved": False, "approved_by": []})
        calls: list[list[str]] = []

        def _side_effect(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(list(cmd))
            if "api" in cmd and "approvals" in " ".join(cmd):
                return _ok(stdout=approvals_payload)
            return _ok()

        with (
            patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/glab"),
            patch("kaji_harness.providers.gitlab.shutil.which", return_value="/usr/bin/glab"),
            patch("subprocess.run", side_effect=_side_effect),
        ):
            rc = _handle_pr(["review", "42", "--request-changes", "--body", "fix"])
        assert rc == 0
        # 呼出: note (cli_main) + approvals API (provider)。revoke は呼ばれない
        cmds = [" ".join(c) for c in calls]
        assert any("note" in c and "--message" in c for c in cmds)
        assert any("approvals" in c for c in cmds)
        assert not any("revoke" in c for c in cmds)
        # marker 注入
        note_call = next(c for c in calls if "note" in c)
        msg_idx = note_call.index("--message")
        assert note_call[msg_idx + 1].startswith("<!-- kaji-review: state=CHANGES_REQUESTED -->")

    def test_request_changes_revokes_when_approved(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _write_gitlab_repo(tmp_path)
        monkeypatch.chdir(repo)
        approvals_payload = json.dumps(
            {"user_has_approved": True, "approved_by": [{"user": {"username": "me"}}]}
        )
        calls: list[list[str]] = []

        def _side_effect(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(list(cmd))
            if "api" in cmd and "approvals" in " ".join(cmd):
                return _ok(stdout=approvals_payload)
            return _ok()

        with (
            patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/glab"),
            patch("kaji_harness.providers.gitlab.shutil.which", return_value="/usr/bin/glab"),
            patch("subprocess.run", side_effect=_side_effect),
        ):
            rc = _handle_pr(["review", "42", "--request-changes", "--body", "fix"])
        assert rc == 0
        cmds = [" ".join(c) for c in calls]
        assert any("revoke" in c for c in cmds)

    def test_note_failure_skips_approve(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _write_gitlab_repo(tmp_path)
        monkeypatch.chdir(repo)
        with (
            patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/glab"),
            patch("kaji_harness.cli_main.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=1)
            rc = _handle_pr(["review", "42", "--approve", "--body", "x"])
        assert rc == 1
        # approve は呼ばれない
        assert mock_run.call_count == 1

    def test_body_file_via_stdin(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = _write_gitlab_repo(tmp_path)
        monkeypatch.chdir(repo)
        monkeypatch.setattr("sys.stdin", io.StringIO("from stdin\n"))
        with (
            patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/glab"),
            patch("kaji_harness.cli_main.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            rc = _handle_pr(["review", "42", "--approve", "--body-file", "-"])
        assert rc == 0
        first = mock_run.call_args_list[0][0][0]
        msg_idx = first.index("--message")
        marked = first[msg_idx + 1]
        assert "from stdin" in marked


@pytest.mark.medium
class TestGitLabPrViewWithJson:
    def test_json_field_projection(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = _write_gitlab_repo(tmp_path)
        monkeypatch.chdir(repo)
        view_payload = json.dumps(
            {
                "iid": 42,
                "title": "T",
                "description": "B",
                "state": "opened",
                "source_branch": "feat/x",
                "target_branch": "main",
            }
        )
        with (
            patch("kaji_harness.providers.gitlab.shutil.which", return_value="/usr/bin/glab"),
            patch(
                "kaji_harness.providers.gitlab.subprocess.run",
                return_value=_ok(stdout=view_payload),
            ) as mock_run,
        ):
            rc = _handle_pr(["view", "42", "--json", "number,state,title", "--jq", ".number"])
        assert rc == 0
        out = capsys.readouterr().out.strip()
        assert out == "42"
        # 実 glab CLI に存在しない `--output json` ではなく `glab api` 経由で取得する
        cmd = mock_run.call_args[0][0]
        assert "api" in cmd
        assert any("merge_requests/42" in str(c) for c in cmd)
        assert "--output" not in cmd

    def test_comments_passthrough(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = _write_gitlab_repo(tmp_path)
        monkeypatch.chdir(repo)
        with (
            patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/glab"),
            patch("kaji_harness.cli_main.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            rc = _handle_pr(["view", "42", "--comments"])
        assert rc == 0
        cmd = mock_run.call_args[0][0]
        assert "--comments" in cmd
        assert "view" in cmd
        assert "42" in cmd


@pytest.mark.medium
class TestGitLabPrTierA:
    def test_review_comments_returns_github_subset(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = _write_gitlab_repo(tmp_path)
        monkeypatch.chdir(repo)
        discussions = json.dumps(
            [
                {
                    "id": "d1",
                    "notes": [
                        {
                            "id": 7,
                            "system": False,
                            "body": "comment",
                            "author": {"username": "alice"},
                            "position": {"new_path": "f.py", "new_line": 3},
                        }
                    ],
                }
            ]
        )
        with (
            patch("kaji_harness.providers.gitlab.shutil.which", return_value="/usr/bin/glab"),
            patch(
                "kaji_harness.providers.gitlab.subprocess.run",
                return_value=_ok(stdout=discussions),
            ),
        ):
            rc = _handle_pr(["review-comments", "42"])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out == [
            {
                "id": "d1:7",
                "path": "f.py",
                "line": 3,
                "body": "comment",
                "user": {"login": "alice"},
            }
        ]

    def test_reviews_combines_notes_and_approvals(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = _write_gitlab_repo(tmp_path)
        monkeypatch.chdir(repo)
        notes = json.dumps(
            [
                {
                    "id": 1,
                    "system": False,
                    "body": "<!-- kaji-review: state=CHANGES_REQUESTED -->\nplease fix",
                    "author": {"username": "bob"},
                    "created_at": "2026-01-02T00:00:00Z",
                }
            ]
        )
        approvals = json.dumps({"approved_by": [{"user": {"username": "carol"}}]})
        outputs = iter([_ok(stdout=notes), _ok(stdout=approvals)])
        with (
            patch("kaji_harness.providers.gitlab.shutil.which", return_value="/usr/bin/glab"),
            patch(
                "kaji_harness.providers.gitlab.subprocess.run",
                side_effect=lambda *a, **kw: next(outputs),
            ),
        ):
            rc = _handle_pr(["reviews", "42", "--jq", "[.[].state]"])
        assert rc == 0
        out = capsys.readouterr().out.strip()
        # bob (CHANGES_REQUESTED) は ts あり、carol (APPROVED) は ts 空 → 順序保持
        assert out == '["CHANGES_REQUESTED","APPROVED"]'

    def test_reply_to_comment_validates_opaque_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = _write_gitlab_repo(tmp_path)
        monkeypatch.chdir(repo)
        with patch("kaji_harness.providers.gitlab.subprocess.run") as mock_run:
            rc = _handle_pr(["reply-to-comment", "42", "--to", "12345", "--body", "x"])
        # ":" を含まないと reject
        assert rc == 2
        mock_run.assert_not_called()
        assert "<discussion_id>:<note_id>" in capsys.readouterr().err

    def test_reply_to_comment_posts_to_discussion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _write_gitlab_repo(tmp_path)
        monkeypatch.chdir(repo)
        with (
            patch("kaji_harness.providers.gitlab.shutil.which", return_value="/usr/bin/glab"),
            patch(
                "kaji_harness.providers.gitlab.subprocess.run",
                return_value=_ok(),
            ) as mock_run,
        ):
            rc = _handle_pr(["reply-to-comment", "42", "--to", "abc:7", "--body", "reply"])
        assert rc == 0
        cmd = mock_run.call_args[0][0]
        # glab api --method POST projects/.../merge_requests/42/discussions/abc/notes -f body=reply
        assert "POST" in cmd
        assert any("discussions/abc/notes" in str(p) for p in cmd)
        assert "body=reply" in cmd
