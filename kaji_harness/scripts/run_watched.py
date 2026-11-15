"""Deterministic helpers for ``/run-watched`` (Issue #543 A2・A3).

``plan`` は読み取り・分類・承認の要否の計算・draft の保存を行い、GitHub には書かない。
``execute`` は draft の段（前提の検証 → series YAML → 検証 → commit / push → ``state:planned``
→ series のタブと起動 → 監視の起動）を順に実行する。各段は外部の結果との照合で済んだかを
判定し、receipt を副作用の直前（intent）と直後（result）に draft へ保存する（M3・M8）。

CLI::

    python -m kaji_harness.scripts.run_watched plan --issues 101,102 [--workflow dev]
    python -m kaji_harness.scripts.run_watched execute --draft <draft-id>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from ..artifacts import resolve_artifacts_dir
from ..config import KajiConfig
from ..errors import (
    ConfigLoadError,
    ConfigNotFoundError,
    HarnessError,
    RunWatchedError,
    WatchPreconditionError,
)
from ..providers import Issue
from ..runstate.query import collect_status, list_series
from ..series.generator import generate_series_yaml
from ..series.models import SeriesConfig, SeriesMember
from ..watch.herdr import SERIES_TAB_PREFIX, CliHerdrClient, HerdrClient, find_tab, foreground_has
from ..watch.preflight import check_watch_preconditions

ExclusionReason = Literal[
    "closed",
    "state_label",
    "state_conflict",
    "workflow_mismatch",
    "wf_unresolvable",
    "wf_ambiguous",
    "run_alive_or_unknown",
    "worktree_or_pr",
]
_BLOCKING_STATE_LABELS = frozenset(
    {
        "state:planned",
        "state:running",
        "state:aborted",
        "state:done",
        "state:hold",
        "state:needs-maintainer",
    }
)


class WorkflowResolutionError(ValueError):
    """workflow を一意に解決できない。

    Attributes:
        reason: ``unresolvable`` / ``ambiguous``。
    """

    def __init__(self, reason: Literal["unresolvable", "ambiguous"], message: str) -> None:
        self.reason = reason
        super().__init__(message)


def _workflow_files(wf_root: Path) -> list[Path]:
    return sorted(path for path in wf_root.rglob("*.yaml") if path.is_file())


def resolve_workflow_arg(value: str, repo_root: Path) -> Path:
    """引数（stem・ファイル名・パス）を workflow YAML に解決する。

    Raises:
        WorkflowResolutionError: 0 個（unresolvable）または 2 個以上（ambiguous）に一致する場合。
    """
    candidate = (repo_root / value).resolve()
    if ("/" in value or value.endswith(".yaml")) and candidate.is_file():
        return candidate
    stem = Path(value).name.removesuffix(".yaml")
    matches = [p for p in _workflow_files(repo_root / ".kaji" / "wf") if p.stem == stem]
    if not matches:
        raise WorkflowResolutionError("unresolvable", f"workflow not found: {value}")
    if len(matches) > 1:
        raise WorkflowResolutionError("ambiguous", f"workflow is ambiguous: {value}")
    return matches[0]


def resolve_wf_label(name: str, repo_root: Path) -> Path:
    """``wf:<name>`` を ``name:`` が一致する workflow YAML（ちょうど 1 個）に解決する。

    Raises:
        WorkflowResolutionError: 0 個（unresolvable）または 2 個以上（ambiguous）の場合。
    """
    matches: list[Path] = []
    for path in _workflow_files(repo_root / ".kaji" / "wf"):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(data, dict) and data.get("name") == name:
            matches.append(path)
    if not matches:
        raise WorkflowResolutionError("unresolvable", f"wf:{name} matches no workflow")
    if len(matches) > 1:
        raise WorkflowResolutionError("ambiguous", f"wf:{name} matches several workflows")
    return matches[0]


def make_series_id(issues: list[int], now: datetime) -> str:
    """``rw-<最初の issue>-<最後の issue>-<YYMMDDHHMM>`` を返す。"""
    return f"rw-{issues[0]}-{issues[-1]}-{now.strftime('%y%m%d%H%M')}"


@dataclass(frozen=True)
class IssueFacts:
    """``plan`` の分類に使う 1 Issue の事実。

    Attributes:
        issue: Issue 番号。
        state: ``open`` / ``closed``。
        labels: 付いているラベル。
        any_run_alive: #540 の値（``None`` は判定不能）。
        launching: #540 の値（``None`` は判定不能）。
        has_worktree_or_pr: worktree または open の PR が残っている。
    """

    issue: int
    state: str
    labels: tuple[str, ...]
    any_run_alive: bool | None
    launching: bool | None
    has_worktree_or_pr: bool


def classify(
    facts: IssueFacts, *, workflow_arg: Path | None, wf_label_path: Path | None
) -> ExclusionReason | None:
    """除外の理由（なければ ``None``）を返す。

    Args:
        facts: Issue の事実。
        workflow_arg: 引数で指定された workflow（解決済み）。
        wf_label_path: ``wf:*`` ラベルから解決した workflow（ラベルがなければ ``None``）。
    """
    if facts.state.lower() != "open":
        return "closed"
    state_labels = [label for label in facts.labels if label.startswith("state:")]
    if len(state_labels) >= 2:
        return "state_conflict"
    if any(label in _BLOCKING_STATE_LABELS for label in state_labels):
        return "state_label"
    if workflow_arg is not None and wf_label_path is not None and workflow_arg != wf_label_path:
        return "workflow_mismatch"
    if facts.any_run_alive is not False or facts.launching is not False:
        return "run_alive_or_unknown"
    if facts.has_worktree_or_pr:
        return "worktree_or_pr"
    return None


def approval_required(*, chosen_by_skill: bool, order_ambiguous: bool, excluded: int) -> bool:
    """承認が要るか（skill が workflow を選んだ・順番が曖昧・除外が 1 件以上）。"""
    return chosen_by_skill or order_ambiguous or excluded > 0


# ---------------------------------------------------------------------------
# draft（Pydantic で検証する。LLM は手で書かない）
# ---------------------------------------------------------------------------

JsonDict = dict[str, Any]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DraftMember(_Strict):
    """series に入る 1 Issue（workflow は repo 相対パス）。"""

    issue: int
    workflow: str


class DraftExclusion(_Strict):
    """除外した Issue と理由。"""

    issue: int
    reason: str


class IssueSnapshot(_Strict):
    """``plan`` 時点の Issue の状態（段 1 の期待状態の基準）。"""

    state: str
    labels: list[str]


class CommitIntent(_Strict):
    """commit の前に保存する照合情報（``is_own_series_commit`` の入力）。"""

    parent: str
    yaml_path: str
    yaml_blob: str
    message: str


class CommitResult(_Strict):
    """commit の結果。"""

    sha: str
    parent: str


class TabReceipt(_Strict):
    """series のタブ。"""

    tab_id: str
    pane_id: str


class Receipts(_Strict):
    """各段の receipt（識別子と期待状態）。"""

    yaml_sha256: str | None = None
    commit_intent: CommitIntent | None = None
    commit: CommitResult | None = None
    pushed: bool = False
    label_intent: list[int] = []
    labels_done: list[int] = []
    tab: TabReceipt | None = None
    launch_intent: bool = False
    series_pane: str | None = None
    watch: str | None = None


class Draft(_Strict):
    """``<artifacts>/_watch/run-watched/<draft-id>.json`` の内容（schema v1）。"""

    schema_version: Literal[1] = 1
    draft_id: str
    created_at: str
    series_id: str
    yaml_path: str
    members: list[DraftMember]
    excluded: list[DraftExclusion]
    approval_required: bool
    snapshot: dict[str, IssueSnapshot]
    receipts: Receipts = Receipts()


def draft_dir(artifacts_dir: Path) -> Path:
    """draft の保存先 directory。"""
    return artifacts_dir / "_watch" / "run-watched"


def save_draft(artifacts_dir: Path, draft: Draft) -> Path:
    """draft を原子的に保存する。"""
    directory = draft_dir(artifacts_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{draft.draft_id}.json"
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(draft.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_draft(artifacts_dir: Path, draft_id: str) -> Draft:
    """draft を読み込む。

    Raises:
        RunWatchedError: 存在しない・不正な draft の場合（exit 2）。
    """
    if "/" in draft_id or draft_id.startswith("."):
        raise RunWatchedError(2, f"invalid draft id: {draft_id}")
    path = draft_dir(artifacts_dir) / f"{draft_id}.json"
    try:
        return Draft.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RunWatchedError(2, f"draft not found: {draft_id}") from exc
    except (OSError, ValidationError) as exc:
        raise RunWatchedError(2, f"draft is invalid: {draft_id}: {exc}") from exc


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


class PlanProvider(Protocol):
    """``plan`` が使う provider の読み取り操作。"""

    def view_issue(self, issue_id: str) -> Issue:
        """Issue を取得する。"""
        ...

    def resolve_issue_context(self, issue_id: str) -> Any:
        """``branch_prefix`` を持つ Issue context。"""
        ...

    def resolve_pr_context(self, branch_name: str) -> Any:
        """open の PR（無ければ ``None``）。"""
        ...


@dataclass(frozen=True)
class PlanDeps:
    """``plan`` の依存。"""

    provider: PlanProvider
    artifacts_dir: Path
    repo_root: Path


#: 段 1 の再検査で snapshot と照合するラベルの接頭辞
_RECHECK_PREFIXES = ("state:", "wf:")


def _labels(issue: Issue) -> tuple[str, ...]:
    return tuple(sorted(label.name for label in issue.labels))


def _has_worktree_or_pr(deps: PlanDeps, issue: int, latest_run: JsonDict | None) -> bool:
    worktree = (latest_run or {}).get("worktree_dir")
    if isinstance(worktree, str) and worktree and Path(worktree).exists():
        return True
    try:
        context = deps.provider.resolve_issue_context(str(issue))
        branch = f"{context.branch_prefix}/{issue}"
        return deps.provider.resolve_pr_context(branch) is not None
    except (HarnessError, OSError, ValueError):
        # 判定できなければ除外する（保守側）
        return True


def gather_facts(deps: PlanDeps, issue: int) -> tuple[IssueFacts, Issue]:
    """Issue の状態・#540 の生存判定・worktree / PR を集める。"""
    view = deps.provider.view_issue(str(issue))
    document = collect_status(deps.artifacts_dir, [str(issue)])
    entry: JsonDict = document["issues"][0] if document.get("issues") else {}
    latest = entry.get("latest_run")
    facts = IssueFacts(
        issue=issue,
        state=view.state,
        labels=_labels(view),
        any_run_alive=entry.get("any_run_alive"),
        launching=entry.get("launching"),
        has_worktree_or_pr=_has_worktree_or_pr(
            deps, issue, latest if isinstance(latest, dict) else None
        ),
    )
    return facts, view


