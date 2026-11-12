"""Recovery data models and artifact serialization (Issue #288).

``FailureClassification`` は cause 軸の分類値と、それと直交する ``synthetic``
（failure record が runner 生成か）を持つ。``RecoveryDecision`` は run 単位の
``recovery.json`` に、``recovery-chain.json`` は child run が自らの chain identity を
書き出すために使う。

recovery budget / wait は運用上の安全弁であり config 化しない（Issue #288 決定 1 / 9）。
テストは ``RecoveryHandler(wait_seconds=...)`` で wait のみ注入できる。
"""

from __future__ import annotations

import fcntl
import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

RECOVERY_FILE = "recovery.json"
RECOVERY_CHAIN_FILE = "recovery-chain.json"
RECOVERY_SCHEMA_VERSION = 1

#: 1 recovery chain あたりの自動再開可能回数。config 化しない（Issue #288 決定 1）。
RECOVERY_BUDGET = 1

#: ``decision: resume`` の child run 起動前に置く固定ウェイト（秒）。決定 9。
RECOVERY_WAIT_SECONDS = 600

#: irreversible / 外部公開系の副作用を持つ skill。これを実行する step は step ID に
#: 依存せず自動再開・手動 resume 提示の対象にしない（Issue #349）。
NON_RESUMABLE_SKILLS = frozenset({"issue-start", "i-pr", "issue-close"})

FailureCause = Literal[
    "dispatch_failure",
    "verdict_resolution_failure",
    "cycle_exhausted",
    "agent_declared_abort",
    "ambiguous_worktree_abort",
    "config_or_definition_error",
    "kaji_bug_suspected",
    "runtime_error",
    "unknown_external_error",
    # Issue #322 / #396: 調査を要さない既知のユーザー前提エラー（選択した terminal
    # backend の session 外での interactive runner 起動）。incident 記録の対象外にする。
    "user_precondition_error",
    # Issue #403: 利用者の Ctrl-C による run 中断。harness の不具合ではないため
    # incident 記録の対象外にする。
    "user_interrupted",
    # 予約値。セッション異常の機械判定は pure code では不可能なため初期 classifier は
    # emit しない（将来の深掘り調査 agent 導入時に使用）。
    "external_upstream_anomaly",
    # Issue #540: ``workflow_end`` も ``failure_event`` も無いまま ``kaji run`` プロセスが
    # 消えた run（kill / OOM 等の推定）。triage 外の経路（status / retry / series resume）
    # でのみ出る。
    "process_vanished",
]

FailureSource = Literal["runner", "agent", "external", "config"]
RecoverabilityHint = Literal["candidate", "no", "unknown"]

RecoveryDecisionValue = Literal[
    "resume",
    "not_resumable",
    "exhausted",
    "comment_only",
    "bug_issue_created",
    "cancelled_newer_run_detected",
    "cancelled_interrupted",
]

FAILURE_CAUSES: frozenset[str] = frozenset(
    {
        "dispatch_failure",
        "verdict_resolution_failure",
        "cycle_exhausted",
        "agent_declared_abort",
        "ambiguous_worktree_abort",
        "config_or_definition_error",
        "kaji_bug_suspected",
        "runtime_error",
        "unknown_external_error",
        "user_precondition_error",
        "user_interrupted",
        "external_upstream_anomaly",
        "process_vanished",
    }
)

#: incident 記録（新規起票 / 再発追記 / ローカル occurrence 追記）の対象外にする cause。
#: triage コメント・run artifact・console 表示は維持する（Issue #322 / #403 / #405）。
#: 「ユーザー起因で調査を要さない」cause（#322 / #403）に加え、「契約上の正常終端であり
#: 識別署名が定数へ退化する」cause（#405）も対象になる。他の cause の一般化は scope 外
#: であり、要素追加は別 Issue で判断する。
INCIDENT_EXEMPT_CAUSES: frozenset[str] = frozenset(
    {
        "user_precondition_error",
        "user_interrupted",
        "agent_declared_abort",
        "cycle_exhausted",
    }
)

#: 抑止理由の固定文（``run.log`` の ``incident_suppressed`` event と ``recovery.json``）。
INCIDENT_SUPPRESSION_REASONS: dict[str, str] = {
    "user_precondition_error": (
        "known user precondition error (interactive terminal runner requires its selected "
        "backend session); excluded from incident recording"
    ),
    "user_interrupted": (
        "run interrupted by the operator (KeyboardInterrupt); excluded from incident recording"
    ),
    "agent_declared_abort": (
        "agent returned a legitimate ABORT verdict (safe stop / manual confirmation "
        "requested); excluded from incident recording"
    ),
    "cycle_exhausted": (
        "cycle reached max_iterations (safety valve worked as designed); "
        "excluded from incident recording"
    ),
}

