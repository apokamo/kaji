"""Medium tests: ``run_watched plan`` / ``execute`` (Issue #543 A2・A3・M3・M7・M8).

tmp の git repo（bare の remote 付き）で git は実物を使い、GitHub provider と Herdr は fake。
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from kaji_harness.errors import WatchPreconditionError
from kaji_harness.providers import Issue, Label
from kaji_harness.scripts import run_watched
from kaji_harness.scripts.run_watched import (
    ExecuteDeps,
    Git,
    PlanDeps,
    RunWatchedError,
    load_draft,
    run_execute,
    run_plan,
)
from kaji_harness.watch.herdr import TabRef

pytestmark = pytest.mark.medium

NOW = datetime(2026, 11, 15, 3, 1, tzinfo=UTC)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeIssue:
    state: str = "open"
    labels: set[str] = field(default_factory=set)


class FakeProvider:
    def __init__(self, issues: dict[int, FakeIssue]) -> None:
        self.issues = issues
        self.add_calls: list[tuple[str, list[str]]] = []
        self.fail_after_edit = False
        self.pr_branches: set[str] = set()

    def view_issue(self, issue_id: str) -> Issue:
        data = self.issues[int(issue_id)]
        return Issue(
            id=issue_id,
            title="t",
            body="",
            state=data.state,
            labels=[Label(name=n) for n in sorted(data.labels)],
        )

    def edit_issue(
        self,
        issue_id: str,
        *,
        title: str | None = None,
        body: str | None = None,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> Issue:
        self.add_calls.append((issue_id, list(add_labels or [])))
        self.issues[int(issue_id)].labels.update(add_labels or [])
        if self.fail_after_edit:
            self.fail_after_edit = False
            raise RuntimeError("injected: after label")
        return self.view_issue(issue_id)

    def resolve_issue_context(self, issue_id: str) -> Any:
        @dataclass
        class _Ctx:
            branch_prefix: str = "feat"

        return _Ctx()

    def resolve_pr_context(self, branch_name: str) -> Any:
        return object() if branch_name in self.pr_branches else None


class FakeHerdr:
    def __init__(self) -> None:
        self.tabs: list[TabRef] = []
        self.created: list[str] = []
        self.runs: list[tuple[str, str]] = []
        self.fail_after_create = False
        self.fail_after_run = False

    def list_tabs(self) -> list[TabRef]:
        return list(self.tabs)

    def create_tab(self, label: str, cwd: Path) -> tuple[str, str]:
        n = len(self.created) + 1
        self.created.append(label)
        self.tabs.append(TabRef(tab_id=f"t_{n}", pane_id=f"p_{n}", label=label))
        if self.fail_after_create:
            self.fail_after_create = False
            raise RuntimeError("injected: after tab create")
        return f"t_{n}", f"p_{n}"

    def run_in_pane(self, pane_id: str, command: str) -> None:
        self.runs.append((pane_id, command))
        if self.fail_after_run:
            self.fail_after_run = False
            raise RuntimeError("injected: after pane run")

    def close_tab(self, tab_id: str) -> None:
        self.tabs = [t for t in self.tabs if t.tab_id != tab_id]

    def pane_foreground(self, pane_id: str) -> tuple[str, list[str]]:
        commands = [cmd for pane, cmd in self.runs if pane == pane_id]
        return ("active", commands) if commands else ("shell_only", [])

    def agent_status(self, pane_id: str, origin_pane: str | None, run_id: str | None) -> str:
        return "unknown"


@dataclass
class Env:
    repo: Path
    remote: Path
    artifacts: Path
    provider: FakeProvider
    herdr: FakeHerdr
    validate_rc: int = 0
    watch_calls: int = 0
    preflight_error: Exception | None = None

    def deps(self) -> ExecuteDeps:
        def _preflight() -> None:
            if self.preflight_error is not None:
                raise self.preflight_error

        def _watch() -> str:
            self.watch_calls += 1
            return "watch started (pane p_w)"

        return ExecuteDeps(
            preflight=_preflight,
            provider=self.provider,
            herdr=self.herdr,
            artifacts_dir=self.artifacts,
            repo_root=self.repo,
            git_remote="origin",
            default_branch="main",
            run_command=lambda argv: self.validate_rc,
            watch_start=_watch,
        )

    def plan(self) -> str:
        code, document = run_plan(
            PlanDeps(provider=self.provider, artifacts_dir=self.artifacts, repo_root=self.repo),
            [101, 102],
            workflow="dev",
            member_workflows={},
            chosen_by_skill=False,
            order_ambiguous=False,
            now=NOW,
        )
        assert code == 0, document
        return str(document["draft_id"])

    def remote_log(self) -> list[str]:
        return _git(self.remote, "log", "--format=%s", "main").splitlines()

    def remote_head(self) -> str:
        return _git(self.remote, "rev-parse", "main")


@pytest.fixture
def env(tmp_path: Path) -> Env:
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", "-b", "main", str(remote))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "user.email", "t@example.invalid")
    wf = repo / ".kaji" / "wf" / "official"
    wf.mkdir(parents=True)
    (wf / "dev.yaml").write_text("name: dev\n", encoding="utf-8")
    (wf / "docs.yaml").write_text("name: docs\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "origin", "main")
    provider = FakeProvider({101: FakeIssue(), 102: FakeIssue()})
    return Env(repo, remote, tmp_path / "artifacts", provider, FakeHerdr())


def _yaml(env: Env, draft_id: str) -> Path:
    return env.repo / load_draft(env.artifacts, draft_id).yaml_path


def _assert_converged(env: Env, draft_id: str) -> None:
    assert env.remote_log() == [
        "chore(series): add rw-101-102-2611150301 for #101, #102",
        "init",
    ]
    parent, head = _git(env.remote, "rev-parse", "main~1"), env.remote_head()
    diff = _git(env.remote, "diff-tree", "--no-commit-id", "-r", "--name-status", parent, head)
    assert diff == f"A\t{load_draft(env.artifacts, draft_id).yaml_path}"
    assert _git(env.repo, "rev-parse", "HEAD") == head
    for issue in (101, 102):
        assert "state:planned" in env.provider.issues[issue].labels
    assert [issue for issue, labels in env.provider.add_calls if labels] == ["101", "102"]
    assert env.herdr.created == ["kaji-series-rw-101-102-2611150301"]
    assert len(env.herdr.runs) == 1
    assert (
        env.herdr.runs[0][1] == f"kaji run-series {load_draft(env.artifacts, draft_id).yaml_path}"
    )


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


def test_plan_saves_draft_and_reports_exclusions(env: Env) -> None:
    env.provider.issues[103] = FakeIssue(labels={"state:hold"})
    env.provider.issues[104] = FakeIssue(labels={"wf:docs"})
    env.provider.pr_branches.add("feat/105")
    env.provider.issues[105] = FakeIssue()
    code, document = run_plan(
        PlanDeps(provider=env.provider, artifacts_dir=env.artifacts, repo_root=env.repo),
        [101, 103, 104, 105],
        workflow="dev",
        member_workflows={},
        chosen_by_skill=False,
        order_ambiguous=False,
        now=NOW,
    )
    assert code == 0
    assert document["members"] == [{"issue": 101, "workflow": ".kaji/wf/official/dev.yaml"}]
    assert {(e["issue"], e["reason"]) for e in document["excluded"]} == {
        (103, "state_label"),
        (104, "workflow_mismatch"),
        (105, "worktree_or_pr"),
    }
    assert document["approval_required"] is True
    draft = load_draft(env.artifacts, document["draft_id"])
    assert draft.series_id == "rw-101-101-2611150301"
    assert draft.yaml_path == ".kaji/series/rw-101-101-2611150301.yaml"
    assert env.provider.add_calls == []


def test_plan_needs_selection_without_workflow(env: Env) -> None:
    env.provider.issues[102].labels.add("wf:docs")
    code, document = run_plan(
        PlanDeps(provider=env.provider, artifacts_dir=env.artifacts, repo_root=env.repo),
        [101, 102],
        workflow=None,
        member_workflows={},
        chosen_by_skill=False,
        order_ambiguous=False,
        now=NOW,
    )
    assert code == 0
    assert document["needs_selection"] == [101]
    assert document["draft_id"] is None
    assert not (env.artifacts / "_watch" / "run-watched").exists()


def test_plan_exits_2_when_no_member(env: Env) -> None:
    env.provider.issues[101].state = "CLOSED"
    env.provider.issues[102].labels.add("state:done")
    code, document = run_plan(
        PlanDeps(provider=env.provider, artifacts_dir=env.artifacts, repo_root=env.repo),
        [101, 102],
        workflow="dev",
        member_workflows={},
        chosen_by_skill=False,
        order_ambiguous=False,
        now=NOW,
    )
    assert code == 2
    assert document["draft_id"] is None


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------


def test_execute_happy_path_and_idempotent_rerun(env: Env) -> None:
    draft_id = env.plan()
    result = run_execute(env.deps(), draft_id)
    assert result["series_id"] == "rw-101-102-2611150301"
    assert result["commit"] == env.remote_head()
    assert result["series_pane"] == "p_1"
    assert result["watch"] == "watch started (pane p_w)"
    _assert_converged(env, draft_id)

    again = run_execute(env.deps(), draft_id)
    assert again["commit"] == result["commit"]
    _assert_converged(env, draft_id)
    assert env.watch_calls == 2


def test_commit_receipt_is_recovered_after_crash_before_result(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    draft_id = env.plan()
    original = Git.out

    def crash_after_commit(self: Git, *args: str) -> str:
        out = original(self, *args)
        if args[0] == "commit":
            raise RuntimeError("injected: after commit, before result")
        return out

    monkeypatch.setattr(Git, "out", crash_after_commit)
    with pytest.raises(RuntimeError, match="after commit"):
        run_execute(env.deps(), draft_id)
    monkeypatch.setattr(Git, "out", original)
    receipts = load_draft(env.artifacts, draft_id).receipts
    assert receipts.commit_intent is not None and receipts.commit is None
    assert env.remote_log() == ["init"]

    run_execute(env.deps(), draft_id)
    _assert_converged(env, draft_id)
    assert load_draft(env.artifacts, draft_id).receipts.commit is not None


def test_commit_is_retried_once_after_crash_between_intent_and_commit(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    draft_id = env.plan()
    original = Git.out

    def crash_before_add(self: Git, *args: str) -> str:
        if args[0] == "add":
            raise RuntimeError("injected: after intent, before commit")
        return original(self, *args)

    monkeypatch.setattr(Git, "out", crash_before_add)
    with pytest.raises(RuntimeError, match="before commit"):
        run_execute(env.deps(), draft_id)
    monkeypatch.setattr(Git, "out", original)
    assert load_draft(env.artifacts, draft_id).receipts.commit_intent is not None

    run_execute(env.deps(), draft_id)
    _assert_converged(env, draft_id)


def _crash_after_intent(env: Env, monkeypatch: pytest.MonkeyPatch, draft_id: str) -> None:
    original = Git.out

    def crash_before_add(self: Git, *args: str) -> str:
        if args[0] == "add":
            raise RuntimeError("injected")
        return original(self, *args)

    monkeypatch.setattr(Git, "out", crash_before_add)
    with pytest.raises(RuntimeError):
        run_execute(env.deps(), draft_id)
    monkeypatch.setattr(Git, "out", original)


@pytest.mark.parametrize("variant", ["unrelated", "extra_file", "other_blob", "other_message"])
def test_foreign_head_commit_is_rejected_and_not_pushed(
    env: Env, monkeypatch: pytest.MonkeyPatch, variant: str
) -> None:
    draft_id = env.plan()
    _crash_after_intent(env, monkeypatch, draft_id)
    yaml_path = load_draft(env.artifacts, draft_id).yaml_path
    message = "chore(series): add rw-101-102-2611150301 for #101, #102"
    if variant == "unrelated":
        (env.repo / "other.txt").write_text("x\n", encoding="utf-8")
        _git(env.repo, "add", "other.txt")
    elif variant == "extra_file":
        (env.repo / "other.txt").write_text("x\n", encoding="utf-8")
        _git(env.repo, "add", "other.txt", yaml_path)
    elif variant == "other_blob":
        (env.repo / yaml_path).write_text("id: changed\n", encoding="utf-8")
        _git(env.repo, "add", yaml_path)
    else:
        _git(env.repo, "add", yaml_path)
        message = "chore: something else"
    _git(env.repo, "commit", "-m", message)
    local_head = _git(env.repo, "rev-parse", "HEAD")

    with pytest.raises(RunWatchedError) as exc:
        run_execute(env.deps(), draft_id)
    assert exc.value.exit_code == 2
    assert env.remote_log() == ["init"]
    assert _git(env.repo, "rev-parse", "HEAD") == local_head
    assert env.provider.add_calls == []
    assert env.herdr.created == []


@pytest.mark.parametrize("stage", ["yaml", "push", "label", "tab", "pane_run"])
def test_interrupted_stage_resumes_without_duplicates(
    env: Env, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    draft_id = env.plan()
    if stage == "yaml":
        original_gen = run_watched.generate_series_yaml

        def crash_after_yaml(*args: Any, **kwargs: Any) -> Path:
            original_gen(*args, **kwargs)
            raise RuntimeError("injected: after yaml")

        monkeypatch.setattr(run_watched, "generate_series_yaml", crash_after_yaml)
    elif stage == "push":
        original_run = Git.run

        def crash_after_push(self: Git, *args: str, check: bool = True) -> Any:
            result = original_run(self, *args, check=check)
            if args[0] == "push":
                raise RuntimeError("injected: after push")
            return result

        monkeypatch.setattr(Git, "run", crash_after_push)
    elif stage == "label":
        env.provider.fail_after_edit = True
    elif stage == "tab":
        env.herdr.fail_after_create = True
    else:
        env.herdr.fail_after_run = True

    with pytest.raises(RuntimeError, match="injected"):
        run_execute(env.deps(), draft_id)
    monkeypatch.undo()

    run_execute(env.deps(), draft_id)
    _assert_converged(env, draft_id)


def test_push_failure_retries_push_when_remote_unchanged(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    draft_id = env.plan()
    original_run = Git.run

    def failing_push(self: Git, *args: str, check: bool = True) -> Any:
        if args[0] == "push":
            return subprocess.CompletedProcess(args, 1, "", "rejected")
        return original_run(self, *args, check=check)

    monkeypatch.setattr(Git, "run", failing_push)
    with pytest.raises(RunWatchedError) as exc:
        run_execute(env.deps(), draft_id)
    assert exc.value.exit_code == 3
    monkeypatch.undo()
    assert env.remote_log() == ["init"]

    run_execute(env.deps(), draft_id)
    _assert_converged(env, draft_id)


def test_push_stops_with_exit_3_when_remote_moved(
    env: Env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    draft_id = env.plan()
    original_run = Git.run

    def failing_push(self: Git, *args: str, check: bool = True) -> Any:
        if args[0] == "push":
            return subprocess.CompletedProcess(args, 1, "", "rejected")
        return original_run(self, *args, check=check)

    monkeypatch.setattr(Git, "run", failing_push)
    with pytest.raises(RunWatchedError):
        run_execute(env.deps(), draft_id)
    monkeypatch.undo()

    other = tmp_path / "other"
    _git(tmp_path, "clone", str(env.remote), str(other))
    _git(other, "config", "user.name", "o")
    _git(other, "config", "user.email", "o@example.invalid")
    (other / "x.txt").write_text("x\n", encoding="utf-8")
    _git(other, "add", "x.txt")
    _git(other, "commit", "-m", "someone else")
    _git(other, "push", "origin", "main")
    moved = env.remote_head()

    with pytest.raises(RunWatchedError) as exc:
        run_execute(env.deps(), draft_id)
    assert exc.value.exit_code == 3
    assert env.remote_head() == moved
    assert env.provider.add_calls == []


@pytest.mark.parametrize("change", ["closed", "hold", "wf_added", "wf_switched"])
def test_recheck_stops_before_side_effects_on_external_change(env: Env, change: str) -> None:
    if change == "wf_switched":
        env.provider.issues[102].labels.add("wf:dev")
    draft_id = env.plan()
    if change == "closed":
        env.provider.issues[102].state = "CLOSED"
    elif change == "hold":
        env.provider.issues[102].labels.add("state:hold")
    elif change == "wf_added":
        # plan --workflow dev の後に wf:docs が付いた: 古い workflow で起動しない
        env.provider.issues[101].labels.add("wf:docs")
    else:
        env.provider.issues[102].labels.discard("wf:dev")
        env.provider.issues[102].labels.add("wf:docs")
    with pytest.raises(RunWatchedError) as exc:
        run_execute(env.deps(), draft_id)
    assert exc.value.exit_code == 2
    assert not _yaml(env, draft_id).exists()
    assert env.remote_log() == ["init"]
    assert env.provider.add_calls == []


def test_recheck_between_stages_stops_before_labels(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    draft_id = env.plan()
    original_push = run_watched._Executor.push

    def push_then_hold(self: Any, sha: str) -> None:
        original_push(self, sha)
        env.provider.issues[101].labels.add("state:hold")

    monkeypatch.setattr(run_watched._Executor, "push", push_then_hold)
    with pytest.raises(RunWatchedError) as exc:
        run_execute(env.deps(), draft_id)
    assert exc.value.exit_code == 2
    assert env.provider.add_calls == []
    assert env.herdr.created == []


def test_recheck_between_stages_detects_wf_label_change(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    draft_id = env.plan()
    original_push = run_watched._Executor.push

    def push_then_relabel(self: Any, sha: str) -> None:
        original_push(self, sha)
        env.provider.issues[101].labels.add("wf:docs")

    monkeypatch.setattr(run_watched._Executor, "push", push_then_relabel)
    with pytest.raises(RunWatchedError) as exc:
        run_execute(env.deps(), draft_id)
    assert exc.value.exit_code == 2
    assert "wf" in str(exc.value)
    assert env.provider.add_calls == []
    assert env.herdr.created == []


def test_rerun_reuses_own_tab_when_pane_list_lacks_pane(env: Env) -> None:
    # Herdr の一覧から pane を解決できなくても、自分の receipt でタブを再利用する
    draft_id = env.plan()
    env.herdr.fail_after_create = True
    with pytest.raises(RuntimeError, match="after tab create"):
        run_execute(env.deps(), draft_id)
    # 応答を失ったので receipt はない。タブは pane 不明で残る → 人手の解決（exit 3）
    env.herdr.tabs = [TabRef(t.tab_id, None, t.label) for t in env.herdr.tabs]
    with pytest.raises(RunWatchedError) as exc:
        run_execute(env.deps(), draft_id)
    assert exc.value.exit_code == 3
    assert env.herdr.created == ["kaji-series-rw-101-102-2611150301"]
    assert env.herdr.runs == []


def test_rerun_after_pane_run_failure_uses_receipt_pane(env: Env) -> None:
    draft_id = env.plan()
    env.herdr.fail_after_run = True
    with pytest.raises(RuntimeError, match="after pane run"):
        run_execute(env.deps(), draft_id)
    env.herdr.tabs = [TabRef(t.tab_id, None, t.label) for t in env.herdr.tabs]
    result = run_execute(env.deps(), draft_id)
    assert result["series_pane"] == "p_1"
    assert env.herdr.created == ["kaji-series-rw-101-102-2611150301"]
    assert len(env.herdr.runs) == 1


def test_existing_yaml_with_other_content_is_not_overwritten(env: Env) -> None:
    draft_id = env.plan()
    path = _yaml(env, draft_id)
    path.parent.mkdir(parents=True)
    path.write_text("id: someone-else\n", encoding="utf-8")
    with pytest.raises(RunWatchedError) as exc:
        run_execute(env.deps(), draft_id)
    assert exc.value.exit_code == 2
    assert path.read_text(encoding="utf-8") == "id: someone-else\n"
    assert env.remote_log() == ["init"]


def test_unrelated_ahead_commit_is_rejected_at_stage_0(env: Env) -> None:
    draft_id = env.plan()
    (env.repo / "local.txt").write_text("x\n", encoding="utf-8")
    _git(env.repo, "add", "local.txt")
    _git(env.repo, "commit", "-m", "local work")
    with pytest.raises(RunWatchedError) as exc:
        run_execute(env.deps(), draft_id)
    assert exc.value.exit_code == 2
    assert not _yaml(env, draft_id).exists()
    assert env.remote_log() == ["init"]
    assert env.provider.add_calls == []


def test_validation_failure_changes_no_labels_or_tabs(env: Env) -> None:
    draft_id = env.plan()
    env.validate_rc = 1
    with pytest.raises(RunWatchedError) as exc:
        run_execute(env.deps(), draft_id)
    assert exc.value.exit_code == 2
    assert env.remote_log() == ["init"]
    assert env.provider.add_calls == []
    assert env.herdr.created == [] and env.herdr.runs == []


def test_precondition_failure_leaves_no_side_effects(env: Env) -> None:
    draft_id = env.plan()
    env.preflight_error = WatchPreconditionError("herdr", "outside Herdr")
    with pytest.raises(WatchPreconditionError):
        run_execute(env.deps(), draft_id)
    assert not _yaml(env, draft_id).exists()
    assert _git(env.repo, "log", "--format=%s") == "init"
    assert env.remote_log() == ["init"]
    assert env.provider.add_calls == []
    assert env.herdr.created == [] and env.watch_calls == 0


def test_execute_rejects_worktree_checkout(env: Env, tmp_path: Path) -> None:
    draft_id = env.plan()
    worktree = tmp_path / "wt"
    _git(env.repo, "worktree", "add", "-b", "side", str(worktree))
    deps = env.deps()
    deps.repo_root = worktree
    with pytest.raises(RunWatchedError) as exc:
        run_execute(deps, draft_id)
    assert exc.value.exit_code == 2
    assert env.remote_log() == ["init"]


# ---------------------------------------------------------------------------
# CLI の exit code（M7）
# ---------------------------------------------------------------------------


def test_cli_execute_without_watch_declaration_exits_2(
    outside_project_tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.runstate_helpers import write_config

    write_config(outside_project_tmp_path)
    code = run_watched.main(
        ["--workdir", str(outside_project_tmp_path), "execute", "--draft", "d-x"]
    )
    assert code == 2
    assert "[watch] is not declared" in capsys.readouterr().err
    assert not (outside_project_tmp_path / ".kaji" / "series").exists()


def test_cli_rejects_invalid_issue_list(outside_project_tmp_path: Path) -> None:
    from tests.runstate_helpers import write_config

    write_config(outside_project_tmp_path)
    code = run_watched.main(["--workdir", str(outside_project_tmp_path), "plan", "--issues", "1,1"])
    assert code == 2


def test_plan_twice_in_same_second_does_not_overwrite_draft(env: Env) -> None:
    first = env.plan()
    second = env.plan()
    assert first != second
    assert load_draft(env.artifacts, first).draft_id == first
    assert load_draft(env.artifacts, second).draft_id == second
