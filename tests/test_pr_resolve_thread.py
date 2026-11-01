"""Small: ``kaji pr resolve-thread`` の gh 転送境界とスレッド特定ロジック（Issue #528）。

``subprocess.run`` / ``shutil.which`` / ``_detect_repo`` をモックし、``gh api graphql`` の
argv 組み立て・ページ送り・冪等判定・権限判定・終了コード / stderr / stdout を検証する。
worktree 解決に届かない gh 転送境界であり、``testing-convention.md`` § patch スコープで
許可される層。
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from kaji_harness.commands.exit_codes import EXIT_INVALID_INPUT, EXIT_OK, EXIT_RUNTIME_ERROR
from kaji_harness.config import (
    ExecutionConfig,
    GitHubProviderConfig,
    KajiConfig,
    PathsConfig,
    ProviderConfig,
)

pytestmark = pytest.mark.small


def _stub_github_config() -> KajiConfig:
    return KajiConfig(
        repo_root=Path("/tmp/stub"),
        paths=PathsConfig(skill_dir=".claude/skills", artifacts_dir=".kaji/artifacts"),
        execution=ExecutionConfig(default_timeout=1800),
        provider=ProviderConfig(type="github", github=GitHubProviderConfig(repo="owner/repo")),
    )


# ----------------------------------------------------------------------------
# GraphQL 応答の組み立て helper（apokamo/kamo2#2047 の実応答の形に合わせる）
# ----------------------------------------------------------------------------


def _comments(
    ids: list[str], *, has_next: bool = False, cursor: str | None = None
) -> dict[str, Any]:
    return {
        "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
        "nodes": [{"fullDatabaseId": i} for i in ids],
    }


def _thread(
    thread_id: str,
    comment_ids: list[str],
    *,
    resolved: bool = False,
    can_resolve: bool = True,
    comments_has_next: bool = False,
    comments_cursor: str | None = None,
) -> dict[str, Any]:
    return {
        "id": thread_id,
        "isResolved": resolved,
        "viewerCanResolve": can_resolve,
        "comments": _comments(comment_ids, has_next=comments_has_next, cursor=comments_cursor),
    }


def _threads_page(
    threads: list[dict[str, Any]], *, has_next: bool = False, cursor: str | None = None
) -> dict[str, Any]:
    return {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                        "nodes": threads,
                    }
                }
            }
        }
    }


def _comments_page(
    ids: list[str], *, has_next: bool = False, cursor: str | None = None
) -> dict[str, Any]:
    return {"data": {"node": {"comments": _comments(ids, has_next=has_next, cursor=cursor)}}}


def _mutation(thread_id: str, *, resolved: bool = True) -> dict[str, Any]:
    return {"data": {"resolveReviewThread": {"thread": {"id": thread_id, "isResolved": resolved}}}}


def _ok(payload: object) -> subprocess.CompletedProcess[str]:
    stdout = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def _fail(stderr: str, rc: int = 1) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout="", stderr=stderr)


def _graphql_vars(cmd: list[str]) -> dict[str, tuple[str, str]]:
    """``gh api graphql`` argv の ``-f`` / ``-F`` を ``{key: (flag, value)}`` に分解する。"""
    out: dict[str, tuple[str, str]] = {}
    for i, tok in enumerate(cmd):
        if tok in ("-f", "-F"):
            key, _, value = cmd[i + 1].partition("=")
            out[key] = (tok, value)
    return out


def _query(cmd: list[str]) -> str:
    return _graphql_vars(cmd)["query"][1]


def _is_mutation(cmd: list[str]) -> bool:
    return "resolveReviewThread" in _query(cmd)


@pytest.fixture(autouse=True)
def _isolate_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("kaji_harness.commands.pr._load_config_for_dispatch", _stub_github_config)


@pytest.fixture
def gh_env() -> Iterator[None]:
    with (
        patch("shutil.which", return_value="/usr/bin/gh"),
        patch("kaji_harness.commands.pr._detect_repo", return_value="owner/repo") as detect,
    ):
        yield
        detect.assert_called_with(override="owner/repo")


def _run(
    argv: list[str], responses: list[subprocess.CompletedProcess[str]]
) -> tuple[int, MagicMock]:
    from kaji_harness.commands.pr import _handle_pr

    with patch("subprocess.run", side_effect=responses) as mock_run:
        rc = _handle_pr(argv)
    return rc, mock_run


def _cmds(mock_run: MagicMock) -> list[list[str]]:
    return [c.args[0] for c in mock_run.call_args_list]


# ----------------------------------------------------------------------------
# 入力検証
# ----------------------------------------------------------------------------


class TestInputValidation:
    @pytest.mark.parametrize("pr_id", ["abc", "１２", "", "-1"])
    def test_non_ascii_decimal_pr_id_rejected(
        self, pr_id: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc, mock_run = _run(["resolve-thread", pr_id, "--comment", "10"], [])
        assert rc == EXIT_INVALID_INPUT
        assert mock_run.call_count == 0
        assert f"Error: PR_ID must be ASCII decimal, got: {pr_id}" in capsys.readouterr().err

    @pytest.mark.parametrize("comment_id", ["abc", "９９９", "12a"])
    def test_non_ascii_decimal_comment_id_rejected(
        self, comment_id: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc, mock_run = _run(["resolve-thread", "10", "--comment", comment_id], [])
        assert rc == EXIT_INVALID_INPUT
        assert mock_run.call_count == 0
        err = capsys.readouterr().err
        assert f"Error: --comment COMMENT_ID must be ASCII decimal, got: {comment_id}" in err

    def test_missing_comment_is_argparse_error(self) -> None:
        with pytest.raises(SystemExit) as exc:
            _run(["resolve-thread", "10"], [])
        assert exc.value.code == 2

    def test_repo_flag_is_unknown_argument(self) -> None:
        with pytest.raises(SystemExit) as exc:
            _run(["resolve-thread", "10", "--comment", "5", "--repo", "x/y"], [])
        assert exc.value.code == 2


# ----------------------------------------------------------------------------
# 正常系・冪等・スレッド特定
# ----------------------------------------------------------------------------


@pytest.mark.usefixtures("gh_env")
class TestResolve:
    def test_unresolved_thread_is_resolved(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc, mock_run = _run(
            ["resolve-thread", "2047", "--comment", "4432690867"],
            [
                _ok(_threads_page([_thread("PRRT_a", ["111"]), _thread("PRRT_b", ["4432690867"])])),
                _ok(_mutation("PRRT_b")),
            ],
        )
        assert rc == EXIT_OK
        assert mock_run.call_count == 2
        lookup, mutation = _cmds(mock_run)
        assert lookup[:3] == ["gh", "api", "graphql"]
        assert not _is_mutation(lookup)
        assert _is_mutation(mutation)
        assert _graphql_vars(mutation)["threadId"] == ("-f", "PRRT_b")
        out = capsys.readouterr().out
        assert out.endswith("\n")
        assert out.count("\n") == 1
        assert json.loads(out) == {
            "comment_id": "4432690867",
            "thread_id": "PRRT_b",
            "result": "resolved",
        }

    def test_lookup_argv_uses_repo_override_and_typed_number(self) -> None:
        rc, mock_run = _run(
            ["resolve-thread", "2047", "--comment", "4432690867"],
            [_ok(_threads_page([_thread("PRRT_b", ["4432690867"])])), _ok(_mutation("PRRT_b"))],
        )
        assert rc == EXIT_OK
        lookup = _cmds(mock_run)[0]
        variables = _graphql_vars(lookup)
        assert variables["owner"] == ("-f", "owner")
        assert variables["name"] == ("-f", "repo")
        assert variables["number"] == ("-F", "2047")
        assert "after" not in variables
        # コメント ID はクエリ文字列にも変数にも埋め込まない（照合は kaji 側で行う）
        assert "4432690867" not in _query(lookup)
        assert all("4432690867" not in tok for tok in lookup)
        for call in mock_run.call_args_list:
            assert call.kwargs.get("capture_output") is True
            assert call.kwargs.get("check") is False

    def test_already_resolved_skips_mutation(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc, mock_run = _run(
            ["resolve-thread", "2047", "--comment", "5"],
            [_ok(_threads_page([_thread("PRRT_x", ["5"], resolved=True)]))],
        )
        assert rc == EXIT_OK
        assert mock_run.call_count == 1
        assert json.loads(capsys.readouterr().out) == {
            "comment_id": "5",
            "thread_id": "PRRT_x",
            "result": "already_resolved",
        }

    def test_already_resolved_succeeds_even_without_permission(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc, mock_run = _run(
            ["resolve-thread", "2047", "--comment", "5"],
            [_ok(_threads_page([_thread("PRRT_x", ["5"], resolved=True, can_resolve=False)]))],
        )
        assert rc == EXIT_OK
        assert mock_run.call_count == 1
        assert json.loads(capsys.readouterr().out)["result"] == "already_resolved"

    def test_reply_comment_id_finds_same_thread(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc, mock_run = _run(
            ["resolve-thread", "2047", "--comment", "4434206283"],
            [
                _ok(_threads_page([_thread("PRRT_TJlI", ["4432690863", "4434206283"])])),
                _ok(_mutation("PRRT_TJlI")),
            ],
        )
        assert rc == EXIT_OK
        assert mock_run.call_count == 2
        assert _graphql_vars(_cmds(mock_run)[1])["threadId"] == ("-f", "PRRT_TJlI")
        assert json.loads(capsys.readouterr().out)["thread_id"] == "PRRT_TJlI"

    def test_thread_pagination_passes_end_cursor(self) -> None:
        rc, mock_run = _run(
            ["resolve-thread", "2047", "--comment", "77"],
            [
                _ok(_threads_page([_thread("PRRT_1", ["1"])], has_next=True, cursor="CUR1")),
                _ok(_threads_page([_thread("PRRT_2", ["77"])])),
                _ok(_mutation("PRRT_2")),
            ],
        )
        assert rc == EXIT_OK
        assert mock_run.call_count == 3
        first, second, mutation = _cmds(mock_run)
        assert "after" not in _graphql_vars(first)
        assert _graphql_vars(second)["after"] == ("-f", "CUR1")
        assert _graphql_vars(second)["number"] == ("-F", "2047")
        assert _graphql_vars(mutation)["threadId"] == ("-f", "PRRT_2")

    def test_comment_pagination_uses_node_query(self) -> None:
        first_page_ids = [str(i) for i in range(100)]
        rc, mock_run = _run(
            ["resolve-thread", "2047", "--comment", "150"],
            [
                _ok(
                    _threads_page(
                        [
                            _thread(
                                "PRRT_big",
                                first_page_ids,
                                comments_has_next=True,
                                comments_cursor="C100",
                            )
                        ]
                    )
                ),
                _ok(_comments_page(["149", "150"])),
                _ok(_mutation("PRRT_big")),
            ],
        )
        assert rc == EXIT_OK
        assert mock_run.call_count == 3
        _, comments_lookup, mutation = _cmds(mock_run)
        variables = _graphql_vars(comments_lookup)
        assert variables["threadId"] == ("-f", "PRRT_big")
        assert variables["after"] == ("-f", "C100")
        assert "node(id:" in _query(comments_lookup)
        assert _is_mutation(mutation)

    def test_comment_pagination_follows_multiple_pages_then_next_thread(self) -> None:
        rc, mock_run = _run(
            ["resolve-thread", "2047", "--comment", "900"],
            [
                _ok(
                    _threads_page(
                        [
                            _thread("PRRT_a", ["1"], comments_has_next=True, comments_cursor="A1"),
                            _thread("PRRT_b", ["900"]),
                        ]
                    )
                ),
                _ok(_comments_page(["2"], has_next=True, cursor="A2")),
                _ok(_comments_page(["3"])),
                _ok(_mutation("PRRT_b")),
            ],
        )
        assert rc == EXIT_OK
        cmds = _cmds(mock_run)
        assert len(cmds) == 4
        assert _graphql_vars(cmds[1])["after"] == ("-f", "A1")
        assert _graphql_vars(cmds[2])["after"] == ("-f", "A2")
        assert _graphql_vars(cmds[3])["threadId"] == ("-f", "PRRT_b")


# ----------------------------------------------------------------------------
# 異常系
# ----------------------------------------------------------------------------


@pytest.mark.usefixtures("gh_env")
class TestErrors:
    def test_not_found_after_all_pages(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc, mock_run = _run(
            ["resolve-thread", "2047", "--comment", "1"],
            [
                _ok(_threads_page([_thread("PRRT_1", ["10"])], has_next=True, cursor="C")),
                _ok(_threads_page([_thread("PRRT_2", ["20"])])),
            ],
        )
        assert rc == EXIT_INVALID_INPUT
        assert mock_run.call_count == 2
        assert not any(_is_mutation(c) for c in _cmds(mock_run))
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == (
            "Error: review comment 1 was not found in any review thread of PR #2047 (owner/repo).\n"
        )

    def test_not_found_with_no_threads(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc, mock_run = _run(["resolve-thread", "2047", "--comment", "1"], [_ok(_threads_page([]))])
        assert rc == EXIT_INVALID_INPUT
        assert mock_run.call_count == 1
        assert "was not found" in capsys.readouterr().err

    def test_permission_denied(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc, mock_run = _run(
            ["resolve-thread", "2047", "--comment", "5"],
            [_ok(_threads_page([_thread("PRRT_x", ["5"], can_resolve=False)]))],
        )
        assert rc == EXIT_RUNTIME_ERROR
        assert mock_run.call_count == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == (
            "Error: cannot resolve review thread PRRT_x for comment 5: "
            "viewerCanResolve is false (insufficient permission).\n"
        )

    @pytest.mark.parametrize(
        ("responses", "phase"),
        [
            ([_fail("gh: Could not resolve to a PullRequest with the number of 9.\n")], None),
            (
                [
                    _ok(
                        _threads_page(
                            [_thread("PRRT_a", ["1"], comments_has_next=True, comments_cursor="A1")]
                        )
                    ),
                    _fail("HTTP 502\n"),
                ],
                "thread comments lookup",
            ),
            (
                [_ok(_threads_page([_thread("PRRT_x", ["5"])])), _fail("gh: auth required\n")],
                "resolveReviewThread",
            ),
        ],
        ids=["lookup", "comments", "mutation"],
    )
    def test_gh_failure_relays_stderr_with_phase(
        self,
        responses: list[subprocess.CompletedProcess[str]],
        phase: str | None,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        expected_phase = phase or "reviewThreads lookup"
        rc, mock_run = _run(["resolve-thread", "9", "--comment", "5"], responses)
        assert rc == EXIT_RUNTIME_ERROR
        assert mock_run.call_count == len(responses)
        captured = capsys.readouterr()
        assert captured.out == ""
        gh_stderr = responses[-1].stderr
        assert captured.err == (
            f"{gh_stderr}Error: GitHub GraphQL request failed ({expected_phase}).\n"
        )

    def test_gh_oserror_is_runtime_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        from kaji_harness.commands.pr import _handle_pr

        with patch("subprocess.run", side_effect=OSError("exec format error")):
            rc = _handle_pr(["resolve-thread", "9", "--comment", "5"])
        assert rc == EXIT_RUNTIME_ERROR
        err = capsys.readouterr().err
        assert "exec format error" in err
        assert "Error: GitHub GraphQL request failed (reviewThreads lookup)." in err

    @pytest.mark.parametrize(
        "stdout",
        [
            "not json",
            json.dumps({"data": {"repository": {"pullRequest": None}}}),
            json.dumps({"data": {"repository": None}}),
            json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": {}}}}}),
            json.dumps(_threads_page([{"id": "PRRT_x", "isResolved": False}])),
        ],
        ids=["non-json", "pr-null", "repo-null", "missing-fields", "thread-missing-fields"],
    )
    def test_unexpected_lookup_response(
        self, stdout: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc, mock_run = _run(["resolve-thread", "9", "--comment", "5"], [_ok(stdout)])
        assert rc == EXIT_RUNTIME_ERROR
        assert mock_run.call_count == 1
        err = capsys.readouterr().err
        assert err.startswith("Error: unexpected GitHub GraphQL response (reviewThreads lookup): ")
        assert "Traceback" not in err

    def test_next_page_without_cursor_is_unexpected(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc, mock_run = _run(
            ["resolve-thread", "9", "--comment", "5"],
            [_ok(_threads_page([_thread("PRRT_1", ["1"])], has_next=True, cursor=None))],
        )
        assert rc == EXIT_RUNTIME_ERROR
        assert mock_run.call_count == 1
        assert "unexpected GitHub GraphQL response (reviewThreads lookup)" in (
            capsys.readouterr().err
        )

    def test_unexpected_comments_page_response(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc, _ = _run(
            ["resolve-thread", "9", "--comment", "5"],
            [
                _ok(
                    _threads_page(
                        [_thread("PRRT_a", ["1"], comments_has_next=True, comments_cursor="A1")]
                    )
                ),
                _ok({"data": {"node": None}}),
            ],
        )
        assert rc == EXIT_RUNTIME_ERROR
        assert "unexpected GitHub GraphQL response (thread comments lookup)" in (
            capsys.readouterr().err
        )

    def test_unexpected_mutation_response(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc, _ = _run(
            ["resolve-thread", "9", "--comment", "5"],
            [_ok(_threads_page([_thread("PRRT_x", ["5"])])), _ok({"data": {}})],
        )
        assert rc == EXIT_RUNTIME_ERROR
        assert "unexpected GitHub GraphQL response (resolveReviewThread)" in (
            capsys.readouterr().err
        )

    @pytest.mark.parametrize(
        "payload",
        [
            _mutation("PRRT_x", resolved=False),
            {"data": {"resolveReviewThread": {"thread": None}}},
        ],
        ids=["is-resolved-false", "thread-null"],
    )
    def test_mutation_not_effective(
        self, payload: dict[str, Any], capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc, mock_run = _run(
            ["resolve-thread", "9", "--comment", "5"],
            [_ok(_threads_page([_thread("PRRT_x", ["5"])])), _ok(payload)],
        )
        assert rc == EXIT_RUNTIME_ERROR
        assert mock_run.call_count == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == (
            "Error: resolveReviewThread did not resolve thread PRRT_x for comment 5.\n"
        )


class TestEnvironment:
    def test_gh_missing(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("shutil.which", return_value=None):
            rc, mock_run = _run(["resolve-thread", "9", "--comment", "5"], [])
        assert rc == EXIT_RUNTIME_ERROR
        assert mock_run.call_count == 0
        err = capsys.readouterr().err
        assert "'gh' CLI not found in PATH" in err
        assert "resolve-thread" in err

    def test_repo_detection_failure(self, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch("shutil.which", return_value="/usr/bin/gh"),
            patch("kaji_harness.commands.pr._detect_repo", return_value=None),
        ):
            rc, mock_run = _run(["resolve-thread", "9", "--comment", "5"], [])
        assert rc == EXIT_RUNTIME_ERROR
        assert mock_run.call_count == 0
        assert "Error: failed to detect current repository." in capsys.readouterr().err

    @pytest.mark.parametrize("repo", ["noslash", "a/b/c", "/name", "owner/"])
    def test_repo_not_owner_name_form(self, repo: str, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch("shutil.which", return_value="/usr/bin/gh"),
            patch("kaji_harness.commands.pr._detect_repo", return_value=repo),
        ):
            rc, mock_run = _run(["resolve-thread", "9", "--comment", "5"], [])
        assert rc == EXIT_RUNTIME_ERROR
        assert mock_run.call_count == 0
        assert capsys.readouterr().err == (
            f"Error: repository must be in 'owner/name' form, got: {repo!r}.\n"
        )
