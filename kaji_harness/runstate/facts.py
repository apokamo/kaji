"""Run / Issue facts: the single implementation of liveness, latest run and failed step.

``kaji status``、``kaji recover --retry-failed-step``、``kaji run-series --resume`` は
すべて本モジュールの ``collect_issue_facts`` / ``collect_run_facts`` で事実を得る
（Issue #540 完了条件「同じ判定を 2 箇所に書かない」）。読めない artifact は例外に
せず ``read_errors`` に積み、retry / series のゲートを止める artifact は
``blocking_errors`` にも積む。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ..recovery.classify import classify_failure
from ..recovery.models import (
    RECOVERY_CHAIN_FILE,
    RECOVERY_FILE,
    FailureClassification,
    RecoveryChainError,
    read_recovery_chain_info,
    read_recovery_json,
)
from ..recovery.snapshot import FailureSnapshot, collect_snapshot
from ..state import STATE_FILE
from .launch import LaunchEntry, list_launch_records
from .process import (
    PROCESS_FILE,
    HostProbe,
    Liveness,
    ProcessRecord,
    ProcessRecordError,
    complete_with_run_log,
    probe_liveness,
    read_process_record,
)
from .runlog import (
    RESULT_FILE,
    ArtifactProblem,
    ReadErrorKind,
    attempt_dir_name,
    find_unterminated_step,
    last_event,
    read_run_log,
    required_result_problems,
)

Termination = Literal["running", "ended", "vanished", "unknown"]
PANE_METADATA_FILE = "pane-metadata.json"


@dataclass(frozen=True)
class ReadError:
    """読めなかった artifact 1 件。``path`` は artifacts dir からの相対パス。"""

    path: str
    error: ReadErrorKind
    message: str


@dataclass(frozen=True)
class StepRef:
    """run.log の最後の ``step_start``。"""

    id: str
    attempt: int | None
    agent: str | None
    session_id: str | None


@dataclass(frozen=True)
class PaneRef:
    """最新 attempt の Herdr pane（``pane-metadata.json``）。"""

    pane_id: str
    origin_pane: str | None
    run_id: str | None
    backend: str = "herdr"


@dataclass(frozen=True)
class RecoveryRef:
    """recovery child の chain identity。"""

    root_run_id: str
    parent_run_id: str
    chain_index: int | None


@dataclass
class RunFacts:
    """1 run の事実。``None`` は「無い」または「読めない」（後者は ``read_errors``）。"""

    run_id: str
    run_dir: Path
    workflow: str | None = None
    started_at: str | None = None
    pid: int | None = None
    liveness: Liveness = field(default_factory=lambda: Liveness(None, "no_record"))
    termination: Termination | None = None
    end_status: str | None = None
    ended_at: str | None = None
    last_step: StepRef | None = None
    failed_step: str | None = None
    failure: FailureClassification | None = None
    worktree_dir: str | None = None
    branch_name: str | None = None
    pane: PaneRef | None = None
    pane_unreadable: bool = False
    recovery: RecoveryRef | None = None
    read_errors: list[ReadError] = field(default_factory=list)
    #: retry / series resume の ``artifact_unreadable`` ゲートを止める read error。
    blocking_errors: list[ReadError] = field(default_factory=list)
    events: list[dict[str, Any]] | None = None
    snapshot: FailureSnapshot | None = None


@dataclass
class IssueFacts:
    """1 Issue の事実（全 run と全 launch record の生存を含む）。"""

    issue_id: str
    run_ids: list[str] = field(default_factory=list)
    latest_run: RunFacts | None = None
    #: run_id → 生存判定（全 run の ``process.json`` と run_id 付き launch record の合成）
    run_liveness: dict[str, Liveness] = field(default_factory=dict)
    launches: list[tuple[LaunchEntry, Liveness]] = field(default_factory=list)
    any_run_alive: bool | None = False
    launching: bool | None = False
    read_errors: list[ReadError] = field(default_factory=list)


def list_run_ids(artifacts_dir: Path, issue_id: str) -> list[str]:
    """Issue の run_id を昇順（= 時系列）で返す。"""
    runs_dir = artifacts_dir / issue_id / "runs"
    if not runs_dir.is_dir():
        return []
    return sorted(path.name for path in runs_dir.iterdir() if path.is_dir())


def latest_run_dir(artifacts_dir: Path, issue_id: str) -> Path | None:
    """run_id の辞書順最大の run dir を返す（無ければ ``None``）。"""
    run_ids = list_run_ids(artifacts_dir, issue_id)
    if not run_ids:
        return None
    return artifacts_dir / issue_id / "runs" / run_ids[-1]


def _relative(artifacts_dir: Path, path: Path) -> str:
    try:
        return str(path.relative_to(artifacts_dir))
    except ValueError:
        return str(path)


def _problem_error(artifacts_dir: Path, run_dir: Path, problem: ArtifactProblem) -> ReadError:
    return ReadError(
        _relative(artifacts_dir, run_dir / problem.path), problem.error, problem.message
    )


def _read_json_object(path: Path) -> tuple[dict[str, Any] | None, ReadErrorKind | None, str]:
    """JSON object を読む。無ければ ``(None, None, "")``、壊れていれば種別と理由。"""
    if not path.is_file():
        return None, None, ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        return None, "unreadable", str(exc)
    except json.JSONDecodeError as exc:
        return None, "invalid", str(exc)
    if not isinstance(data, dict):
        return None, "invalid", "top-level is not an object"
    return data, None, ""


def read_process_liveness(
    run_dir: Path,
    *,
    artifacts_dir: Path,
    events: list[dict[str, Any]] | None,
    probe: HostProbe | None = None,
) -> tuple[ProcessRecord | None, Liveness, ReadError | None]:
    """run の ``process.json`` を読み、run.log の終端条件で補完した生存判定を返す。"""
    path = run_dir / PROCESS_FILE
    try:
        record = read_process_record(path)
    except ProcessRecordError as exc:
        error = ReadError(_relative(artifacts_dir, path), "invalid", str(exc))
        return None, probe_liveness(None, unreadable=True, probe=probe), error
    liveness = complete_with_run_log(probe_liveness(record, probe=probe), events)
    return record, liveness, None


def decide_termination(
    events: list[dict[str, Any]] | None, alive: bool | None
) -> Termination | None:
    """設計書の ``termination`` 決定表。"""
    if events is None:
        return "running" if alive is True else None
    if last_event(events, "workflow_end") is not None:
        return "ended"
    if alive is True:
        return "running"
    if alive is False:
        return "vanished"
    return "unknown"


def _attempt_dir(run_dir: Path, step_id: str, attempt: int | None) -> Path | None:
    steps_dir = run_dir / "steps" / step_id
    if attempt is not None:
        candidate = steps_dir / attempt_dir_name(attempt)
        if candidate.is_dir():
            return candidate
    attempts = sorted(path for path in steps_dir.glob("attempt-*") if path.is_dir())
    return attempts[-1] if attempts else None


def _last_step(run_dir: Path, events: list[dict[str, Any]]) -> StepRef | None:
    entry = last_event(events, "step_start")
    if entry is None or not isinstance(entry.get("step_id"), str):
        return None
    step_id: str = entry["step_id"]
    raw_attempt = entry.get("attempt")
    attempt = (
        raw_attempt if isinstance(raw_attempt, int) and not isinstance(raw_attempt, bool) else None
    )
    agent = entry.get("agent") if isinstance(entry.get("agent"), str) else None
    session_id: str | None = None
    if attempt is not None:
        result, _, _ = _read_json_object(
            run_dir / "steps" / step_id / attempt_dir_name(attempt) / RESULT_FILE
        )
        if result is not None and isinstance(result.get("session_id"), str):
            session_id = result["session_id"]
    if session_id is None and isinstance(entry.get("session_id"), str):
        session_id = entry["session_id"]
    return StepRef(step_id, attempt, agent, session_id)


def _ended_failed(termination: Termination | None, end_status: str | None) -> bool:
    return termination == "ended" and end_status in ("ERROR", "ABORT")


def collect_run_facts(
    run_dir: Path,
    *,
    artifacts_dir: Path,
    issue_id: str,
    probe: HostProbe | None = None,
    probe_git: bool = False,
) -> RunFacts:
    """1 run の事実を集める（例外を投げず、読めない項目は ``read_errors``）。

    Args:
        run_dir: ``<artifacts>/<issue>/runs/<run_id>``。
        artifacts_dir: ``resolve_artifacts_dir`` の値。
        issue_id: canonical Issue ID。
        probe: 生存判定の OS 境界の注入点。
        probe_git: ``True`` なら失敗分類の snapshot で git を照会する（retry 用）。
            ``kaji status`` は ``False``（読み取り専用）。
    """
    facts = RunFacts(run_id=run_dir.name, run_dir=run_dir)
    log = read_run_log(run_dir)
    events = log.events
    facts.events = events
    if log.problem is not None:
        error = _problem_error(artifacts_dir, run_dir, log.problem)
        facts.read_errors.append(error)
        facts.blocking_errors.append(error)

    record, liveness, process_error = read_process_liveness(
        run_dir, artifacts_dir=artifacts_dir, events=events, probe=probe
    )
    facts.pid = record.pid if record is not None else None
    facts.liveness = liveness
    if process_error is not None:
        facts.read_errors.append(process_error)
    facts.termination = decide_termination(events, liveness.alive)

    if events is not None:
        start = last_event(events, "workflow_start")
        if start is not None:
            facts.workflow = (
                start.get("workflow") if isinstance(start.get("workflow"), str) else None
            )
            facts.started_at = start.get("ts") if isinstance(start.get("ts"), str) else None
        end = last_event(events, "workflow_end")
        if facts.termination == "ended" and end is not None:
            facts.end_status = end.get("status") if isinstance(end.get("status"), str) else None
            facts.ended_at = end.get("ts") if isinstance(end.get("ts"), str) else None
        facts.last_step = _last_step(run_dir, events)
        for problem in required_result_problems(run_dir, events):
            error = _problem_error(artifacts_dir, run_dir, problem)
            facts.read_errors.append(error)
            facts.blocking_errors.append(error)

    _collect_session_state(facts, artifacts_dir=artifacts_dir, issue_id=issue_id)
    _collect_pane(facts, artifacts_dir=artifacts_dir)
    _collect_recovery(facts, artifacts_dir=artifacts_dir)

    if events is not None and (
        _ended_failed(facts.termination, facts.end_status) or facts.termination == "vanished"
    ):
        try:
            snapshot = collect_snapshot(
                run_dir=run_dir,
                artifacts_dir=artifacts_dir,
                issue_id=issue_id,
                provider_available=True,
                allow_unterminated=facts.termination == "vanished",
                probe_git=probe_git,
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # 防御: snapshot 側の読み取り失敗（非 UTF-8 等）を例外として漏らさず、
            # status は read_errors、retry / series は artifact_unreadable に変換する。
            error = ReadError(
                _relative(artifacts_dir, run_dir),
                "unreadable",
                f"failure snapshot: {type(exc).__name__}: {exc}",
            )
            facts.read_errors.append(error)
            facts.blocking_errors.append(error)
            return facts
        facts.snapshot = snapshot
        facts.failure = classify_failure(snapshot)
        if facts.termination == "vanished":
            pending = find_unterminated_step(events)
            facts.failed_step = pending[0] if pending else None
        else:
            facts.failed_step = snapshot.failed_step
    return facts


def _collect_session_state(facts: RunFacts, *, artifacts_dir: Path, issue_id: str) -> None:
    path = artifacts_dir / issue_id / STATE_FILE
    data, kind, message = _read_json_object(path)
    if kind is not None:
        error = ReadError(_relative(artifacts_dir, path), kind, message)
        facts.read_errors.append(error)
        facts.blocking_errors.append(error)
        return
    if data is None:
        return
    worktree = data.get("worktree_dir")
    branch = data.get("branch_name")
    facts.worktree_dir = worktree if isinstance(worktree, str) and worktree else None
    facts.branch_name = branch if isinstance(branch, str) and branch else None


def _collect_pane(facts: RunFacts, *, artifacts_dir: Path) -> None:
    step = facts.last_step
    if step is None:
        return
    attempt_dir = _attempt_dir(facts.run_dir, step.id, step.attempt)
    if attempt_dir is None:
        return
    path = attempt_dir / PANE_METADATA_FILE
    data, kind, message = _read_json_object(path)
    if kind is None and data is not None:
        # 所有確認（agent_pane ゲート）に必要な 3 項目。欠落・型不正は照会せず
        # agent_pane_unverifiable にする（不足情報を「非所有」と解釈しない）。
        for key in ("pane_id", "origin_pane", "kaji_run"):
            value = data.get(key)
            if not isinstance(value, str) or not value:
                kind, message = "invalid", f"{key} missing or not a non-empty string"
                break
    if kind is not None:
        facts.read_errors.append(ReadError(_relative(artifacts_dir, path), kind, message))
        facts.pane_unreadable = True
        return
    if data is None:
        return
    facts.pane = PaneRef(
        pane_id=data["pane_id"],
        origin_pane=data["origin_pane"],
        run_id=data["kaji_run"],
    )


def _collect_recovery(facts: RunFacts, *, artifacts_dir: Path) -> None:
    chain_path = facts.run_dir / RECOVERY_CHAIN_FILE
    try:
        chain = read_recovery_chain_info(chain_path)
    except RecoveryChainError as exc:
        error = ReadError(_relative(artifacts_dir, chain_path), "invalid", str(exc))
        facts.read_errors.append(error)
        facts.blocking_errors.append(error)
        chain = None
    if chain is not None:
        facts.recovery = RecoveryRef(chain.root_run_id, chain.parent_run_id, chain.chain_index)
    recovery_path = facts.run_dir / RECOVERY_FILE
    if recovery_path.is_file():
        try:
            read_recovery_json(recovery_path)
        except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
            error = ReadError(_relative(artifacts_dir, recovery_path), "invalid", str(exc))
            facts.read_errors.append(error)
            facts.blocking_errors.append(error)


def _merge_alive(values: list[bool | None]) -> bool | None:
    """1 件でも true なら true、true がなく null があれば null、それ以外は false。"""
    if any(value is True for value in values):
        return True
    if any(value is None for value in values):
        return None
    return False


def collect_issue_facts(
    artifacts_dir: Path,
    issue_id: str,
    *,
    probe: HostProbe | None = None,
    probe_git: bool = False,
    target_run_id: str | None = None,
) -> IssueFacts:
    """Issue の全 run・全 launch record の生存と、最新 run（または対象 run）の事実を集める。

    Args:
        artifacts_dir: ``resolve_artifacts_dir`` の値。
        issue_id: canonical Issue ID（``normalize_id`` の値）。
        probe: 生存判定の OS 境界の注入点。
        probe_git: 対象 run の失敗分類で git を照会するか。
        target_run_id: 詳細を集める run。省略時は最新 run。``latest_run`` に入る。
    """
    facts = IssueFacts(issue_id=issue_id)
    facts.run_ids = list_run_ids(artifacts_dir, issue_id)
    runs_dir = artifacts_dir / issue_id / "runs"
    detail_id = target_run_id or (facts.run_ids[-1] if facts.run_ids else None)

    for run_id in facts.run_ids:
        run_dir = runs_dir / run_id
        if run_id == detail_id:
            continue
        log = read_run_log(run_dir)
        _, liveness, error = read_process_liveness(
            run_dir, artifacts_dir=artifacts_dir, events=log.events, probe=probe
        )
        facts.run_liveness[run_id] = liveness
        if error is not None:
            facts.read_errors.append(error)

    if detail_id is not None and (runs_dir / detail_id).is_dir():
        detail = collect_run_facts(
            runs_dir / detail_id,
            artifacts_dir=artifacts_dir,
            issue_id=issue_id,
            probe=probe,
            probe_git=probe_git,
        )
        facts.latest_run = detail
        facts.run_liveness[detail_id] = detail.liveness

    for entry in list_launch_records(artifacts_dir, issue_id):
        if entry.record is None:
            liveness = probe_liveness(None, unreadable=True, probe=probe)
            facts.read_errors.append(
                ReadError(_relative(artifacts_dir, entry.path), "invalid", entry.error or "")
            )
        else:
            liveness = probe_liveness(entry.record, probe=probe)
        facts.launches.append((entry, liveness))
        launch_run_id = entry.record.run_id if entry.record is not None else None
        # process.json の書き込みに失敗した run も、run_id 付き launch record で生存を示す
        if (
            launch_run_id is not None
            and liveness.alive is True
            and launch_run_id in facts.run_liveness
        ):
            facts.run_liveness[launch_run_id] = liveness
            if facts.latest_run is not None and facts.latest_run.run_id == launch_run_id:
                facts.latest_run.liveness = liveness
                facts.latest_run.termination = decide_termination(
                    facts.latest_run.events, liveness.alive
                )

    facts.any_run_alive = _merge_alive(
        [value.alive for value in facts.run_liveness.values()]
        + [liveness.alive for _, liveness in facts.launches]
    )
    pending = [
        liveness.alive
        for entry, liveness in facts.launches
        if entry.record is None or entry.record.run_id is None
    ]
    alive_launches = [liveness.alive for _, liveness in facts.launches]
    if any(value is True for value in pending):
        facts.launching = True
    elif any(value is None for value in alive_launches):
        facts.launching = None
    else:
        facts.launching = False
    return facts
