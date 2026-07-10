"""Failure snapshot collection (Issue #288).

失敗 run の artifact（``run.log`` / ``result.json`` / ``session-state.json`` /
``recovery-chain.json``）と git state を読み、classifier / decision planner が
参照する不変の ``FailureSnapshot`` を組み立てる。

artifact が第一根拠であり、agent の会話内記憶は入力にしない（Issue #288 決定 5）。
読めない artifact は例外にせず ``artifact_read_errors`` として記録し、classifier が
``kaji_bug_suspected`` に落とす。
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..state import STATE_FILE
from .models import (
    RECOVERY_CHAIN_FILE,
    RECOVERY_FILE,
    read_recovery_chain,
    read_recovery_json,
    recovery_budget_consumed,
    select_newer_run_ids,
)
from .report import sanitize_evidence

_logger = logging.getLogger(__name__)

_GIT_TIMEOUT = 15
#: evidence に載せる ``git status --porcelain`` の先頭行数。
_PORCELAIN_PREVIEW_LINES = 3


@dataclass(frozen=True)
class FailureEvent:
    """runner が ``run.log`` に emit した構造化 failure record。"""

    kind: str
    step_id: str | None = None
    exception_type: str | None = None
    cycle_name: str | None = None
    synthetic: bool = True


@dataclass(frozen=True)
class GitStateSummary:
    """再開先 worktree の git state 要約。

    ``available`` は worktree ディレクトリが実在し ``git`` 照会に成功したことを示す。
    未コミット変更自体は safety gate にしない（implement 系 step の失敗では正常）ため、
    件数と先頭数行のみを evidence 用に保持する。
    """

    branch: str | None = None
    porcelain_preview: tuple[str, ...] = ()
    changed_files: int = 0
    available: bool = False


@dataclass(frozen=True)
class FailureSnapshot:
    """failure triage の入力一式（pure data）。"""

    run_id: str
    run_dir: Path
    run_log_schema_version: int | None = None
    workflow_end_status: str | None = None
    workflow_end_error: str | None = None
    failure_event: FailureEvent | None = None
    failed_step: str | None = None
    attempt_error: str | None = None
    attempt_result_present: bool = False
    attempt_synthetic: bool | None = None
    state_loaded: bool = True
    state_last_completed_step: str | None = None
    state_worktree_dir: str | None = None
    state_branch_name: str | None = None
    git: GitStateSummary = field(default_factory=GitStateSummary)
    is_recovery_child: bool = False
    recovery_root_run_id: str | None = None
    recovery_parent_run_id: str | None = None
    budget_consumed: bool = False
    prior_recovery_child_run_id: str | None = None
    provider_available: bool = True
    artifact_read_errors: tuple[str, ...] = ()
    newer_run_ids: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()

    @property
    def failure_error_text(self) -> str:
        """分類 / sensitive pattern 判定に使うエラー文字列（attempt 優先）。"""
        return "\n".join(t for t in (self.attempt_error, self.workflow_end_error) if t)

    @property
    def emits_failure_events(self) -> bool:
        """この run.log が ``failure_event`` 契約（Issue #288）下で書かれたか。

        契約以前の run では ABORT 終端に ``failure_event`` が無いのが正常であり、
        その不在を harness の矛盾と断定してはならない。
        """
        return self.run_log_schema_version is not None and self.run_log_schema_version >= 1

    @property
    def workflow_end_exception_type(self) -> str | None:
        """``workflow_end.error`` の ``"<Type>: <message>"`` から型名を取り出す。"""
        if not self.workflow_end_error:
            return None
        head = self.workflow_end_error.split(":", 1)[0].strip()
        return head or None


def probe_git_state(worktree_dir: Path) -> GitStateSummary:
    """worktree の現在 branch と ``git status --porcelain`` 要約を best-effort で取る。

    ディレクトリ不在 / 非 git / ``git`` CLI 不在 / timeout は ``available=False``。
    handler の safety gate は「取得できなかった = 再開しない」に倒す。
    """
    if not worktree_dir.is_dir():
        return GitStateSummary()
    # ``rev-parse`` は detached HEAD でも ``HEAD`` を返すが、commit の無い repo では
    # 失敗する。``symbolic-ref`` はその逆なので、両者を fallback で組み合わせる。
    branch = _git_output(worktree_dir, ["rev-parse", "--abbrev-ref", "HEAD"]) or _git_output(
        worktree_dir, ["symbolic-ref", "--short", "HEAD"]
    )
    if branch is None:
        return GitStateSummary()
    porcelain = _git_output(worktree_dir, ["status", "--porcelain"])
    if porcelain is None:
        return GitStateSummary(branch=branch, available=True)
    lines = [line for line in porcelain.splitlines() if line.strip()]
    return GitStateSummary(
        branch=branch,
        porcelain_preview=tuple(lines[:_PORCELAIN_PREVIEW_LINES]),
        changed_files=len(lines),
        available=True,
    )


def _git_output(cwd: Path, args: list[str]) -> str | None:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _logger.debug("git %s failed in %s: %s", " ".join(args), cwd, exc)
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def read_run_log_events(run_log: Path) -> list[dict[str, Any]]:
    """``run.log``（JSONL）を読み、parse できた event のみを順序どおり返す。"""
    events: list[dict[str, Any]] = []
    for line in run_log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            events.append(entry)
    return events


def _last_event(events: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for entry in reversed(events):
        if entry.get("event") == name:
            return entry
    return None


def _run_log_schema_version(events: list[dict[str, Any]]) -> int | None:
    """``workflow_start`` に記録された run.log の event 契約バージョンを返す。"""
    entry = _last_event(events, "workflow_start")
    if entry is None:
        return None
    version = entry.get("schema_version")
    return version if isinstance(version, int) else None


def _prior_recovery_state(run_dir: Path) -> tuple[bool, str | None, list[str]]:
    """同一 run に対する過去の handler 実行痕跡から budget 消費状況を読む。

    Returns:
        ``(budget_consumed, child_run_id, evidence)``。``recovery.json`` が存在するのに
        読めない場合は fail-closed で消費済みとして扱う（読めないことを理由に二重起動を
        許すと「1 chain 1 回」の契約が壊れるため）。
    """
    path = run_dir / RECOVERY_FILE
    if not path.is_file():
        return False, None, []
    try:
        prior = read_recovery_json(path)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        return (
            True,
            None,
            [f"{RECOVERY_FILE}: unreadable ({exc}); recovery budget treated as spent"],
        )
    if not recovery_budget_consumed(prior):
        return False, prior.recovery_child_run_id, []
    evidence = [
        f"{RECOVERY_FILE}: prior decision={prior.decision} "
        f"auto_recovery_attempted={str(prior.auto_recovery_attempted).lower()} "
        f"attempt_no={prior.auto_recovery_attempt_no} "
        f"child_run_id={prior.recovery_child_run_id}"
    ]
    return True, prior.recovery_child_run_id, evidence


def _parse_failure_event(entry: dict[str, Any] | None) -> FailureEvent | None:
    if entry is None:
        return None
    kind = entry.get("kind")
    if not isinstance(kind, str):
        return None
    return FailureEvent(
        kind=kind,
        step_id=entry.get("step_id"),
        exception_type=entry.get("exception_type"),
        cycle_name=entry.get("cycle_name"),
        synthetic=bool(entry.get("synthetic", True)),
    )


def _latest_attempt_result(run_dir: Path, step_id: str | None) -> dict[str, Any] | None:
    if not step_id:
        return None
    steps_dir = run_dir / "steps" / step_id
    attempts = sorted(p for p in steps_dir.glob("attempt-*") if p.is_dir())
    if not attempts:
        return None
    path = attempts[-1] / "result.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _load_state(state_path: Path) -> dict[str, Any] | None:
    if not state_path.is_file():
        return None
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or "step_history" not in data:
        return None
    return data


def _list_run_ids(runs_dir: Path) -> list[str]:
    if not runs_dir.is_dir():
        return []
    return [p.name for p in runs_dir.iterdir() if p.is_dir()]


def collect_snapshot(
    *,
    run_dir: Path,
    artifacts_dir: Path,
    issue_id: str,
    provider_available: bool,
) -> FailureSnapshot:
    """失敗 run の artifact / state / git state を収集して ``FailureSnapshot`` を返す。

    Args:
        run_dir: 対象 run の ``runs/<run_id>/``。
        artifacts_dir: ``paths.artifacts_dir`` の resolve 値。
        issue_id: canonical Issue ID（``session-state.json`` の所在）。
        provider_available: handler 起動時点で provider / Issue context が解決できたか。

    Returns:
        分類・判定に必要な入力を持つ不変 snapshot。読めない artifact は
        ``artifact_read_errors`` に記録し、例外にしない。
    """
    run_id = run_dir.name
    runs_dir = run_dir.parent
    read_errors: list[str] = []
    evidence: list[str] = []

    events: list[dict[str, Any]] = []
    run_log = run_dir / "run.log"
    try:
        events = read_run_log_events(run_log)
    except OSError as exc:
        read_errors.append(f"run.log: unreadable ({exc})")
    if not events and not read_errors:
        read_errors.append("run.log: no parsable events")

    end_entry = _last_event(events, "workflow_end")
    end_status = end_entry.get("status") if end_entry else None
    end_error = end_entry.get("error") if end_entry else None
    if end_entry is None and not read_errors:
        read_errors.append("run.log: workflow_end event missing")

    failure_event = _parse_failure_event(_last_event(events, "failure_event"))
    failed_step = failure_event.step_id if failure_event else None

    result = _latest_attempt_result(run_dir, failed_step)
    attempt_error = result.get("error") if result else None
    attempt_synthetic = bool(result["synthetic"]) if result and "synthetic" in result else None

    state = _load_state(artifacts_dir / issue_id / STATE_FILE)
    state_loaded = state is not None
    worktree_dir = state.get("worktree_dir") if state else None
    branch_name = state.get("branch_name") if state else None
    if not state_loaded:
        read_errors.append(f"{STATE_FILE}: missing or unreadable")

    git = probe_git_state(Path(worktree_dir)) if worktree_dir else GitStateSummary()

    chain = read_recovery_chain(run_dir / RECOVERY_CHAIN_FILE)
    root_run_id, parent_run_id = chain if chain else (None, None)

    # budget guard の入力は 2 系統。(1) 過去の recovery.json、(2) この run を parent と
    # する child run dir の実在。(2) は recovery.json の書き込みが失敗した場合の裏取り。
    budget_consumed, prior_child, budget_evidence = _prior_recovery_state(run_dir)
    launched_child = find_child_run_id(runs_dir, run_id) if runs_dir.is_dir() else None
    if launched_child is not None:
        budget_consumed = True
        prior_child = prior_child or launched_child
        budget_evidence.append(f"runs/: recovery child run already launched: {launched_child}")

    newer = select_newer_run_ids(_list_run_ids(runs_dir), run_id)

    # --- evidence（すべて sanitize 済み。credential 形跡を伏字化し 500 文字で切る） ---
    evidence.append(
        sanitize_evidence(f"run.log: workflow_end status={end_status} error={end_error}")
    )
    if failure_event is not None:
        evidence.append(
            sanitize_evidence(
                f"run.log: failure_event kind={failure_event.kind} "
                f"step_id={failure_event.step_id} exception_type={failure_event.exception_type} "
                f"synthetic={failure_event.synthetic}"
            )
        )
    if result is not None and failed_step:
        evidence.append(sanitize_evidence(f"steps/{failed_step}/result.json error={attempt_error}"))
    if state is not None:
        evidence.append(
            sanitize_evidence(
                f"{STATE_FILE}: last_completed_step={state.get('last_completed_step')} "
                f"branch_name={branch_name}"
            )
        )
    if git.available:
        preview = "; ".join(git.porcelain_preview) or "clean"
        evidence.append(
            sanitize_evidence(
                f"git: branch {git.branch}; porcelain: {git.changed_files} changed [{preview}]"
            )
        )
    for item in budget_evidence:
        evidence.append(sanitize_evidence(item))
    for err in read_errors:
        evidence.append(sanitize_evidence(f"artifact: {err}"))
    if newer:
        evidence.append(f"runs/: newer run dirs detected: {', '.join(newer)}")

    return FailureSnapshot(
        run_id=run_id,
        run_dir=run_dir,
        run_log_schema_version=_run_log_schema_version(events),
        workflow_end_status=end_status,
        workflow_end_error=end_error,
        failure_event=failure_event,
        failed_step=failed_step,
        attempt_error=attempt_error,
        attempt_result_present=result is not None,
        attempt_synthetic=attempt_synthetic,
        state_loaded=state_loaded,
        state_last_completed_step=state.get("last_completed_step") if state else None,
        state_worktree_dir=worktree_dir,
        state_branch_name=branch_name,
        git=git,
        is_recovery_child=chain is not None,
        recovery_root_run_id=root_run_id,
        recovery_parent_run_id=parent_run_id,
        budget_consumed=budget_consumed,
        prior_recovery_child_run_id=prior_child,
        provider_available=provider_available,
        artifact_read_errors=tuple(read_errors),
        newer_run_ids=tuple(newer),
        evidence=tuple(evidence),
    )


def find_child_run_id(runs_dir: Path, parent_run_id: str) -> str | None:
    """``recovery-chain.json.parent_run_id`` が ``parent_run_id`` の run dir を探す。

    複数該当した場合は辞書順で最新（= 最後に作られた run）を返す。
    """
    matches = [
        p.name
        for p in runs_dir.iterdir()
        if p.is_dir()
        and (read_recovery_chain(p / RECOVERY_CHAIN_FILE) or ("", ""))[1] == parent_run_id
    ]
    return max(matches) if matches else None


def list_newer_run_ids(runs_dir: Path, run_id: str) -> list[str]:
    """``runs/`` を再走査して ``run_id`` より新しい run dir を返す。"""
    return select_newer_run_ids(_list_run_ids(runs_dir), run_id)
