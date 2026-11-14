"""Large (large_local) tests for the deterministic ``issue-start`` entry (Issue #569).

実 git subprocess（ネットワーク無し）と fake provider で、1 プロセスの entry が
context 取得 → fetch / 照合 → ``--no-track`` worktree 作成 → symlink → NOTE → 報告 → verdict を
実行すること、失敗時に副作用を進めず ABORT すること、部分成功から同一 worktree を検証して
再利用できることを検証する。

fixture は ``outside_project_tmp_path`` に作る。workflow 内の ``tmp_path`` は kaji repo 内になり、
fixture 構築に失敗すると subprocess の git が実 repo を操作しうるため。
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from kaji_harness.issue_start import VALID_STATUSES
from kaji_harness.models import Verdict
from kaji_harness.providers.context import build_worktree_dir, build_worktree_note_body
from kaji_harness.providers.github import GitHubProviderError
from kaji_harness.providers.models import Comment, Issue, IssueContext
from kaji_harness.scripts import issue_start as entry
from kaji_harness.verdict import load_verdict_yaml, parse_verdict

pytestmark = [pytest.mark.large, pytest.mark.large_local]

_ISSUE = "1"
_BRANCH = "fix/1"
_MARKER = "<!-- kaji-verdict: step=start status=PASS -->"
_WRITES = ("edit_issue", "comment_issue")


def _legacy_report(branch: str, directory: str) -> str:
    """現行テンプレート（agent 投稿）の旧形式 start PASS 報告。"""
    return (
        f"{_MARKER}\n## Worktree セットアップ完了\n\n| 項目 | 値 |\n|------|-----|\n"
        f"| Issue | #1 |\n| ブランチ | {branch} |\n| ディレクトリ | {directory} |\n"
        "| 基点 | origin/main（0000000） |\n\n---VERDICT---\nstatus: PASS\nreason: |\n  ok\n"
        "evidence: |\n  ok\nsuggestion: |\n---END_VERDICT---"
    )


# ============================================================
# fixture
# ============================================================


def _git_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    for role in ("AUTHOR", "COMMITTER"):
        env[f"GIT_{role}_NAME"] = "kaji-test"
        env[f"GIT_{role}_EMAIL"] = "kaji-test@example.com"
    return env


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, env=_git_env(), capture_output=True, text=True, check=check
    )


def _out(cwd: Path, *args: str) -> str:
    return _git(cwd, *args).stdout.strip()


def _commit_file(repo: Path, name: str, content: str, message: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", message)


@dataclass(frozen=True)
class _Fixture:
    """bare remote・保守者 checkout（古い base のまま）・別 clone からなる fixture。"""

    root: Path
    bare: Path
    maintainer: Path
    remote: str = "origin"
    base: str = "main"

    def worktree(self, prefix: str = "") -> Path:
        return Path(build_worktree_dir("fix", _ISSUE, self.maintainer, prefix))

    def context(self, prefix: str = "") -> IssueContext:
        return IssueContext(
            issue_id=_ISSUE,
            issue_ref="#1",
            issue_input=_ISSUE,
            slug="x",
            branch_prefix="fix",
            branch_name=_BRANCH,
            worktree_dir=str(self.worktree(prefix)),
            design_path="draft/design/issue-1-x.md",
            provider_type="github",
            default_branch=self.base,
            git_remote=self.remote,
        )


@pytest.fixture
def fx(outside_project_tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Fixture:
    """remote の main だけが 1 commit 進み、保守者 checkout の main は古いままの状態を作る。"""
    root = outside_project_tmp_path
    seed = root / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    _commit_file(seed, "f.txt", "v1\n", "init")
    _commit_file(seed, ".kaji/config.toml", "[paths]\n", "config")
    bare = root / "remote.git"
    _git(root, "clone", "-q", "--bare", str(seed), str(bare))
    maintainer = root / "maintainer"
    _git(root, "clone", "-q", str(bare), str(maintainer))
    (maintainer / ".venv").mkdir()
    other = root / "other"
    _git(root, "clone", "-q", str(bare), str(other))
    _commit_file(other, "f.txt", "v2\n", "outside kaji")
    _git(other, "push", "-q", "origin", "HEAD:main")

    for key in list(os.environ):
        if key.startswith(("GIT_", "KAJI_")):
            monkeypatch.delenv(key)
    for key, value in _git_env().items():
        if key.startswith("GIT_"):
            monkeypatch.setenv(key, value)
    monkeypatch.chdir(maintainer)
    return _Fixture(root=root, bare=bare, maintainer=maintainer)


@dataclass
class _FakeProvider:
    """``resolve_issue_context`` / ``view_issue`` / ``edit_issue`` / ``comment_issue`` を記録する。"""

    context: IssueContext
    body: str = "## 概要\n\n本文\n"
    comments: list[Comment] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    fail_comment: bool = False
    observed_on_write: list[tuple[str, bool]] = field(default_factory=list)

    def resolve_issue_context(self, issue_id: str) -> IssueContext:
        assert issue_id == _ISSUE
        self.calls.append("resolve_issue_context")
        return self.context

    def view_issue(self, issue_id: str) -> Issue:
        assert issue_id == _ISSUE
        self.calls.append("view_issue")
        return Issue(
            id=issue_id, title="t", body=self.body, state="open", comments=list(self.comments)
        )

    def edit_issue(self, issue_id: str, *, body: str | None = None, **_: object) -> Issue:
        self.calls.append("edit_issue")
        self._observe("edit_issue")
        assert body is not None
        self.body = body
        return Issue(id=issue_id, title="t", body=body, state="open")

    def comment_issue(self, issue_id: str, body: str) -> Comment:
        self.calls.append("comment_issue")
        self._observe("comment_issue")
        if self.fail_comment:
            self.fail_comment = False
            raise GitHubProviderError("gh issue comment failed: HTTP 502")
        comment = Comment(author="kaji", body=body, created_at="2026-11-14T00:00:00Z")
        self.comments.append(comment)
        return comment

    def _observe(self, name: str) -> None:
        self.observed_on_write.append((name, Path(self.context.worktree_dir, ".venv").is_symlink()))

    @property
    def writes(self) -> list[str]:
        return [call for call in self.calls if call in _WRITES]

    @property
    def start_reports(self) -> list[Comment]:
        return [c for c in self.comments if c.body.startswith(_MARKER + "\n")]


def _run(
    capsys: pytest.CaptureFixture[str],
    provider: _FakeProvider,
    argv: list[str] | None = None,
) -> Verdict:
    capsys.readouterr()
    rc = entry.main([_ISSUE] if argv is None else argv, provider=provider)  # type: ignore[arg-type]
    assert rc == 0
    return parse_verdict(capsys.readouterr().out, set(VALID_STATUSES))


def _branch_sha(repo: Path, branch: str) -> str | None:
    proc = _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False)
    return proc.stdout.strip() if proc.returncode == 0 else None


def _create_worktree(fx: _Fixture, prefix: str = "") -> Path:
    """worktree 作成直後に止まった状態（#551 型の部分成功）を作る。"""
    _git(fx.maintainer, "fetch", "-q", fx.remote, fx.base)
    wt = fx.worktree(prefix)
    _git(
        fx.maintainer, "worktree", "add", "-q", "--no-track", "-b", _BRANCH, str(wt), "origin/main"
    )
    return wt