def _relative(path: Path, repo_root: Path) -> str:
    return path.resolve().relative_to(repo_root.resolve()).as_posix()


def _new_draft_id(artifacts_dir: Path, moment: datetime) -> str:
    """既存の draft と衝突しない draft-id（同じ秒の 2 回目以降は ``-2`` 等を付ける）。"""
    base = f"d-{moment.strftime('%y%m%d-%H%M%S')}"
    candidate, n = base, 1
    while (draft_dir(artifacts_dir) / f"{candidate}.json").exists():
        n += 1
        candidate = f"{base}-{n}"
    return candidate


def run_plan(
    deps: PlanDeps,
    issues: Sequence[int],
    *,
    workflow: str | None,
    member_workflows: dict[int, str],
    chosen_by_skill: bool,
    order_ambiguous: bool,
    now: datetime | None = None,
) -> tuple[int, JsonDict]:
    """``plan`` を実行し ``(exit code, 出力 JSON)`` を返す。GitHub には書かない。"""
    moment = now or datetime.now(UTC)
    members: list[DraftMember] = []
    excluded: list[DraftExclusion] = []
    needs_selection: list[int] = []
    snapshot: dict[str, IssueSnapshot] = {}
    for issue in issues:
        facts, view = gather_facts(deps, issue)
        snapshot[str(issue)] = IssueSnapshot(state=view.state, labels=list(facts.labels))
        arg_value = member_workflows.get(issue, workflow)
        arg_path: Path | None = None
        label_path: Path | None = None
        reason: str | None = None
        try:
            if arg_value is not None:
                arg_path = resolve_workflow_arg(arg_value, deps.repo_root)
            wf_labels = [name for name in facts.labels if name.startswith("wf:")]
            if len(wf_labels) > 1:
                raise WorkflowResolutionError("ambiguous", "several wf:* labels")
            if wf_labels:
                label_path = resolve_wf_label(wf_labels[0].removeprefix("wf:"), deps.repo_root)
        except WorkflowResolutionError as exc:
            reason = "wf_ambiguous" if exc.reason == "ambiguous" else "wf_unresolvable"
        if reason is None:
            reason = classify(facts, workflow_arg=arg_path, wf_label_path=label_path)
        if reason is not None:
            excluded.append(DraftExclusion(issue=issue, reason=reason))
            continue
        chosen = arg_path or label_path
        if chosen is None:
            needs_selection.append(issue)
            continue
        members.append(DraftMember(issue=issue, workflow=_relative(chosen, deps.repo_root)))
    document: JsonDict = {
        "draft_id": None,
        "members": [m.model_dump() for m in members],
        "excluded": [e.model_dump() for e in excluded],
        "needs_selection": needs_selection,
        "approval_required": approval_required(
            chosen_by_skill=chosen_by_skill,
            order_ambiguous=order_ambiguous,
            excluded=len(excluded),
        ),
    }
    if needs_selection:
        return 0, document
    if not members:
        document["error"] = "no issue can join the series"
        return 2, document
    series_id = make_series_id([m.issue for m in members], moment)
    draft = Draft(
        draft_id=_new_draft_id(deps.artifacts_dir, moment),
        created_at=moment.isoformat(),
        series_id=series_id,
        yaml_path=f".kaji/series/{series_id}.yaml",
        members=members,
        excluded=excluded,
        approval_required=document["approval_required"],
        snapshot=snapshot,
    )
    save_draft(deps.artifacts_dir, draft)
    document.update(draft_id=draft.draft_id, series_id=series_id, yaml=draft.yaml_path)
    return 0, document


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------


