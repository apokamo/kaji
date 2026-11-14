"""Deterministic worktree setup for the ``issue-start`` skill (Issue #569).

``start`` step が agent に複数の Bash block を実行させていたため、block 間でシェル変数が
失われ（#560）、agent 手書きの verdict が崩れると副作用完了後に ERROR になった（#551）。
本 module は context 取得から報告コメント・verdict 生成までを 1 プロセスで行う。

正常時の順序（現行 SKILL.md と同じ副作用・順序）:

0. 事前検証（書き込みなし）: context 取得、env 照合、既存 start PASS 報告の分類
1. ``git fetch <remote> <base>`` と ``FETCH_HEAD`` / ``refs/remotes/<remote>/<base>`` の一致確認
2. ``git worktree add --no-track -b <branch> <worktree_dir> <remote>/<base>``（既存の一致する
   worktree は検証のみで再利用し、Git に書き込まない）
3. ``.venv`` symlink、4. main に overlay がある場合のみ ``.kaji/config.local.toml`` symlink
5. ``git worktree list --porcelain`` で登録を再確認
6. Issue 本文先頭の NOTE（同一 NOTE が既にあれば省略）
7. ``step=start status=PASS`` marker 付き報告コメント（同一報告が既にあれば省略）

想定内の失敗はすべて ABORT outcome（値）として返し、副作用はそれ以上進めない。
reset / checkout / clean / branch 削除 / worktree 削除は一切行わない。
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal, TypeVar

from kaji_harness.errors import HarnessError
from kaji_harness.models import Verdict
from kaji_harness.providers import Comment, IssueContext, IssueProvider
from kaji_harness.providers.context import build_worktree_note_body
from kaji_harness.providers.github import GitHubProviderError
from kaji_harness.providers.markers import build_kaji_verdict_marker, parse_kaji_verdict_marker
from kaji_harness.verdict import (
    dump_verdict_yaml,
    normalize_verdict_fields,
    validate_verdict_fields,
)

START_STEP_ID = "start"
VALID_STATUSES = frozenset({"PASS", "ABORT"})
BRANCH_LABEL = "ブランチ"
DIRECTORY_LABEL = "ディレクトリ"

# env 名 -> IssueContext の属性名。runner が注入した値と provider context の一致を検査する。
_ENV_CONTEXT_FIELDS: tuple[tuple[str, str], ...] = (
    ("KAJI_WORKTREE_DIR", "worktree_dir"),
    ("KAJI_BRANCH_NAME", "branch_name"),
    ("KAJI_GIT_REMOTE", "git_remote"),
    ("KAJI_DEFAULT_BRANCH", "default_branch"),
)
_TABLE_ROW_RE = re.compile(r"^\|(?P<label>[^|]*)\|(?P<value>[^|]*)\|\s*$")
_STDERR_TAIL_LINES = 5

OverlayStatus = Literal["created", "existing", "absent"]
_T = TypeVar("_T")


# ============================================================
# 報告コメントの同一性
# ============================================================


@dataclass(frozen=True)
class ReportIdentity:
    """start 報告の同一性キー（報告本文の表の ``ブランチ`` / ``ディレクトリ`` 行の値）。

    Attributes:
        branch: ``<branch_prefix>/<issue_id>``。
        directory: ``../<worktree_dir の basename>``。
    """

    branch: str
    directory: str

    @classmethod
    def from_context(cls, ctx: IssueContext) -> ReportIdentity:
        """provider context から同一性キーを導出する。"""
        return cls(branch=ctx.branch_name, directory=f"../{Path(ctx.worktree_dir).name}")


class ReportMatch(Enum):
    """start PASS 報告 1 件の分類。"""

    SAME = "same"
    OTHER = "other"
    UNIDENTIFIABLE = "unidentifiable"


class ReportDecision(Enum):
    """Issue 全体の既存 start PASS 報告から決まる報告コメントの扱い。"""

    POST = "post"
    SKIP = "skip"
    ABORT = "abort"


@dataclass(frozen=True)
class ExistingReports:
    """既存 start PASS 報告の分類結果。

    Attributes:
        decision: 報告コメントを投稿するか、省略するか、書き込み前に ABORT するか。
        unidentifiable: 同一性を判定できなかった報告コメント。
    """

    decision: ReportDecision
    unidentifiable: tuple[Comment, ...] = ()


def _table_row(label: str, value: str) -> str:
    return f"| {label} | {value} |"


def _normalize_cell(value: str) -> str:
    """セル値の前後空白と、値全体を囲む 1 組の backtick を除く。"""
    stripped = value.strip()
    if len(stripped) >= 2 and stripped.startswith("`") and stripped.endswith("`"):
        stripped = stripped[1:-1].strip()
    return stripped


def parse_report_identity(body: str) -> ReportIdentity | None:
    """報告本文の表から同一性キーを取り出す。

    Args:
        body: Issue コメント本文。

    Returns:
        ``ブランチ`` / ``ディレクトリ`` 行がそれぞれ 1 つの値に定まる場合はそのキー。
        どちらかの行が無い、または同じラベルの行が異なる値で複数ある場合は ``None``。
    """
    values: dict[str, set[str]] = {BRANCH_LABEL: set(), DIRECTORY_LABEL: set()}
    for line in body.splitlines():
        match = _TABLE_ROW_RE.match(line.strip())
        if match is None:
            continue
        label = match.group("label").strip()
        if label in values:
            values[label].add(_normalize_cell(match.group("value")))
    branches = values[BRANCH_LABEL]
    directories = values[DIRECTORY_LABEL]
    if len(branches) != 1 or len(directories) != 1:
        return None
    return ReportIdentity(branch=next(iter(branches)), directory=next(iter(directories)))


def classify_start_report(body: str, identity: ReportIdentity) -> ReportMatch | None:
    """コメント 1 件を start PASS 報告として分類する。

    Args:
        body: Issue コメント本文。
        identity: 今回の start の同一性キー。

    Returns:
        1 行目が ``step=start status=PASS`` の marker（metadata 不問）でなければ ``None``。
        それ以外は SAME / OTHER / UNIDENTIFIABLE。
    """
    lines = body.splitlines()
    if not lines:
        return None
    marker = parse_kaji_verdict_marker(lines[0])
    if marker is None or marker.step != START_STEP_ID or marker.status != "PASS":
        return None
    found = parse_report_identity(body)
    if found is None:
        return ReportMatch.UNIDENTIFIABLE
    return ReportMatch.SAME if found == identity else ReportMatch.OTHER


def classify_start_reports(
    comments: Sequence[Comment], identity: ReportIdentity
) -> ExistingReports:
    """Issue 全体の既存 start PASS 報告から報告コメントの扱いを決める。

    SAME が 1 件以上あれば投稿しない。SAME が無く UNIDENTIFIABLE があれば、重複しうるため
    書き込み前に ABORT する。それ以外（候補なし / OTHER のみ）は投稿する。

    Args:
        comments: Issue の全コメント（投稿順）。
        identity: 今回の start の同一性キー。

    Returns:
        判定結果と、識別不能だったコメント。
    """
    matches = [(comment, classify_start_report(comment.body, identity)) for comment in comments]
    unidentifiable = tuple(c for c, m in matches if m is ReportMatch.UNIDENTIFIABLE)
    if any(m is ReportMatch.SAME for _, m in matches):
        return ExistingReports(ReportDecision.SKIP, unidentifiable)
    if unidentifiable:
        return ExistingReports(ReportDecision.ABORT, unidentifiable)
    return ExistingReports(ReportDecision.POST)


# ============================================================
# worktree / symlink / NOTE の判定
# ============================================================


@dataclass(frozen=True)
class WorktreeEntry:
    """``git worktree list --porcelain`` の 1 entry。

    Attributes:
        path: worktree のパス（呼び出し側で realpath 化して比較する）。
        branch: checkout 中の完全 ref（``refs/heads/...``）。detached / bare なら ``None``。
        prunable: git が prunable（ディレクトリ不在）と報告しているか。
    """

    path: str
    branch: str | None
    prunable: bool


def parse_worktree_porcelain(text: str) -> list[WorktreeEntry]:
    """``git worktree list --porcelain`` の出力を entry の列に変換する。"""
    entries: list[WorktreeEntry] = []
    for chunk in text.strip().split("\n\n"):
        path: str | None = None
        branch: str | None = None
        prunable = False
        for line in chunk.splitlines():
            key, _, value = line.partition(" ")
            if key == "worktree":
                path = value
            elif key == "branch":
                branch = value
            elif key == "prunable":
                prunable = True
        if path is not None:
            entries.append(WorktreeEntry(path=path, branch=branch, prunable=prunable))
    return entries


class WorktreeState(Enum):
    """期待パス / branch に対する worktree の状態。"""

    NEW = "new"
    REUSABLE = "reusable"
    PATH_ON_OTHER_BRANCH = "path_on_other_branch"
    BRANCH_IN_OTHER_PATH = "branch_in_other_path"
    BRANCH_WITHOUT_WORKTREE = "branch_without_worktree"
    PATH_EXISTS_UNREGISTERED = "path_exists_unregistered"
    REGISTERED_PATH_MISSING = "registered_path_missing"


_WORKTREE_ABORT_REASONS: dict[WorktreeState, str] = {
    WorktreeState.PATH_ON_OTHER_BRANCH: "worktree_dir は別 branch（または detached）で登録済み",
    WorktreeState.BRANCH_IN_OTHER_PATH: "branch は別パスの worktree で checkout 済み",
    WorktreeState.BRANCH_WITHOUT_WORKTREE: (
        "branch は存在するが worktree 未登録（既存 branch を自動で attach しない）"
    ),
    WorktreeState.PATH_EXISTS_UNREGISTERED: "worktree_dir は未登録だがパスが既に存在する",
    WorktreeState.REGISTERED_PATH_MISSING: "worktree_dir は登録済みだがディレクトリが無い（prunable）",
}


def classify_worktree(
    entries: Sequence[WorktreeEntry],
    *,
    worktree_dir: str,
    branch: str,
    branch_exists: bool,
    path_exists: bool,
    path_is_dir: bool,
) -> WorktreeState:
    """期待する worktree パス / branch の登録状態を判定する（再利用の判定表）。

    Args:
        entries: realpath 化済みの worktree 一覧。
        worktree_dir: realpath 化済みの期待パス。
        branch: 期待 branch 名（``fix/569`` 等）。
        branch_exists: ``refs/heads/<branch>`` が存在するか。
        path_exists: 期待パスに何か（ファイル・dangling symlink を含む）が存在するか。
        path_is_dir: 期待パスがディレクトリか。

    Returns:
        判定した状態。NEW / REUSABLE 以外は ABORT 対象。
    """
    ref = f"refs/heads/{branch}"
    at_path = [entry for entry in entries if entry.path == worktree_dir]
    if at_path:
        entry = at_path[0]
        if entry.branch != ref:
            return WorktreeState.PATH_ON_OTHER_BRANCH
        if entry.prunable or not path_is_dir:
            return WorktreeState.REGISTERED_PATH_MISSING
        return WorktreeState.REUSABLE
    if any(entry.branch == ref for entry in entries):
        return WorktreeState.BRANCH_IN_OTHER_PATH
    if branch_exists:
        return WorktreeState.BRANCH_WITHOUT_WORKTREE
    if path_exists:
        return WorktreeState.PATH_EXISTS_UNREGISTERED
    return WorktreeState.NEW


class SymlinkState(Enum):
    """symlink の配置先の状態。"""

    MISSING = "missing"
    MATCHES = "matches"
    CONFLICT = "conflict"


def classify_symlink(observed_target: str | None, *, exists: bool, expected: str) -> SymlinkState:
    """symlink 配置先の観測結果を判定する。

    Args:
        observed_target: symlink なら ``os.readlink`` の値、symlink でなければ ``None``。
        exists: 配置先に何か（実ファイル・ディレクトリ・symlink）が存在するか。
        expected: 期待する参照先（絶対パス）。

    Returns:
        不在なら MISSING、期待どおりの symlink なら MATCHES、それ以外は CONFLICT。
    """
    if not exists:
        return SymlinkState.MISSING
    if observed_target == expected:
        return SymlinkState.MATCHES
    return SymlinkState.CONFLICT


def has_worktree_note(body: str, *, worktree: str, branch: str) -> bool:
    """Issue 本文の先頭に同一の worktree NOTE があるかを返す。

    Args:
        body: Issue 本文。改行は ``\\r\\n`` でもよい。
        worktree: worktree basename。
        branch: branch 名。

    Returns:
        ``build_worktree_note_body`` が生成する NOTE（3 行）が本文先頭にある場合 True。
    """
    note = build_worktree_note_body("", worktree=worktree, branch=branch).rstrip("\n")
    normalized = body.replace("\r\n", "\n").lstrip("\n")
    if not normalized.startswith(note):
        return False
    rest = normalized[len(note) :]
    return rest == "" or rest.startswith("\n")


def find_env_mismatches(ctx: IssueContext, env: Mapping[str, str]) -> list[str]:
    """runner が注入した ``KAJI_*`` と provider context の不一致を列挙する。

    未設定・空の env は照合しない（手動実行）。``KAJI_WORKTREE_DIR`` は realpath で比較する。

    Args:
        ctx: provider が解決した context（正本）。
        env: プロセスの環境変数。

    Returns:
        ``<env 名>=<env 値> != context <属性>=<値>`` 形式の不一致一覧。
    """
    mismatches: list[str] = []
    for env_name, attribute in _ENV_CONTEXT_FIELDS:
        actual = env.get(env_name, "")
        if not actual:
            continue
        expected = str(getattr(ctx, attribute))
        if attribute == "worktree_dir":
            same = os.path.realpath(actual) == os.path.realpath(expected)
        else:
            same = actual == expected
        if not same:
            mismatches.append(f"{env_name}={actual!r} != context {attribute}={expected!r}")
    return mismatches


# ============================================================
# 報告本文と verdict
# ============================================================


@dataclass(frozen=True)
class ReportFacts:
    """報告コメントと PASS verdict に載せる事実。

    Attributes:
        issue_id: 正規化済み Issue ID。
        issue_ref: 人間可読の Issue 参照。
        identity: 同一性キー（表の ``ブランチ`` / ``ディレクトリ`` 行）。
        remote: git remote 名。
        base: default branch 名。
        base_sha: 新規時は ``<remote>/<base>``、再利用時は現在の共通祖先の短縮 SHA。
        reused: 既存 worktree を検証して再利用したか。
        venv_created: ``.venv`` symlink を今回作成したか（False は既存を確認）。
        overlay: config overlay symlink の扱い。
        note_added: Issue NOTE を今回追記したか（False は既存を確認）。
        local_ahead: ローカル ``<base>`` にある未 push commit 数。
    """

    issue_id: str
    issue_ref: str
    identity: ReportIdentity
    remote: str
    base: str
    base_sha: str
    reused: bool
    venv_created: bool
    overlay: OverlayStatus
    note_added: bool
    local_ahead: int


def _base_text(facts: ReportFacts) -> str:
    remote_base = f"{facts.remote}/{facts.base}"
    if facts.reused:
        return (
            f"{remote_base} との現在の共通祖先（{facts.base_sha}）。"
            "作成時の基点とは限らない（merge / rebase で変わりうる）"
        )
    return f"{remote_base}（{facts.base_sha}）"


def _symlink_text(created: bool) -> str:
    return "シンボリックリンク作成済み" if created else "シンボリックリンク確認済み（既存）"


def _overlay_text(overlay: OverlayStatus) -> str:
    if overlay == "absent":
        return "不要（main に未配置）"
    return _symlink_text(overlay == "created")


def format_verdict_block(verdict: Verdict) -> str:
    """verdict を ``---VERDICT---`` / ``---END_VERDICT---`` で囲んだ block にする。"""
    return f"---VERDICT---\n{dump_verdict_yaml(verdict)}---END_VERDICT---\n"


def render_report(facts: ReportFacts, verdict: Verdict) -> str:
    """報告コメント本文（marker 行を除く）を現行テンプレートの項目で生成する。

    Args:
        facts: 報告に載せる事実。
        verdict: 末尾に付ける PASS verdict（stdout / artifact と同じ値）。

    Returns:
        ``## Worktree セットアップ完了`` で始まる本文。
    """
    rows = [
        "| 項目 | 値 |",
        "|------|-----|",
        _table_row("Issue", facts.issue_ref),
        _table_row(BRANCH_LABEL, facts.identity.branch),
        _table_row(DIRECTORY_LABEL, facts.identity.directory),
        _table_row("基点", _base_text(facts)),
    ]
    if facts.reused:
        rows.append(
            _table_row("再利用", "既存 worktree を検証して再利用（reset / checkout / 削除なし）")
        )
    rows += [
        _table_row("venv", _symlink_text(facts.venv_created)),
        _table_row("config overlay", _overlay_text(facts.overlay)),
        _table_row(
            "メタ情報", "Issue本文に追記済み" if facts.note_added else "Issue本文に記載済み（既存）"
        ),
    ]
    parts = ["## Worktree セットアップ完了", "", *rows]
    if facts.local_ahead:
        parts += [
            "",
            f"> 注意: ローカル `{facts.base}` に `{facts.remote}/{facts.base}` へ未 push の commit が"
            f" {facts.local_ahead} 件あります。",
            "> これらは worktree の基点に含まれていません。",
        ]
    parts += [
        "",
        "### 次のステップ",
        "",
        "このタスクに関する今後のコマンドは、すべて以下のディレクトリ内で実行してください:",
        "",
        f"cd {facts.identity.directory}",
        "",
        "### クリーンアップ（作業完了後）",
        "",
        f"作業が完了したら `/issue-close {facts.issue_id}` を実行してください。",
        "",
        format_verdict_block(verdict),
    ]
    return "\n".join(parts)


def _validated(status: str, reason: str, evidence: str, suggestion: str) -> Verdict:
    verdict = normalize_verdict_fields(
        status=status, reason=reason, evidence=evidence, suggestion=suggestion
    )
    validate_verdict_fields(verdict, VALID_STATUSES)
    return verdict


def build_pass_verdict(facts: ReportFacts, *, comment_posted: bool) -> Verdict:
    """PASS verdict を生成し、#568 の正規化・検証を通す。

    Args:
        facts: 報告に載せる事実。
        comment_posted: 今回報告コメントを投稿するか（False は同一報告が既存）。

    Returns:
        検証済みの PASS verdict。
    """
    remote_base = f"{facts.remote}/{facts.base}"
    if facts.reused:
        worktree_line = (
            f"既存 worktree {facts.identity.directory}（{facts.identity.branch}）を検証して再利用"
            f"（Git 書き込みなし）。fetch 直後の {remote_base} と FETCH_HEAD の一致を確認し、"
            f"現在の共通祖先は {facts.base_sha}"
        )
    else:
        worktree_line = (
            f"fetch 直後の {remote_base}（{facts.base_sha}）と FETCH_HEAD の一致を確認し、"
            f"{facts.identity.branch} を {facts.identity.directory} に --no-track で作成"
        )
    evidence = "\n".join(
        [
            worktree_line,
            f"venv: {_symlink_text(facts.venv_created)}",
            f"config overlay: {_overlay_text(facts.overlay)}",
            f"Issue NOTE: {'追記' if facts.note_added else '既存を確認'}",
            f"報告コメント: {'投稿' if comment_posted else '同一の start PASS 報告が既存のため省略'}",
        ]
    )
    reason = (
        "既存 worktree を検証して再利用し、start を完了" if facts.reused else "Worktree 構築成功"
    )
    return _validated("PASS", reason, evidence, "")


def build_abort_verdict(
    issue_id: str,
    *,
    reason: str,
    evidence: str,
    completed: Sequence[str],
    suggestion: str = "",
) -> Verdict:
    """ABORT verdict を生成し、#568 の正規化・検証を通す。

    Args:
        issue_id: 正規化済み Issue ID（再実行コマンドに使う）。
        reason: 判定理由。
        evidence: 失敗した操作・コマンド・終了コード・stderr 末尾。
        completed: この時点で完了済みの副作用。
        suggestion: 原因に固有の対処。空なら既定文のみ。

    Returns:
        検証済みの ABORT verdict（suggestion は常に再実行コマンドを含む）。
    """
    done = ", ".join(completed) if completed else "なし"
    rerun = (
        f"原因を解消してから main checkout で `kaji issue start {issue_id}` を再実行する"
        "（一致する worktree は検証して再利用され、作成済みの NOTE / 同一報告は重複しない）。"
    )
    full_suggestion = f"{suggestion}\n{rerun}" if suggestion else rerun
    return _validated("ABORT", reason, f"{evidence}\n完了済みの副作用: {done}", full_suggestion)


# ============================================================
# 実行
# ============================================================


@dataclass(frozen=True)
class StartOutcome:
    """``run_issue_start`` の結果。

    Attributes:
        verdict: 検証済み verdict（PASS / ABORT）。
        reused: 既存 worktree を再利用したか。
        note_added: Issue NOTE を今回追記したか。
        report_posted: 報告コメントを今回投稿したか。
    """

    verdict: Verdict
    reused: bool = False
    note_added: bool = False
    report_posted: bool = False


class _StartAbort(HarnessError):
    """想定内の失敗。``run_issue_start`` 内で ABORT outcome に変換し、外へ送出しない。"""

    def __init__(self, reason: str, evidence: str, suggestion: str = "") -> None:
        self.reason = reason
        self.evidence = evidence
        self.suggestion = suggestion
        super().__init__(reason)


def _stderr_tail(stderr: str) -> str:
    lines = [line for line in stderr.strip().splitlines() if line.strip()]
    return "\n".join(lines[-_STDERR_TAIL_LINES:]) or "(no stderr)"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )


def _git_required(repo: Path, reason: str, *args: str) -> str:
    proc = _git(repo, *args)
    if proc.returncode != 0:
        raise _StartAbort(
            reason,
            f"git {' '.join(args)}: exit {proc.returncode}\nstderr: {_stderr_tail(proc.stderr)}",
        )
    return proc.stdout.strip()


@dataclass
class _StartRun:
    """1 回の start 実行。完了済みの副作用を記録し、ABORT evidence に載せる。"""

    issue_id: str
    provider: IssueProvider
    cwd: Path
    env: Mapping[str, str]
    completed: list[str]

    def run(self) -> StartOutcome:
        ctx = self._provider_call("Issue context の取得に失敗", self.provider.resolve_issue_context)
        mismatches = find_env_mismatches(ctx, self.env)
        if mismatches:
            raise _StartAbort(
                "runner が注入した KAJI_* と kaji issue context の値が一致しない",
                "\n".join(mismatches),
                "どちらの値を正とするかを確認する（state の worktree と label 由来の context の食い違い等）。",
            )
        identity = ReportIdentity.from_context(ctx)
        marker = build_kaji_verdict_marker(START_STEP_ID, "PASS")
        issue = self._provider_call("Issue の取得に失敗", self.provider.view_issue)
        existing = classify_start_reports(issue.comments, identity)
        if existing.decision is ReportDecision.ABORT:
            raise _StartAbort(*_unidentifiable_abort(existing.unidentifiable))

        main_repo = Path(
            _git_required(self.cwd, "main checkout を解決できない", "rev-parse", "--show-toplevel")
        )
        remote, base, branch = ctx.git_remote, ctx.default_branch, ctx.branch_name
        remote_base = f"{remote}/{base}"
        _git_required(main_repo, f"git fetch {remote} {base} に失敗", "fetch", remote, base)
        self.completed.append(f"git fetch {remote} {base}（remote-tracking ref 更新）")
        fetched = _git_required(
            main_repo,
            "fetch 後に FETCH_HEAD を解決できない",
            "rev-parse",
            "--verify",
            "--quiet",
            "FETCH_HEAD^{commit}",
        )
        tracking = _git_required(
            main_repo,
            f"fetch 後に {remote_base} を解決できない",
            "rev-parse",
            "--verify",
            "--quiet",
            f"refs/remotes/{remote_base}^{{commit}}",
        )
        if fetched != tracking:
            raise _StartAbort(
                f"{remote_base} が fetch した FETCH_HEAD と一致しない（古い base で続行しない）",
                f"refs/remotes/{remote_base}={tracking} != FETCH_HEAD={fetched}",
                f"remote.{remote}.fetch の refspec が {base} を {remote_base} へ写すか確認する。",
            )

        # 登録照合は realpath 同士で行うが、存在の観測は realpath 前のパスで行う。
        # realpath 化すると dangling symlink が「存在しないリンク先」に化け、NEW と誤認するため。
        path_exists = os.path.lexists(ctx.worktree_dir)
        worktree = Path(os.path.realpath(ctx.worktree_dir))
        reused = self._ensure_worktree(
            main_repo, worktree, branch, remote_base, path_exists=path_exists
        )
        venv_created = self._ensure_symlink(worktree / ".venv", main_repo / ".venv", ".venv")
        overlay: OverlayStatus = "absent"
        main_overlay = main_repo / ".kaji" / "config.local.toml"
        if main_overlay.is_file():
            overlay_link = worktree / ".kaji" / "config.local.toml"
            created = self._ensure_symlink(overlay_link, main_overlay, ".kaji/config.local.toml")
            overlay = "created" if created else "existing"
        self._confirm_registered(main_repo, worktree, branch)

        if reused:
            common = _git_required(
                main_repo,
                "共通祖先を解決できない",
                "merge-base",
                f"refs/heads/{branch}",
                remote_base,
            )
            base_sha = _git_required(
                main_repo, "SHA を短縮できない", "rev-parse", "--short", common
            )
        else:
            base_sha = _git_required(
                main_repo, "基点 SHA を解決できない", "rev-parse", "--short", remote_base
            )
        facts = ReportFacts(
            issue_id=self.issue_id,
            issue_ref=ctx.issue_ref,
            identity=identity,
            remote=remote,
            base=base,
            base_sha=base_sha,
            reused=reused,
            venv_created=venv_created,
            overlay=overlay,
            note_added=self._ensure_note(Path(ctx.worktree_dir).name, branch),
            local_ahead=_local_ahead(main_repo, remote_base, base),
        )
        post = existing.decision is ReportDecision.POST
        verdict = build_pass_verdict(facts, comment_posted=post)
        if post:
            body = f"{marker}\n{render_report(facts, verdict)}"
            self._provider_call(
                "報告コメントの投稿に失敗",
                lambda issue_id: self.provider.comment_issue(issue_id, body),
            )
            self.completed.append("報告コメント投稿")
        return StartOutcome(
            verdict=verdict, reused=reused, note_added=facts.note_added, report_posted=post
        )

    def _provider_call(self, reason: str, call: Callable[[str], _T]) -> _T:
        try:
            return call(self.issue_id)
        except GitHubProviderError as exc:
            raise _StartAbort(reason, f"{type(exc).__name__}: {exc}") from exc

    def _ensure_worktree(
        self, main_repo: Path, worktree: Path, branch: str, base: str, *, path_exists: bool
    ) -> bool:
        listing = _git_required(
            main_repo, "worktree 一覧を取得できない", "worktree", "list", "--porcelain"
        )
        entries = [
            WorktreeEntry(os.path.realpath(e.path), e.branch, e.prunable)
            for e in parse_worktree_porcelain(listing)
        ]
        branch_exists = (
            _git(main_repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}").returncode
            == 0
        )
        state = classify_worktree(
            entries,
            worktree_dir=str(worktree),
            branch=branch,
            branch_exists=branch_exists,
            path_exists=path_exists,
            path_is_dir=worktree.is_dir(),
        )
        if state is WorktreeState.REUSABLE:
            return True
        if state is not WorktreeState.NEW:
            raise _StartAbort(
                f"既存の worktree / branch が期待と一致しない: {_WORKTREE_ABORT_REASONS[state]}",
                f"state={state.value} worktree_dir={worktree} branch={branch}\n"
                f"git worktree list --porcelain:\n{listing}",
                "既存の worktree / branch を確認し、不要なら利用者が整理する"
                "（kaji は reset / 削除しない）。",
            )
        _git_required(
            main_repo,
            "git worktree add に失敗",
            "worktree",
            "add",
            "--no-track",
            "-b",
            branch,
            str(worktree),
            base,
        )
        self.completed.append(f"worktree 作成（{branch} → {worktree}）")
        return False

    def _ensure_symlink(self, link: Path, target: Path, label: str) -> bool:
        observed = os.readlink(link) if link.is_symlink() else None
        state = classify_symlink(observed, exists=os.path.lexists(link), expected=str(target))
        if state is SymlinkState.MATCHES:
            return False
        if state is SymlinkState.CONFLICT:
            kind = f"symlink → {observed}" if observed is not None else "実ファイル / ディレクトリ"
            raise _StartAbort(
                f"{label} が期待と異なる参照先を持つ（上書き・削除しない）",
                f"{link}: {kind}（期待: symlink → {target}）",
                f"{link} を確認し、不要なら利用者が削除する。",
            )
        try:
            link.parent.mkdir(exist_ok=True)
            os.symlink(target, link)
        except OSError as exc:
            raise _StartAbort(f"{label} symlink を作成できない", f"{link}: {exc}") from exc
        self.completed.append(f"{label} symlink 作成")
        return True

    def _confirm_registered(self, main_repo: Path, worktree: Path, branch: str) -> None:
        listing = _git_required(
            main_repo, "worktree 一覧を取得できない", "worktree", "list", "--porcelain"
        )
        ref = f"refs/heads/{branch}"
        if not any(
            os.path.realpath(e.path) == str(worktree) and e.branch == ref
            for e in parse_worktree_porcelain(listing)
        ):
            raise _StartAbort(
                "worktree の登録を再確認できない",
                f"{worktree} ({ref}) が git worktree list に無い:\n{listing}",
            )

    def _ensure_note(self, worktree_name: str, branch: str) -> bool:
        issue = self._provider_call("Issue の取得に失敗", self.provider.view_issue)
        if has_worktree_note(issue.body, worktree=worktree_name, branch=branch):
            return False
        new_body = build_worktree_note_body(issue.body, worktree=worktree_name, branch=branch)
        self._provider_call(
            "Issue NOTE の追記に失敗",
            lambda issue_id: self.provider.edit_issue(issue_id, body=new_body),
        )
        self.completed.append("Issue NOTE 追記")
        return True


def _unidentifiable_abort(comments: Sequence[Comment]) -> tuple[str, str, str]:
    listed = "\n".join(f"- {c.ref or 'n/a'}（{c.created_at or 'n/a'}）" for c in comments)
    return (
        "同一か判定できない start PASS 報告があり、新規投稿すると重複しうる（書き込み前に停止）",
        f"表の {BRANCH_LABEL} / {DIRECTORY_LABEL} 行が欠落または矛盾する start PASS 報告:\n{listed}",
        f"次のコメント（URL / 作成日時）を確認し、同一 start の報告なら表の {BRANCH_LABEL} / "
        f"{DIRECTORY_LABEL} 行を現在の値で補い、別物なら 1 行目の marker を外す。\n{listed}",
    )


def _local_ahead(main_repo: Path, remote_base: str, base: str) -> int:
    proc = _git(main_repo, "rev-list", "--count", f"{remote_base}..refs/heads/{base}")
    if proc.returncode != 0:
        return 0
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return 0


def run_issue_start(
    issue_id: str,
    provider: IssueProvider,
    *,
    cwd: Path,
    env: Mapping[str, str],
) -> StartOutcome:
    """start の機械的手順を 1 プロセスで実行し、PASS / ABORT の outcome を返す。

    Args:
        issue_id: 正規化済み Issue ID。
        provider: Issue 操作に使う provider。
        cwd: main checkout 内のディレクトリ（``git rev-parse --show-toplevel`` の起点）。
        env: プロセスの環境変数（``KAJI_*`` と context の一致検査に使う）。

    Returns:
        verdict を含む outcome。想定内の失敗は ABORT verdict として返し、送出しない。
    """
    completed: list[str] = []
    try:
        return _StartRun(issue_id, provider, cwd, env, completed).run()
    except _StartAbort as abort:
        verdict = build_abort_verdict(
            issue_id,
            reason=abort.reason,
            evidence=abort.evidence,
            completed=completed,
            suggestion=abort.suggestion,
        )
        return StartOutcome(verdict=verdict)
