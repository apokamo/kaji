"""Large (local) tests: `review_poll_entry` を実 subprocess として端から端まで検証する (Issue #429).

stub の `gh` / `kaji` / `git` 実行ファイルを `PATH` の先頭に置き、fixture JSON を返させる。
env の解釈 → argv の受け渡し → 取得 / 判定 → 証跡の保存 → stdout の verdict という経路を
実プロセスで通す。実 GitHub API（bot の非同期応答）に依存する確認は、ワークフロー完了後の
確認項目として実 PR で行う（CI で再現できないため恒久テストにしない）。
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from kaji_harness.review_poll_evidence import EVIDENCE_FILENAME, load_evidence
from kaji_harness.verdict import parse_verdict_block

pytestmark = [pytest.mark.large, pytest.mark.large_local]

REPO_ROOT = Path(__file__).resolve().parent.parent
HEAD = "91d11b151e50a90ff02ffd427205f78017205bb4"
HEAD_AT = "2026-09-08T01:23:50Z"
BOT = {"id": 199175422, "login": "chatgpt-codex-connector[bot]"}
PR_URL = "https://github.com/owner/repo/pull/163"
VALID_STATUSES = {"PASS", "RETRY", "BACK_FALLBACK", "ABORT"}

_GH_STUB = """
import json, os, sys

data = json.load(open(os.environ["STUB_DATA"]))
args = sys.argv[1:]
if args[:1] != ["api"]:
    sys.exit("unexpected gh invocation: %r" % (args,))
path = args[-1]
paginated = "--paginate" in args and "--slurp" in args
if path.endswith("/pulls/163"):
    print(json.dumps(data["pulls"]))
else:
    kind = path.rsplit("/", 1)[1]
    pages = data["pages"][kind]
    if not paginated:
        sys.exit("list endpoint %s must be fetched with --paginate --slurp" % path)
    print(json.dumps(pages))
"""

_KAJI_STUB = """
import json, os, sys

data = json.load(open(os.environ["STUB_DATA"]))
args = sys.argv[1:]
if args[:2] == ["pr", "view"]:
    jq = args[args.index("--jq") + 1]
    if jq == ".headRefOid":
        print(data["pr_view"]["headRefOid"])
        sys.exit(0)
    if jq == ".commits[-1].committedDate":
        print(data["pr_view"]["committedDate"])
        sys.exit(0)
sys.exit("unexpected kaji invocation: %r" % (args,))
"""

_GIT_STUB = """
import sys

if sys.argv[1:3] == ["remote", "get-url"]:
    print("git@github.com:owner/repo.git")
    sys.exit(0)