class Git:
    """main checkout に対する git の実行（実物の ``git`` を使う）。"""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root

    def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        """git を実行する。

        Raises:
            RunWatchedError: ``check`` で失敗した場合（exit 2）。
        """
        result = subprocess.run(
            ["git", *args], cwd=self.repo_root, capture_output=True, text=True, check=False
        )
        if check and result.returncode != 0:
            raise RunWatchedError(2, f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result

    def out(self, *args: str) -> str:
        """git を実行し標準出力（前後の空白を除く）を返す。"""
        return self.run(*args).stdout.strip()

    def rev(self, ref: str) -> str:
        """ref の sha。"""
        return self.out("rev-parse", "--verify", ref)

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        """``ancestor`` が ``descendant`` の祖先（同一を含む）か。"""
        return (
            self.run("merge-base", "--is-ancestor", ancestor, descendant, check=False).returncode
            == 0
        )


def is_own_series_commit(git: Git, intent: CommitIntent, sha: str) -> bool:
    """``sha`` が ``intent`` の commit（自分の series YAML の追加 1 件）か（段 0・段 4 で共有）。"""
    parents = git.run("rev-list", "--parents", "-n", "1", sha, check=False).stdout.split()
    if len(parents) != 2 or parents[1] != intent.parent:
        return False
    diff = git.run(
        "diff-tree", "--no-commit-id", "-r", "--name-status", intent.parent, sha, check=False
    ).stdout.strip()
    if diff != f"A\t{intent.yaml_path}":
        return False
    blob = git.run("rev-parse", f"{sha}:{intent.yaml_path}", check=False).stdout.strip()
    if blob != intent.yaml_blob:
        return False
    message = git.run("log", "-1", "--format=%B", sha, check=False).stdout.rstrip("\n")
    return message == intent.message


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------


class ExecuteProvider(Protocol):
    """``execute`` が使う provider の操作。"""

    def view_issue(self, issue_id: str) -> Issue:
        """Issue を取得する。"""
        ...

    def edit_issue(
        self,
        issue_id: str,
        *,
        title: str | None = None,
        body: str | None = None,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> Issue:
        """Issue を更新する。"""
        ...


@dataclass
class ExecuteDeps:
    """``execute`` の依存（テストは fake を注入する）。

    Attributes:
        preflight: 前提の検証（``check_watch_preconditions(require_herdr=True)``）。
        provider: GitHub provider。
        herdr: Herdr の client。
        artifacts_dir: artifacts の root。
        repo_root: main checkout の root。
        git_remote: push 先の remote。
        default_branch: 既定 branch。
        run_command: ``kaji validate-series`` 等を実行し exit code を返す。
        watch_start: ``kaji watch start`` 相当（出力する 1 行を返す）。
    """

    preflight: Callable[[], None]
    provider: ExecuteProvider
    herdr: HerdrClient
    artifacts_dir: Path
    repo_root: Path
    git_remote: str
    default_branch: str
    run_command: Callable[[list[str]], int]
    watch_start: Callable[[], str]


def _yaml_content(draft: Draft) -> str:
    config = SeriesConfig(
        id=draft.series_id,
        strategy="sequential",
        members=[SeriesMember(issue=m.issue, workflow=m.workflow) for m in draft.members],
        on_failure="stop",
    )
    payload = config.model_dump(mode="json", exclude_none=True)
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def commit_message(draft: Draft) -> str:
    """series YAML の commit message（完全な文字列）。"""
    refs = ", ".join(f"#{m.issue}" for m in draft.members)
    return f"chore(series): add {draft.series_id} for {refs}"


class _Executor:
    def __init__(self, deps: ExecuteDeps, draft: Draft) -> None:
        self.deps = deps
        self.draft = draft
        self.git = Git(deps.repo_root)
        self.remote_ref = f"refs/remotes/{deps.git_remote}/{deps.default_branch}"
        self.done: list[str] = []

    @property
    def receipts(self) -> Receipts:
        return self.draft.receipts

    def save(self) -> None:
        save_draft(self.deps.artifacts_dir, self.draft)

    def fail(self, code: int, message: str) -> RunWatchedError:
        done = ", ".join(self.done) or "none"
        return RunWatchedError(code, f"{message} (completed stages: {done})")

    # 段 0
    def check_git(self) -> None:
        git = self.git
        git_dir = Path(git.out("rev-parse", "--absolute-git-dir")).resolve()
        common = Path(git.out("rev-parse", "--git-common-dir"))
        if not common.is_absolute():
            common = (self.deps.repo_root / common).resolve()
        if git_dir != common.resolve():
            raise self.fail(2, "run_watched execute must run in the main checkout (not a worktree)")
        branch = git.run("symbolic-ref", "--short", "HEAD", check=False).stdout.strip()
        if branch != self.deps.default_branch:
            raise self.fail(2, f"current branch must be {self.deps.default_branch}, got {branch!r}")
        staged = [p for p in git.out("diff", "--cached", "--name-only").splitlines() if p]
        if any(path != self.draft.yaml_path for path in staged):
            raise self.fail(2, f"index has staged changes other than the series YAML: {staged}")
        git.out("fetch", self.deps.git_remote, self.deps.default_branch)
        head = git.rev("HEAD")
        remote = git.rev(self.remote_ref)
        if head == remote:
            return
        intent = self.receipts.commit_intent
        if intent is not None and is_own_series_commit(git, intent, head):
            if intent.parent == remote or git.is_ancestor(head, remote):
                return
            # 自分の commit だが remote が先に進んだ: 段 4 と同じく人手の解決を求める
            raise self.fail(
                3,
                f"{self.deps.git_remote}/{self.deps.default_branch} moved; resolve by hand "
                f"(e.g. git reset --soft {intent.parent[:12]}). No rebase or force push was done",
            )
        raise self.fail(
            2,
            f"HEAD {head[:12]} differs from {self.deps.git_remote}/{self.deps.default_branch} "
            f"{remote[:12]} and is not this series commit",
        )

    # 段 6 の照合
    def series_launched(self) -> bool:
        for series in list_series(self.deps.artifacts_dir):
            if series.get("id") != self.draft.series_id:
                continue
            if series.get("status") is not None:
                return True
            for member in series.get("members") or []:
                if member.get("run_id") is not None or member.get("child_pid") is not None:
                    return True
        return False

    # 段 1
    def recheck(self) -> None:
        mine = set(self.receipts.label_intent) | set(self.receipts.labels_done)
        for member in self.draft.members:
            key = str(member.issue)
            snap = self.draft.snapshot[key]
            view = self.deps.provider.view_issue(key)
            if view.state.lower() != "open":
                raise self.fail(2, f"#{member.issue} is no longer open")
            # plan の判断に使った state:* と wf:* を照合する（自分の state:planned だけ許す）
            current = {n for n in _labels(view) if n.startswith(_RECHECK_PREFIXES)}
            expected = {n for n in snap.labels if n.startswith(_RECHECK_PREFIXES)}
            allowed = [expected]
            if member.issue in mine:
                allowed.append(expected | {"state:planned"})
            if current not in allowed:
                raise self.fail(
                    2,
                    f"#{member.issue} state/wf labels changed: {sorted(current)}"
                    f" (expected {sorted(expected)})",
                )
        document = collect_status(
            self.deps.artifacts_dir, [str(m.issue) for m in self.draft.members]
        )
        for entry in document.get("issues", []):
            if entry.get("any_run_alive") is not False or entry.get("launching") is not False:
                raise self.fail(2, f"#{entry.get('issue')} has a live or unknown run")

    # 段 2
    def ensure_yaml(self) -> Path:
        path = self.deps.repo_root / self.draft.yaml_path
        content = _yaml_content(self.draft)
        expected = self.receipts.yaml_sha256 or _sha256(content)
        if path.exists():
            if _sha256(path.read_text(encoding="utf-8")) != expected:
                raise self.fail(2, f"existing series YAML differs; refusing to overwrite: {path}")
        else:
            self.receipts.yaml_sha256 = expected
            self.save()
            generate_series_yaml(
                SeriesConfig.model_validate(yaml.safe_load(content)), path, update=False
            )
        if self.receipts.yaml_sha256 is None:
            self.receipts.yaml_sha256 = expected
            self.save()
        self.done.append("yaml")
        return path

    # 段 3
    def validate(self, path: Path) -> None:
        for argv in (
            ["kaji", "validate-series", str(path)],
            ["kaji", "run-series", str(path), "--dry-run"],
        ):
            if self.deps.run_command(argv) != 0:
                raise self.fail(2, f"{shlex.join(argv)} failed")
        self.done.append("validate")

    # 段 4
    def commit(self) -> str:
        git = self.git
        receipts = self.receipts
        if receipts.commit is None:
            head = git.rev("HEAD")
            remote = git.rev(self.remote_ref)
            intent = receipts.commit_intent
            if intent is not None and is_own_series_commit(git, intent, head):
                receipts.commit = CommitResult(sha=head, parent=intent.parent)
                self.save()
            elif head == remote:
                yaml_path = self.draft.yaml_path
                receipts.commit_intent = CommitIntent(
                    parent=remote,
                    yaml_path=yaml_path,
                    yaml_blob=git.out("hash-object", "--", yaml_path),
                    message=commit_message(self.draft),
                )
                self.save()
                git.out("add", "--", yaml_path)
                staged = [p for p in git.out("diff", "--cached", "--name-only").splitlines() if p]
                if staged != [yaml_path]:
                    raise self.fail(2, f"unexpected staged files: {staged}")
                git.out("commit", "-m", receipts.commit_intent.message)
                receipts.commit = CommitResult(sha=git.rev("HEAD"), parent=remote)
                self.save()
            else:
                raise self.fail(2, "HEAD is neither the remote nor this series commit")
        self.done.append("commit")
        return receipts.commit.sha

    def push(self, sha: str) -> None:
        git = self.git
        commit = self.receipts.commit
        assert commit is not None  # noqa: S101 - commit() の後でだけ呼ぶ
        git.out("fetch", self.deps.git_remote, self.deps.default_branch)
        remote = git.rev(self.remote_ref)
        if not git.is_ancestor(sha, remote):
            if remote != commit.parent:
                raise self.fail(
                    3,
                    f"{self.deps.git_remote}/{self.deps.default_branch} moved; resolve by hand "
                    f"(e.g. git reset --soft {commit.parent[:12]}). No rebase or force push was done",
                )
            if git.rev("HEAD") != sha:
                raise self.fail(2, "HEAD is not the series commit; refusing to push")
            pushed = git.run(
                "push",
                self.deps.git_remote,
                f"{sha}:refs/heads/{self.deps.default_branch}",
                check=False,
            )
            if pushed.returncode != 0:
                raise self.fail(3, f"git push failed: {pushed.stderr.strip()}")
        self.receipts.pushed = True
        self.save()
        self.done.append("push")

    # 段 5
    def label(self, launched: bool) -> None:
        receipts = self.receipts
        for member in self.draft.members:
            if launched and member.issue in receipts.labels_done:
                continue
            key = str(member.issue)
            if "state:planned" not in _labels(self.deps.provider.view_issue(key)):
                if member.issue not in receipts.label_intent:
                    receipts.label_intent.append(member.issue)
                    self.save()
                self.deps.provider.edit_issue(key, add_labels=["state:planned"])
            if member.issue not in receipts.labels_done:
                receipts.labels_done.append(member.issue)
                self.save()
        self.done.append("labels")

    # 段 6
    def launch(self, path: Path) -> None:
        herdr = self.deps.herdr
        label = f"{SERIES_TAB_PREFIX}{self.draft.series_id}"
        command = shlex.join(["kaji", "run-series", self.draft.yaml_path])
        tab = find_tab(herdr.list_tabs(), label)
        pane_id: str | None = None
        tab_pane = tab.pane_id if tab is not None else None
        receipt = self.receipts.tab
        if tab is not None and tab_pane is None and receipt is not None:
            # pane を一覧から解決できないときは、自分が作ったタブの receipt で補う
            tab_pane = receipt.pane_id if receipt.tab_id == tab.tab_id else None
        if tab is not None and tab_pane is None:
            raise self.fail(3, f"cannot resolve the pane of tab {label}")
        if tab_pane is not None:
            foreground, commands = herdr.pane_foreground(tab_pane)
            if foreground_has(commands, "run-series"):
                self.receipts.series_pane = tab_pane
                self.save()
                self.done.append("launch")
                return
            if foreground != "shell_only":
                raise self.fail(2, f"tab {label} is busy with another command")
            pane_id = tab_pane
        if pane_id is None:
            tab_id, pane_id = herdr.create_tab(label, self.deps.repo_root)
            self.receipts.tab = TabReceipt(tab_id=tab_id, pane_id=pane_id)
            self.save()
        self.receipts.launch_intent = True
        self.save()
        herdr.run_in_pane(pane_id, command)
        self.receipts.series_pane = pane_id
        self.save()
        self.done.append("launch")

    def run(self) -> JsonDict:
        self.deps.preflight()
        self.check_git()
        launched = self.series_launched()
        if not launched:
            self.recheck()
        path = self.ensure_yaml()
        self.validate(path)
        if not launched:
            self.recheck()
        sha = self.commit()
        self.push(sha)
        if not launched:
            self.recheck()
        self.label(launched)
        if self.series_launched():
            self.done.append("launch")
        else:
            self.recheck()
            self.launch(path)
        self.receipts.watch = self.deps.watch_start()
        self.save()
        return {
            "series_id": self.draft.series_id,
            "yaml": self.draft.yaml_path,
            "commit": sha,
            "series_pane": self.receipts.series_pane,
            "watch": self.receipts.watch,
        }


def run_execute(deps: ExecuteDeps, draft_id: str) -> JsonDict:
    """``execute`` を実行し、出力 JSON を返す。

    Raises:
        RunWatchedError: 前提違反・相違（exit 2）、解決に人手が要る状況（exit 3）。
        WatchPreconditionError: ``[watch]``・ラベル・Herdr の前提違反（exit 2）。
    """
    draft = load_draft(deps.artifacts_dir, draft_id)
    return _Executor(deps, draft).run()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def create_parser() -> argparse.ArgumentParser:
    """CLI の parser。"""
    parser = argparse.ArgumentParser(description="deterministic steps of /run-watched")
    parser.add_argument("--workdir", default=".", help="repo の作業 directory")
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="classify Issues and save a draft (no GitHub writes)")
    plan.add_argument("--issues", required=True, help="comma separated Issue numbers (in order)")
    plan.add_argument("--workflow", help="workflow stem, file name or path for all members")
    plan.add_argument("--member", action="append", default=[], help="<issue>=<workflow>")
    plan.add_argument("--chosen-by-skill", action="store_true")
    plan.add_argument("--order-ambiguous", action="store_true")
    execute = sub.add_parser("execute", help="run the stages of a saved draft")
    execute.add_argument("--draft", required=True)
    return parser


def _parse_issues(value: str) -> list[int]:
    issues = [int(part.strip().lstrip("#")) for part in value.split(",") if part.strip()]
    if not issues or any(issue <= 0 for issue in issues) or len(set(issues)) != len(issues):
        raise ValueError(f"invalid --issues: {value}")
    return issues


def _parse_members(values: list[str]) -> dict[int, str]:
    result: dict[int, str] = {}
    for value in values:
        issue, sep, workflow = value.partition("=")
        if not sep or not workflow:
            raise ValueError(f"invalid --member: {value}")
        result[int(issue.strip().lstrip("#"))] = workflow.strip()
    return result


def _run_command(argv: list[str]) -> int:
    return subprocess.run(argv, check=False).returncode


def main(argv: list[str] | None = None) -> int:
    """CLI の entrypoint（0 = 成功、2 = 前提違反・相違、3 = 人手での解決・予期しない例外）。"""
    args = create_parser().parse_args(argv)
    try:
        config = KajiConfig.discover(start_dir=Path(args.workdir).resolve())
        artifacts_dir = resolve_artifacts_dir(config)
        if args.command == "plan":
            from ..providers import get_provider

            code, document = run_plan(
                PlanDeps(
                    provider=get_provider(config),
                    artifacts_dir=artifacts_dir,
                    repo_root=config.repo_root,
                ),
                _parse_issues(args.issues),
                workflow=args.workflow,
                member_workflows=_parse_members(args.member),
                chosen_by_skill=args.chosen_by_skill,
                order_ambiguous=args.order_ambiguous,
            )
            print(json.dumps(document, ensure_ascii=False))
            return code
        context = check_watch_preconditions(config, require_herdr=True)
        herdr = CliHerdrClient(context.herdr_bin or "herdr")
        github = config.provider.github if config.provider is not None else None

        def _watch_start() -> str:
            from ..watch import service

            return service.start(context, herdr)

        deps = ExecuteDeps(
            preflight=lambda: None,
            provider=context.provider,
            herdr=herdr,
            artifacts_dir=artifacts_dir,
            repo_root=config.repo_root,
            git_remote=github.git_remote if github is not None else "origin",
            default_branch=github.default_branch if github is not None else "main",
            run_command=_run_command,
            watch_start=_watch_start,
        )
        print(json.dumps(run_execute(deps, args.draft), ensure_ascii=False))
        return 0
    except (ConfigNotFoundError, ConfigLoadError, WatchPreconditionError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except RunWatchedError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return exc.exit_code
    except Exception as exc:  # noqa: BLE001 - 予期しない例外は exit 3
        print(f"Error: unexpected failure: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