FAILURE_SOURCES: frozenset[str] = frozenset({"runner", "agent", "external", "config"})
RECOVERABILITY_HINTS: frozenset[str] = frozenset({"candidate", "no", "unknown"})

RECOVERY_DECISIONS: frozenset[str] = frozenset(
    {
        "resume",
        "not_resumable",
        "exhausted",
        "comment_only",
        "bug_issue_created",
        "cancelled_newer_run_detected",
        "cancelled_interrupted",
    }
)

#: child run の exit code → ``recovery_child_final_status``。
#: 既存 exit code map（``0=OK / 1=ABORT / 2=定義エラー / 3=ランタイムエラー``）と対応する。
_CHILD_STATUS_BY_EXIT_CODE = {0: "COMPLETE", 1: "ABORT", 2: "DEFINITION_ERROR", 3: "ERROR"}


@dataclass(frozen=True)
class FailureClassification:
    """failure の cause 軸分類。``synthetic`` は cause と直交する属性。"""

    cause: FailureCause
    synthetic: bool
    source: FailureSource
    recoverability_hint: RecoverabilityHint

    def __post_init__(self) -> None:
        if self.cause not in FAILURE_CAUSES:
            raise ValueError(f"unknown failure cause: {self.cause!r}")
        if self.source not in FAILURE_SOURCES:
            raise ValueError(f"unknown failure source: {self.source!r}")
        if self.recoverability_hint not in RECOVERABILITY_HINTS:
            raise ValueError(f"unknown recoverability hint: {self.recoverability_hint!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "cause": self.cause,
            "synthetic": self.synthetic,
            "source": self.source,
            "recoverability_hint": self.recoverability_hint,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FailureClassification:
        return cls(
            cause=data["cause"],
            synthetic=bool(data["synthetic"]),
            source=data["source"],
            recoverability_hint=data["recoverability_hint"],
        )


@dataclass
class RecoveryDecision:
    """``recovery.json`` の直列化対象。decision 更新のたびに上書き保存する。

    時刻はすべて UTC ISO 8601。``resume_scheduled_at`` は decision 確定時刻 +
    ``RECOVERY_WAIT_SECONDS``、``resume_started_at`` はウェイト明けに child 起動を
    開始した実時刻。
    """

    run_id: str
    recoverable: bool
    decision: RecoveryDecisionValue
    classification: FailureClassification
    failed_step: str | None
    resume_from: str | None = None
    resume_mode: str | None = None
    resume_command: str | None = None
    reason: str = ""
    evidence: list[str] = field(default_factory=list)
    auto_recovery_attempted: bool = False
    auto_recovery_attempt_no: int = 0
    recovery_parent_run_id: str | None = None
    recovery_root_run_id: str | None = None
    recovery_child_run_id: str | None = None
    recovery_child_final_status: str | None = None
    resume_scheduled_at: str | None = None
    resume_started_at: str | None = None
    discarded_resume_session: bool = False
    triage_comment_ref: str | None = None
    bug_issue: dict[str, str] | None = None
    workflow_path: str = ""
    # Issue #304 第1層: incident 記録の再入ガード / 監査痕跡（additive・optional）。
    incident_ref: str | None = None
    incident_action: str | None = None
    incident_transient_closed: bool = False
    # Issue #322: incident 記録を抑止した事実と理由（additive・optional）。
    incident_suppressed: bool = False
    incident_suppression_reason: str | None = None
    # Issue #540: ``--retry-failed-step`` / series resume の記録（additive・optional）。
    # 値は JSON の dict（requested_at / trigger / outcome / gates / ...）。
    retries: list[dict[str, Any]] = field(default_factory=list)
    schema_version: int = RECOVERY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.decision not in RECOVERY_DECISIONS:
            raise ValueError(f"unknown recovery decision: {self.decision!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "recoverable": self.recoverable,
            "decision": self.decision,
            "classification": self.classification.to_dict(),
            "failed_step": self.failed_step,
            "resume_from": self.resume_from,
            "resume_mode": self.resume_mode,
            "resume_command": self.resume_command,
            "reason": self.reason,
            "evidence": list(self.evidence),
            "auto_recovery_attempted": self.auto_recovery_attempted,
            "auto_recovery_attempt_no": self.auto_recovery_attempt_no,
            "recovery_parent_run_id": self.recovery_parent_run_id,
            "recovery_root_run_id": self.recovery_root_run_id,
            "recovery_child_run_id": self.recovery_child_run_id,
            "recovery_child_final_status": self.recovery_child_final_status,
            "resume_scheduled_at": self.resume_scheduled_at,
            "resume_started_at": self.resume_started_at,
            "discarded_resume_session": self.discarded_resume_session,
            "triage_comment_ref": self.triage_comment_ref,
            "bug_issue": self.bug_issue,
            "workflow_path": self.workflow_path,
            "incident_ref": self.incident_ref,
            "incident_action": self.incident_action,
            "incident_transient_closed": self.incident_transient_closed,
            "incident_suppressed": self.incident_suppressed,
            "incident_suppression_reason": self.incident_suppression_reason,
            **({"retries": [dict(entry) for entry in self.retries]} if self.retries else {}),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecoveryDecision:
        return cls(
            run_id=data["run_id"],
            recoverable=bool(data["recoverable"]),
            decision=data["decision"],
            classification=FailureClassification.from_dict(data["classification"]),
            failed_step=data.get("failed_step"),
            resume_from=data.get("resume_from"),
            resume_mode=data.get("resume_mode"),
            resume_command=data.get("resume_command"),
            reason=data.get("reason", ""),
            evidence=list(data.get("evidence", [])),
            auto_recovery_attempted=bool(data.get("auto_recovery_attempted", False)),
            auto_recovery_attempt_no=int(data.get("auto_recovery_attempt_no", 0)),
            recovery_parent_run_id=data.get("recovery_parent_run_id"),
            recovery_root_run_id=data.get("recovery_root_run_id"),
            recovery_child_run_id=data.get("recovery_child_run_id"),
            recovery_child_final_status=data.get("recovery_child_final_status"),
            resume_scheduled_at=data.get("resume_scheduled_at"),
            resume_started_at=data.get("resume_started_at"),
            discarded_resume_session=bool(data.get("discarded_resume_session", False)),
            triage_comment_ref=data.get("triage_comment_ref"),
            bug_issue=data.get("bug_issue"),
            workflow_path=data.get("workflow_path", ""),
            incident_ref=data.get("incident_ref"),
            incident_action=data.get("incident_action"),
            incident_transient_closed=bool(data.get("incident_transient_closed", False)),
            incident_suppressed=bool(data.get("incident_suppressed", False)),
            incident_suppression_reason=data.get("incident_suppression_reason"),
            retries=[dict(entry) for entry in data.get("retries", []) if isinstance(entry, dict)],
            schema_version=int(data.get("schema_version", RECOVERY_SCHEMA_VERSION)),
        )


def recovery_budget_consumed(decision: RecoveryDecision) -> bool:
    """既存の ``recovery.json`` が recovery budget を消費済みかを判定する。

    「1 recovery chain あたり ``RECOVERY_BUDGET`` 回」の契約は、chain identity
    （``recovery-chain.json``）だけでなく **同一 run に対する handler 再入**からも
    守る必要がある。``decision == "resume"`` は child 起動を確約した時点で書き出される
    ため、ウェイト中に handler が強制終了された場合でも budget は消費済みとして扱う
    （fail-closed。再開の取りこぼしより二重起動の回避を優先する）。
    """
    if decision.auto_recovery_attempted:
        return True
    if decision.auto_recovery_attempt_no >= RECOVERY_BUDGET:
        return True
    if decision.recovery_child_run_id is not None:
        return True
    return decision.decision == "resume"


def write_recovery_json(path: Path, decision: RecoveryDecision) -> None:
    """``RecoveryDecision`` を pure JSON で書き出す（親ディレクトリは必要なら作成）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(decision.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def read_recovery_json(path: Path) -> RecoveryDecision:
    """``recovery.json`` を読み戻す。"""
    return RecoveryDecision.from_dict(json.loads(path.read_text(encoding="utf-8")))


RECOVERY_WRITE_LOCK_FILE = ".recovery.lock"


class RecoveryWriteLock:
    """run dir 単位の ``recovery.json`` 更新用ブロッキング lock（``fcntl.flock``）。

    retry 記録（``RetryRecorder``）と triage の decision 上書き（``RecoveryHandler``）が
    同じ lock で read-modify-write を直列化し、互いの更新を失わないようにする。
    """

    def __init__(self, run_dir: Path) -> None:
        self.path = run_dir / RECOVERY_WRITE_LOCK_FILE
        self._fd: int | None = None

    def __enter__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
        except OSError:
            os.close(fd)
            raise
        self._fd = fd

    def __exit__(self, *_exc: object) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _merge_retries(
    on_disk: list[dict[str, Any]], in_memory: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """保存済み ``retries`` を正とし、未保存の in-memory entry（``request_id`` 単位）を追記する。"""
    merged = [dict(entry) for entry in on_disk]
    known = {entry.get("request_id") for entry in merged if entry.get("request_id") is not None}
    for entry in in_memory:
        request_id = entry.get("request_id")
        if request_id is not None and request_id in known:
            continue
        if request_id is None and entry in merged:
            continue
        merged.append(dict(entry))
    return merged


def write_recovery_decision_preserving_retries(path: Path, decision: RecoveryDecision) -> None:
    """triage の decision で ``recovery.json`` を上書きする。既存の ``retries`` は保持する。

    ``plan_recovery`` は ``retries`` を空で組み立てるため、単純な上書きでは
    ``--retry-failed-step`` / series resume の記録が消える（Issue #540）。
    ``RecoveryWriteLock`` の下で既存ファイルの ``retries`` を読み戻してマージする。
    既存ファイルが読めない場合は従来どおり decision をそのまま書く。
    """
    with RecoveryWriteLock(path.parent):
        retries = list(decision.retries)
        if path.is_file():
            try:
                existing = read_recovery_json(path)
            except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError):
                existing = None
            if existing is not None:
                retries = _merge_retries(existing.retries, decision.retries)
        write_recovery_json(path, replace(decision, retries=retries))


RecoveryTrigger = Literal["auto_recover", "retry_failed_step", "series_resume"]
RECOVERY_TRIGGERS: frozenset[str] = frozenset(
    {"auto_recover", "retry_failed_step", "series_resume"}
)


def write_recovery_chain(
    path: Path,
    *,
    root_run_id: str,
    parent_run_id: str,
    chain_index: int | None = None,
    trigger: str | None = None,
) -> None:
    """child run の chain identity を ``recovery-chain.json`` に書き出す。

    Issue #540: ``chain_index``（chain 内で何回目の child か）と ``trigger``
    （``auto_recover`` / ``retry_failed_step`` / ``series_resume``）は optional。
    """
    data: dict[str, Any] = {"root_run_id": root_run_id, "parent_run_id": parent_run_id}
    if chain_index is not None:
        data["chain_index"] = chain_index
    if trigger is not None:
        data["trigger"] = trigger
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class RecoveryChainInfo:
    """``recovery-chain.json`` の内容（Issue #540 の optional 項目を含む）。"""

    root_run_id: str
    parent_run_id: str
    chain_index: int | None = None
    trigger: str | None = None


class RecoveryChainError(ValueError):
    """``recovery-chain.json`` が存在するが読めない・不正な場合。"""


def read_recovery_chain_info(path: Path) -> RecoveryChainInfo | None:
    """``recovery-chain.json`` を読み、``chain_index`` / ``trigger`` まで返す。

    Returns:
        chain 情報。ファイルが無ければ ``None``（root run）。

    Raises:
        RecoveryChainError: 存在するが読めない・JSON 不正・必須 key 欠落の場合。
    """
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecoveryChainError(f"{path.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise RecoveryChainError(f"{path.name}: top-level is not an object")
    root = data.get("root_run_id")
    parent = data.get("parent_run_id")
    if not isinstance(root, str) or not isinstance(parent, str):
        raise RecoveryChainError(f"{path.name}: root_run_id / parent_run_id missing")
    raw_index = data.get("chain_index")
    chain_index = (
        raw_index if isinstance(raw_index, int) and not isinstance(raw_index, bool) else None
    )
    raw_trigger = data.get("trigger")
    trigger = raw_trigger if isinstance(raw_trigger, str) else None
    return RecoveryChainInfo(root, parent, chain_index, trigger)


def read_recovery_chain(path: Path) -> tuple[str, str] | None:
    """``recovery-chain.json`` から ``(root_run_id, parent_run_id)`` を読む。

    ファイル不在 / 破損 / 必須 key 欠落は ``None``（= chain 情報なし = root run 扱い）。
    誤って budget を消費しないよう、読めない場合は child とみなさない。
    """
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    root = data.get("root_run_id")
    parent = data.get("parent_run_id")
    if not isinstance(root, str) or not isinstance(parent, str):
        return None
    return root, parent


def derive_child_final_status(exit_code: int | None) -> str:
    """child run の exit code から ``recovery_child_final_status`` を導出する。

    既存 exit code map の 4 値に落とす。map 外（signal 終了 ``-N`` / shell 慣例
    ``128+N`` など）と取得不能（``None``）は ``ERROR`` に寄せる。
    """
    if exit_code is None:
        return "ERROR"
    return _CHILD_STATUS_BY_EXIT_CODE.get(exit_code, "ERROR")


def select_newer_run_ids(run_ids: list[str], run_id: str) -> list[str]:
    """``run_id`` より新しい run_id を辞書順比較で抽出し、昇順で返す。

    ``allocate_run_dir`` の ``YYMMDDHHMMSS[-NNN]`` 形式では辞書順 = 時系列順が成立する
    （同一 ``artifacts_dir`` 内比較。世紀跨ぎ・システム時計の後退は考慮外）。
    """
    return sorted(rid for rid in run_ids if rid > run_id)