def _add_user_changes(wt: Path) -> str:
    _commit_file(wt, "feature.txt", "user commit\n", "user work")
    (wt / "f.txt").write_text("uncommitted\n", encoding="utf-8")
    return _out(wt, "rev-parse", "HEAD")


def _assert_user_changes_kept(wt: Path, head: str) -> None:
    assert _out(wt, "rev-parse", "HEAD") == head
    assert (wt / "f.txt").read_text(encoding="utf-8") == "uncommitted\n"
    assert (wt / "feature.txt").read_text(encoding="utf-8") == "user commit\n"


# ============================================================
# 正常系（回帰テスト 2 を含む）
# ============================================================


def test_entry_without_kaji_env_starts_from_fetched_remote_base(
    fx: _Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """#560 の回帰: KAJI_* env もシェル変数も無い状態で、1 プロセスが fetched base から作成する。"""
    assert not any(key.startswith("KAJI_") for key in os.environ)
    stale_local = _out(fx.maintainer, "rev-parse", "refs/heads/main")
    remote_tip = _out(fx.bare, "rev-parse", "main")
    assert stale_local != remote_tip
    provider = _FakeProvider(fx.context())

    verdict = _run(capsys, provider)

    assert verdict.status == "PASS", verdict
    wt = fx.worktree()
    assert _out(wt, "rev-parse", "HEAD") == remote_tip
    assert _out(wt, "branch", "--show-current") == _BRANCH
    assert (wt / "f.txt").read_text(encoding="utf-8") == "v2\n"
    assert _git(wt, "rev-parse", "--abbrev-ref", "@{u}", check=False).returncode != 0
    assert _out(fx.maintainer, "rev-parse", "refs/heads/main") == stale_local
    assert os.readlink(wt / ".venv") == str(fx.maintainer / ".venv")
    assert not (wt / ".kaji" / "config.local.toml").is_symlink()


def test_normal_run_order_note_report_and_verdict_artifact(
    fx: _Fixture, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    (fx.maintainer / ".kaji" / "config.local.toml").write_text("[execution]\n", encoding="utf-8")
    verdict_path = fx.root / "artifacts" / "verdict.yaml"
    ctx = fx.context()
    monkeypatch.setenv("KAJI_ISSUE_ID", _ISSUE)
    monkeypatch.setenv("KAJI_WORKTREE_DIR", ctx.worktree_dir)
    monkeypatch.setenv("KAJI_BRANCH_NAME", ctx.branch_name)
    monkeypatch.setenv("KAJI_GIT_REMOTE", ctx.git_remote)
    monkeypatch.setenv("KAJI_DEFAULT_BRANCH", ctx.default_branch)
    monkeypatch.setenv("KAJI_VERDICT_PATH", str(verdict_path))
    provider = _FakeProvider(ctx)

    verdict = _run(capsys, provider, argv=[])

    assert verdict.status == "PASS"
    assert load_verdict_yaml(verdict_path, set(VALID_STATUSES)) == verdict
    wt = fx.worktree()
    assert os.readlink(wt / ".kaji" / "config.local.toml") == str(
        fx.maintainer / ".kaji" / "config.local.toml"
    )
    assert provider.calls[0] == "resolve_issue_context"
    assert provider.writes == ["edit_issue", "comment_issue"]
    # Issue への書き込みは worktree と symlink の作成後
    assert provider.observed_on_write == [("edit_issue", True), ("comment_issue", True)]
    assert provider.body == build_worktree_note_body(
        "## 概要\n\n本文\n", worktree=wt.name, branch=_BRANCH
    )
    [report] = provider.start_reports
    remote_short = _out(fx.maintainer, "rev-parse", "--short", "origin/main")
    assert f"| 基点 | origin/main（{remote_short}） |" in report.body
    assert "| config overlay | シンボリックリンク作成済み |" in report.body
    assert parse_verdict(report.body, set(VALID_STATUSES)) == verdict


def test_local_ahead_commits_are_excluded_and_reported(
    fx: _Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    _commit_file(fx.maintainer, "local.txt", "x\n", "unpushed")
    provider = _FakeProvider(fx.context())

    verdict = _run(capsys, provider)

    assert verdict.status == "PASS"
    assert not (fx.worktree() / "local.txt").exists()
    [report] = provider.start_reports
    assert "ローカル `main` に `origin/main` へ未 push の commit が 1 件" in report.body


# ============================================================
# 失敗系: 副作用を進めず ABORT
# ============================================================


def test_unreachable_remote_aborts_without_side_effects(
    fx: _Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    _git(fx.maintainer, "remote", "set-url", "origin", str(fx.root / "missing.git"))
    provider = _FakeProvider(fx.context())

    verdict = _run(capsys, provider)

    assert verdict.status == "ABORT"
    assert "git fetch origin main" in verdict.evidence
    assert "kaji issue start 1" in verdict.suggestion
    assert not fx.worktree().exists()
    assert _branch_sha(fx.maintainer, _BRANCH) is None
    assert provider.writes == []


def test_stale_tracking_ref_aborts_without_side_effects(
    fx: _Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    _git(fx.maintainer, "config", "remote.origin.fetch", "+refs/heads/x:refs/remotes/origin/x")
    provider = _FakeProvider(fx.context())

    verdict = _run(capsys, provider)

    assert verdict.status == "ABORT"
    assert "FETCH_HEAD" in verdict.evidence
    assert not fx.worktree().exists()
    assert _branch_sha(fx.maintainer, _BRANCH) is None
    assert provider.writes == []


@pytest.mark.parametrize(
    "case",
    [
        "path-on-other-branch",
        "branch-in-other-path",
        "branch-only",
        "unregistered-path",
        "unregistered-dangling-symlink",
    ],
)
def test_worktree_mismatch_aborts_and_keeps_existing_state(
    fx: _Fixture, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    wt = fx.worktree()
    _git(fx.maintainer, "fetch", "-q", "origin", "main")
    if case == "path-on-other-branch":
        _git(fx.maintainer, "worktree", "add", "-q", "-b", "other/1", str(wt), "origin/main")
    elif case == "branch-in-other-path":
        _git(fx.maintainer, "worktree", "add", "-q", "-b", _BRANCH, str(fx.root / "else"), "main")
    elif case == "branch-only":
        _git(fx.maintainer, "branch", _BRANCH, "main")
    elif case == "unregistered-path":
        wt.mkdir()
        (wt / "keep.txt").write_text("keep\n", encoding="utf-8")
    else:
        # 期待パスに未登録の dangling symlink。realpath 化でリンク先へ作成してはならない。
        wt.symlink_to(fx.root / "unexpected-target", target_is_directory=True)
    branch_before = _branch_sha(fx.maintainer, _BRANCH)
    listing_before = _out(fx.maintainer, "worktree", "list", "--porcelain")
    provider = _FakeProvider(fx.context())

    verdict = _run(capsys, provider)

    assert verdict.status == "ABORT", verdict
    assert _branch_sha(fx.maintainer, _BRANCH) == branch_before
    assert _out(fx.maintainer, "worktree", "list", "--porcelain") == listing_before
    assert not (wt / ".venv").is_symlink()
    if case == "unregistered-path":
        assert (wt / "keep.txt").read_text(encoding="utf-8") == "keep\n"
    if case == "unregistered-dangling-symlink":
        assert os.readlink(wt) == str(fx.root / "unexpected-target")
        assert not os.path.lexists(fx.root / "unexpected-target")
        assert "state=path_exists_unregistered" in verdict.evidence
    assert provider.writes == []


@pytest.mark.parametrize("link", [".venv", ".kaji/config.local.toml"])
def test_symlink_conflict_aborts_without_overwriting(
    fx: _Fixture, capsys: pytest.CaptureFixture[str], link: str
) -> None:
    (fx.maintainer / ".kaji" / "config.local.toml").write_text("[execution]\n", encoding="utf-8")
    wt = _create_worktree(fx)
    elsewhere = str(fx.root / "elsewhere")
    os.symlink(elsewhere, wt / link)
    provider = _FakeProvider(fx.context())

    verdict = _run(capsys, provider)

    assert verdict.status == "ABORT"
    assert link in verdict.evidence
    assert os.readlink(wt / link) == elsewhere
    assert provider.writes == []


# ============================================================
# 部分成功からの再実行
# ============================================================


def test_rerun_after_worktree_creation_keeps_user_changes(
    fx: _Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    wt = _create_worktree(fx)
    head = _add_user_changes(wt)
    provider = _FakeProvider(fx.context())

    verdict = _run(capsys, provider)

    assert verdict.status == "PASS", verdict
    _assert_user_changes_kept(wt, head)
    assert os.readlink(wt / ".venv") == str(fx.maintainer / ".venv")
    assert provider.writes == ["edit_issue", "comment_issue"]
    [report] = provider.start_reports
    assert "| 再利用 |" in report.body
    assert "現在の共通祖先" in report.body


def test_rerun_after_comment_failure_posts_once_without_duplicate_note(
    fx: _Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    provider = _FakeProvider(fx.context(), fail_comment=True)

    first = _run(capsys, provider)

    assert first.status == "ABORT"
    assert "Issue NOTE" in first.evidence
    assert provider.start_reports == []

    second = _run(capsys, provider)

    assert second.status == "PASS", second
    assert provider.body.count("> [!NOTE]") == 1
    assert len(provider.start_reports) == 1
    assert provider.writes == ["edit_issue", "comment_issue", "comment_issue"]


def test_rerun_after_success_does_not_repeat_report(
    fx: _Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    provider = _FakeProvider(fx.context())
    assert _run(capsys, provider).status == "PASS"
    writes_after_first = list(provider.writes)

    verdict = _run(capsys, provider)

    assert verdict.status == "PASS"
    assert provider.writes == writes_after_first
    assert len(provider.start_reports) == 1
    assert provider.body.count("> [!NOTE]") == 1


def test_legacy_same_report_and_note_are_not_duplicated(
    fx: _Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """#551 型: agent が旧形式で NOTE と PASS 報告を投稿した後の再実行。"""
    wt = _create_worktree(fx)
    head = _add_user_changes(wt)
    provider = _FakeProvider(
        fx.context(),
        body=build_worktree_note_body("## 概要\n", worktree=wt.name, branch=_BRANCH),
        comments=[Comment("agent", _legacy_report(_BRANCH, f"../{wt.name}"), "t")],
    )

    verdict = _run(capsys, provider)

    assert verdict.status == "PASS", verdict
    assert provider.writes == []
    _assert_user_changes_kept(wt, head)
    assert os.readlink(wt / ".venv") == str(fx.maintainer / ".venv")


@pytest.mark.parametrize("reuse", [False, True], ids=["new", "reuse"])
def test_legacy_report_of_other_start_is_not_treated_as_same(
    fx: _Fixture, capsys: pytest.CaptureFixture[str], reuse: bool
) -> None:
    if reuse:
        _create_worktree(fx)
    provider = _FakeProvider(
        fx.context(), comments=[Comment("agent", _legacy_report("feat/1", "../kaji-feat-1"), "t")]
    )

    verdict = _run(capsys, provider)

    assert verdict.status == "PASS", verdict
    assert len(provider.start_reports) == 2
    assert provider.writes.count("comment_issue") == 1


def test_unidentifiable_report_aborts_before_any_git_or_issue_write(
    fx: _Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    tracking_before = _out(fx.maintainer, "rev-parse", "refs/remotes/origin/main")
    broken = _legacy_report(_BRANCH, "../kaji-fix-1").replace("| ブランチ | fix/1 |\n", "")
    provider = _FakeProvider(
        fx.context(), comments=[Comment("agent", broken, "t", ref="https://example/c/1")]
    )

    verdict = _run(capsys, provider)

    assert verdict.status == "ABORT"
    assert "https://example/c/1" in verdict.suggestion
    assert "完了済みの副作用: なし" in verdict.evidence
    assert _out(fx.maintainer, "rev-parse", "refs/remotes/origin/main") == tracking_before
    assert _branch_sha(fx.maintainer, _BRANCH) is None
    assert not fx.worktree().exists()
    assert provider.writes == []


# ============================================================
# 合法な worktree prefix（R2）
# ============================================================


@pytest.mark.parametrize("prefix", ["_kaji", ".kaji", "-kaji"])
def test_legal_prefix_first_run_and_rerun(
    fx: _Fixture, capsys: pytest.CaptureFixture[str], prefix: str
) -> None:
    provider = _FakeProvider(fx.context(prefix))

    first = _run(capsys, provider)
    second = _run(capsys, provider)

    assert (first.status, second.status) == ("PASS", "PASS")
    wt = fx.worktree(prefix)
    assert wt.name == f"{prefix}-fix-1"
    assert _out(wt, "branch", "--show-current") == _BRANCH
    assert len(provider.start_reports) == 1
    assert f"| ディレクトリ | ../{prefix}-fix-1 |" in provider.start_reports[0].body