sys.exit("unexpected git invocation: %r" % (sys.argv[1:],))
"""


def _write_stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!{sys.executable}\n{textwrap.dedent(body)}", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _summary_comment() -> dict[str, Any]:
    body = (
        "<!-- codex-pull-request-review-summary -->\n\n## Codex Review Summary\n\n"
        "| Review | Status | Commit | Review trigger |\n| --- | --- | --- | --- |\n"
        "| 📝 **Code Review** | ✅ **Completed** "
        '<relative-time datetime="2026-09-08T01:33:32.391219Z">x</relative-time> '
        "| `91d11b1` | PR opened |\n"
    )
    return {
        "id": 5577710898,
        "html_url": f"{PR_URL}#issuecomment-5577710898",
        "user": BOT,
        "created_at": "2026-09-08T01:30:05Z",
        "updated_at": "2026-09-08T01:33:33Z",
        "body": body,
    }


def _plus_one() -> dict[str, Any]:
    return {"id": 4242, "user": BOT, "content": "+1", "created_at": "2026-09-08T01:33:35Z"}


def _data(*, pr_head: str = HEAD) -> dict[str, Any]:
    return {
        "pulls": {"head": {"sha": pr_head}, "state": "open", "html_url": PR_URL},
        "pr_view": {"headRefOid": HEAD, "committedDate": HEAD_AT},
        # 各リストを 2 ページに分け、2 ページ目にしかないシグナルも拾えることを確かめる
        "pages": {
            "reactions": [[], [_plus_one()]],
            "reviews": [[], []],
            "comments": [[], [_summary_comment()]],
            "commits": [[{"sha": "0" * 40}], [{"sha": HEAD}]],
        },
    }


def _run_entry(
    tmp_path: Path, data: dict[str, Any], *, with_verdict_path: bool = True
) -> tuple[subprocess.CompletedProcess[str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stub(bin_dir, "gh", _GH_STUB)
    _write_stub(bin_dir, "kaji", _KAJI_STUB)
    _write_stub(bin_dir, "git", _GIT_STUB)
    data_path = tmp_path / "stub-data.json"
    data_path.write_text(json.dumps(data), encoding="utf-8")

    attempt_dir = tmp_path / "steps" / "review-poll" / "attempt-001"
    attempt_dir.mkdir(parents=True)
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "PYTHONPATH": str(REPO_ROOT),
        "STUB_DATA": str(data_path),
        "KAJI_PROVIDER_TYPE": "github",
        "KAJI_ISSUE_ID": "163",
        "KAJI_PR_ID": "163",
        "KAJI_GIT_REMOTE": "origin",
        "KAJI_WORKTREE_DIR": str(worktree),
    }
    env.pop("KAJI_VERDICT_PATH", None)
    if with_verdict_path:
        env["KAJI_VERDICT_PATH"] = str(attempt_dir / "verdict.yaml")

    proc = subprocess.run(
        [sys.executable, "-m", "kaji_harness.scripts.review_poll_entry"],
        env=env,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return proc, attempt_dir / EVIDENCE_FILENAME


class TestReviewPollEntryEndToEnd:
    def test_pass_saves_head_bound_evidence_and_verdict(self, tmp_path: Path) -> None:
        proc, evidence_path = _run_entry(tmp_path, _data())
        assert proc.returncode == 0, proc.stderr

        verdict = parse_verdict_block(proc.stdout, VALID_STATUSES)
        assert verdict is not None and verdict.status == "PASS", proc.stdout
        assert f"head_sha={HEAD}" in verdict.evidence
        assert "pr=owner/repo#163" in verdict.evidence
        assert f"evidence_path={evidence_path}" in verdict.evidence

        evidence = load_evidence(evidence_path)
        assert evidence.repository.owner == "owner" and evidence.repository.name == "repo"
        assert evidence.pull_request.number == 163
        assert evidence.reviewed_head.sha == HEAD
        assert evidence.approval.reaction.id == 4242
        assert evidence.approval.review_summary.comment_id == 5577710898
        assert evidence.checks.short_sha_matching_pr_commits == 1

    def test_head_moved_after_resolution_aborts_without_evidence(self, tmp_path: Path) -> None:
        # entry が解決した head と、API が返す現在の head が違う（push された）
        proc, evidence_path = _run_entry(tmp_path, _data(pr_head="1" * 40))
        assert proc.returncode == 0, proc.stderr
        verdict = parse_verdict_block(proc.stdout, VALID_STATUSES)
        assert verdict is not None and verdict.status == "ABORT", proc.stdout
        assert "head changed" in verdict.evidence
        assert not evidence_path.exists()

    def test_pass_condition_without_verdict_path_aborts(self, tmp_path: Path) -> None:
        proc, _ = _run_entry(tmp_path, _data(), with_verdict_path=False)
        assert proc.returncode == 0, proc.stderr
        verdict = parse_verdict_block(proc.stdout, VALID_STATUSES)
        assert verdict is not None and verdict.status == "ABORT", proc.stdout
        assert "evidence unavailable" in verdict.evidence
        assert not list(tmp_path.rglob(EVIDENCE_FILENAME))
