"""Workflow execution runner for kaji_harness.

Main loop that executes workflow steps sequentially,
manages state transitions, and handles cycle limits.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from itertools import count
from pathlib import Path

from .cli import execute_cli
from .config import KajiConfig
from .errors import (
    CLIExecutionError,
    CLINotFoundError,
    InvalidTransition,
    InvalidVerdictValue,
    IssueContextResolutionError,
    MissingResumeSessionError,
    ScriptExecutionError,
    StepTimeoutError,
    VerdictNotFound,
    VerdictParseError,
    WorkdirNotFoundError,
    WorkflowValidationError,
)
from .interactive_terminal import execute_interactive_terminal
from .logger import RunLogger
from .models import CLIResult, CostInfo, CycleDefinition, Step, Verdict, Workflow
from .prompt import build_prompt
from .providers import IssueContext, IssueProvider, PRContext, get_provider, normalize_id
from .providers.github import GitHubProviderError
from .providers.local import LocalProvider
from .recovery.models import RECOVERY_CHAIN_FILE, write_recovery_chain
from .result import RESULT_FILE, AttemptResult, derive_signal, write_result_json
from .script_exec import execute_exec, execute_script
from .skill import SkillMetadata, load_skill_metadata, validate_skill_exists
from .state import SessionState
from .verdict import create_verdict_formatter, resolve_verdict, write_verdict_yaml
from .workflow import validate_workflow
from .worktree_discovery import AmbiguousWorktreeError, discover_existing_worktree

# module-level stdlib logger. ``run()`` 内のローカル ``logger`` (RunLogger) と
# 名前衝突しないよう underscore 付きで分離する（runner.py は RunLogger を
# ``logger`` という名でローカル束縛する既存規約を持つため）。
_logger = logging.getLogger(__name__)

# Issue #235: 起動コンソール向け human-readable progress logger（kaji.* 名前空間）。
# ``_logger``（``kaji_harness.runner``）とは別ツリーで、console_log の handler に
# 伝播する。RunLogger の JSONL 契約とは独立した人間向け表示のみを担う。
_console = logging.getLogger("kaji.runner")

RUN_ID_FORMAT = "%y%m%d%H%M%S"


def allocate_run_dir(runs_dir: Path, timestamp: datetime | None = None) -> Path:
    """一意な ``runs/<run_id>/`` ディレクトリを atomic に採番して作成する。

    base は秒精度の ``YYMMDDHHMMSS``。同一秒内に既存 directory がある場合は
    ``-002`` / ``-003`` ... suffix を付け、``mkdir(exist_ok=False)`` の成功を
    一意性の判定にする。
    """
    runs_dir.mkdir(parents=True, exist_ok=True)
    base_id = (timestamp or datetime.now()).strftime(RUN_ID_FORMAT)

    for sequence in count(1):
        run_id = base_id if sequence == 1 else f"{base_id}-{sequence:03d}"
        candidate = runs_dir / run_id
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate

    raise RuntimeError("unreachable: itertools.count is infinite")


def allocate_attempt_dir(run_dir: Path, step_id: str) -> Path:
    """Issue #220: 当該 step の次の attempt ディレクトリを採番して作成する。

    layout は ``run_dir/steps/<step_id>/attempt-NNN/``。``steps/<step_id>/`` 配下に
    既存の ``attempt-*`` がある場合はその数 + 1 を採番する（cycle / retry / resume で
    同一 step が複数回 dispatch されても prompt / logs / verdict の対応関係を一意に
    保つ）。``latest`` symlink は最新 attempt を指す convenience（symlink 非対応 FS
    でも採番が壊れないよう best-effort で張り替える）。

    Args:
        run_dir: ``runs/<run_id>/``（``run.log`` と同階層）。
        step_id: step ID。

    Returns:
        作成済みの attempt ディレクトリ絶対パス。
    """
    steps_dir = run_dir / "steps" / step_id
    steps_dir.mkdir(parents=True, exist_ok=True)
    existing = [p for p in steps_dir.glob("attempt-*") if p.is_dir()]
    attempt_no = len(existing) + 1
    attempt_name = f"attempt-{attempt_no:03d}"
    attempt_dir = steps_dir / attempt_name
    attempt_dir.mkdir(parents=True, exist_ok=True)
    _update_latest_symlink(steps_dir, attempt_name)
    return attempt_dir


def _update_latest_symlink(steps_dir: Path, attempt_name: str) -> None:
    """``steps/<step_id>/latest`` を最新 attempt に張り替える（best-effort）。

    symlink 非対応 FS / 権限不足では例外を握り潰して採番を継続する。
    """
    latest = steps_dir / "latest"
    try:
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        os.symlink(attempt_name, latest)
    except OSError as exc:
        _logger.debug("latest symlink update skipped (%s): %s", steps_dir, exc)


def _attempt_number(attempt_dir: Path) -> int:
    """``attempt-NNN`` ディレクトリ名から 1 始まりの attempt 番号を取り出す。"""
    return int(attempt_dir.name.split("-")[1])


def _record_attempt_end(
    *,
    attempt_dir: Path,
    step_id: str,
    attempt: int,
    verdict: Verdict,
    exit_code: int | None,
    signal: str | None,
    started_at: datetime,
    ended_at: datetime,
    step_duration_ms: int,
    session_id: str | None,
    dispatch: str,
    error: str | None,
    cost: CostInfo | None,
    logger: RunLogger,
    state: SessionState,
    synthetic: bool = False,
) -> None:
    """attempt 終了処理（Issue #222）を 1 箇所にまとめる。

    ``result.json`` 書き出し → ``step_end`` ログ → ``record_step``（progress.md
    更新）を行う。正常終了・異常終了（ABORT）の両方から呼ばれる。

    ``result.json`` 書き出しの ``OSError`` は best-effort で握り（元処理を妨げない）、
    異常終了経路では呼び出し側が元例外を優先 re-raise するため crash semantics を
    壊さない。``result.json`` の ``duration_ms`` は ``ended_at - started_at`` の
    wall-clock 値、``step_end`` の ``duration_ms`` は呼び出し側が計測した
    ``step_duration_ms``（step iteration 全体）を用いる。

    Issue #288: ``synthetic`` は except 経路の合成 ABORT record で ``True``、
    dispatch 結果から解決した verdict で ``False``。
    """
    result_duration_ms = int((ended_at - started_at).total_seconds() * 1000)
    result = AttemptResult(
        step_id=step_id,
        attempt=attempt,
        status=verdict.status,
        exit_code=exit_code,
        signal=signal,
        started_at=started_at.isoformat(),
        ended_at=ended_at.isoformat(),
        duration_ms=result_duration_ms,
        session_id=session_id,
        dispatch=dispatch,
        error=error,
        synthetic=synthetic,
    )
    try:
        write_result_json(attempt_dir / RESULT_FILE, result)
    except OSError as exc:
        _logger.warning("result.json write failed (%s): %s", attempt_dir, exc)
    logger.log_step_end(
        step_id,
        verdict,
        step_duration_ms,
        cost,
        attempt=attempt,
        exit_code=exit_code,
        signal=signal,
        dispatch=dispatch,
    )
    state.record_step(step_id, verdict, attempt=attempt, exit_code=exit_code, signal=signal)


@dataclass(frozen=True)
class RunIssueContext:
    """``kaji run`` 起動時に確定する Issue 識別の DTO。

    Phase 3-d preflight § 1 で導入。``input_id`` は user 入力、``canonical_id`` は
    state / artifacts / run log / prompt が共有する正規化済み Issue ID。
    ``issue_ref`` は人間可読な参照（``#153`` / ``local-pc1-3`` など）。

    ``issue_context`` は provider が解決した IssueContext。Phase 3-e の fail-fast
    化以降は必ず非 None（``cmd_run`` 冒頭で `[provider]` 必須化を validate するため）。

    Runner 内部に閉じた DTO で public API として export しない。
    """

    input_id: str
    canonical_id: str
    issue_ref: str
    issue_context: IssueContext


@dataclass(frozen=True)
class _StepOutcome:
    """Result of one step iteration, including synthetic non-dispatch outcomes."""

    verdict: Verdict
    cost: CostInfo | None
    dispatched: bool
    duration_ms: int
    dispatch: str


@dataclass(frozen=True)
class _ExecutionSettings:
    """Resolved dispatch settings for one workflow step."""

    kind: str
    is_script_like: bool
    default_timeout: int
    timeout: int
    workdir: Path


def _dispatch_kind(step: Step, metadata: SkillMetadata | None) -> str:
    """Select the exec, exec_script, or agent dispatch path."""
    if step.exec is not None:
        return "exec"
    if metadata is not None and metadata.exec_script is not None:
        return "exec_script"
    return "agent"


@dataclass
class _StepExecutor:
    """Execute and record one dispatched workflow step attempt."""

    workflow: Workflow
    config: KajiConfig
    provider: IssueProvider
    run_ctx: RunIssueContext
    run_dir: Path
    logger: RunLogger
    state: SessionState
    project_root: Path
    verbose: bool
    resolve_pr_context: Callable[[IssueProvider, str], PRContext | None]

    def execute(
        self,
        step: Step,
        metadata: SkillMetadata | None,
        issue_context: IssueContext,
        started_monotonic: float,
    ) -> _StepOutcome:
        """Dispatch one attempt and persist its verdict and result artifacts."""
        settings = self._resolve_settings(step, metadata)
        pr_context = self.resolve_pr_context(self.provider, issue_context.branch_name)
        session_id = self.state.get_session_id(step.resume) if step.resume else None
        if step.resume and session_id is None:
            raise MissingResumeSessionError(step.id, step.resume)

        attempt_dir = allocate_attempt_dir(self.run_dir, step.id)
        attempt_no = _attempt_number(attempt_dir)
        verdict_path = attempt_dir / "verdict.yaml"
        self._log_step_start(step, settings, session_id, attempt_dir, attempt_no)
        if not settings.workdir.is_dir():
            raise WorkdirNotFoundError(step.id, settings.workdir)

        attempt_started_at_ref: list[datetime] = []
        exit_code: int | None = None
        signal_name: str | None = None
        result_session_id: str | None = None
        try:
            result = self._dispatch(
                step,
                metadata,
                issue_context,
                pr_context,
                session_id,
                attempt_dir,
                verdict_path,
                settings,
                attempt_started_at_ref,
            )
            attempt_started_at = attempt_started_at_ref[0]
            if result.session_id:
                self.state.save_session_id(step.id, result.session_id)
            result_session_id = result.session_id
            exit_code = result.exit_code
            signal_name = result.signal
            verdict = self._resolve_step_verdict(
                step,
                settings,
                result,
                attempt_dir,
                verdict_path,
                attempt_started_at,
            )
        except (
            StepTimeoutError,
            CLIExecutionError,
            CLINotFoundError,
            ScriptExecutionError,
            VerdictNotFound,
            VerdictParseError,
            InvalidVerdictValue,
        ) as exc:
            self._record_dispatch_failure(
                exc=exc,
                step=step,
                attempt_dir=attempt_dir,
                attempt_no=attempt_no,
                attempt_started_at=(attempt_started_at_ref[0] if attempt_started_at_ref else None),
                started_monotonic=started_monotonic,
                session_id=result_session_id or session_id,
                dispatch=settings.kind,
                exit_code=exit_code,
                signal_name=signal_name,
            )
            raise

        duration_ms = int((time.monotonic() - started_monotonic) * 1000)
        _record_attempt_end(
            attempt_dir=attempt_dir,
            step_id=step.id,
            attempt=attempt_no,
            verdict=verdict,
            exit_code=exit_code,
            signal=signal_name,
            started_at=attempt_started_at,
            ended_at=datetime.now(UTC),
            step_duration_ms=duration_ms,
            session_id=result_session_id,
            dispatch=settings.kind,
            error=None,
            cost=result.cost,
            logger=self.logger,
            state=self.state,
            synthetic=False,
        )
        if verdict.status == "ABORT":
            self.logger.log_failure_event(kind="agent_abort", step_id=step.id, synthetic=False)
        return _StepOutcome(verdict, result.cost, True, duration_ms, settings.kind)

    def _resolve_settings(self, step: Step, metadata: SkillMetadata | None) -> _ExecutionSettings:
        """Resolve dispatch kind, timeout, and workdir for a step."""
        kind = _dispatch_kind(step, metadata)
        default_timeout = (
            self.workflow.default_timeout
            if self.workflow.default_timeout is not None
            else self.config.execution.default_timeout
        )
        timeout = step.timeout if step.timeout is not None else default_timeout
        raw_workdir = step.workdir or self.workflow.workdir
        workdir = Path(raw_workdir) if raw_workdir else self.project_root
        return _ExecutionSettings(
            kind, kind in ("exec", "exec_script"), default_timeout, timeout, workdir
        )

    def _log_step_start(
        self,
        step: Step,
        settings: _ExecutionSettings,
        session_id: str | None,
        attempt_dir: Path,
        attempt_no: int,
    ) -> None:
        """Emit structured and human-readable step-start events."""
        self.logger.log_step_start(
            step.id,
            None if settings.is_script_like else step.agent,
            None if settings.is_script_like else step.model,
            None if settings.is_script_like else step.effort,
            session_id,
            attempt=attempt_no,
            dispatch=settings.kind,
        )
        agent_suffix = ""
        if not settings.is_script_like:
            parts = [f"agent={step.agent}"]
            if step.model:
                parts.append(f"model={step.model}")
            if step.effort:
                parts.append(f"effort={step.effort}")
            agent_suffix = " " + " ".join(parts)
        _console.info(
            "step start: %s %s dispatch=%s%s",
            step.id,
            attempt_dir.name,
            settings.kind,
            agent_suffix,
        )

    def _dispatch(
        self,
        step: Step,
        metadata: SkillMetadata | None,
        issue_context: IssueContext,
        pr_context: PRContext | None,
        session_id: str | None,
        attempt_dir: Path,
        verdict_path: Path,
        settings: _ExecutionSettings,
        attempt_started_at_ref: list[datetime],
    ) -> CLIResult:
        """Run the selected backend while retaining runner-module patch lookup."""
        if settings.is_script_like:
            context_env = self._build_context_env(step, issue_context, pr_context, verdict_path)
            attempt_started_at_ref.append(datetime.now(UTC))
            if settings.kind == "exec":
                assert step.exec is not None
                result = execute_exec(
                    step=step,
                    argv=step.exec,
                    env=context_env,
                    workdir=settings.workdir,
                    log_dir=attempt_dir,
                    timeout=settings.timeout,
                    verbose=self.verbose,
                )
            else:
                assert metadata is not None
                assert metadata.exec_script is not None
                result = execute_script(
                    step=step,
                    module=metadata.exec_script,
                    env=context_env,
                    workdir=settings.workdir,
                    log_dir=attempt_dir,
                    timeout=settings.timeout,
                    verbose=self.verbose,
                )
            return result

        prompt = build_prompt(
            step,
            self.run_ctx.canonical_id,
            self.state,
            self.workflow,
            issue_context=issue_context,
            pr_context=pr_context,
            verdict_path=str(verdict_path),
        )
        (attempt_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        attempt_started_at_ref.append(datetime.now(UTC))
        if self.config.execution.agent_runner == "interactive_terminal":
            result = execute_interactive_terminal(
                step=step,
                prompt_path=attempt_dir / "prompt.txt",
                verdict_path=verdict_path,
                workdir=settings.workdir,
                timeout=settings.timeout,
                session_id=session_id,
                close_on_verdict=self.config.execution.interactive_terminal_close_on_verdict,
            )
        else:
            result = execute_cli(
                step=step,
                prompt=prompt,
                workdir=settings.workdir,
                session_id=session_id,
                log_dir=attempt_dir,
                execution_policy=self.workflow.execution_policy,
                verbose=self.verbose,
                default_timeout=settings.default_timeout,
            )
        return result

    def _build_context_env(
        self,
        step: Step,
        issue_context: IssueContext,
        pr_context: PRContext | None,
        verdict_path: Path,
    ) -> dict[str, str]:
        """Build the canonical ``KAJI_*`` environment for script-like steps."""
        context_env = {
            "KAJI_ISSUE_ID": self.run_ctx.canonical_id,
            "KAJI_ISSUE_REF": self.run_ctx.issue_ref,
            "KAJI_STEP_ID": step.id,
            "KAJI_WORKTREE_DIR": issue_context.worktree_dir,
            "KAJI_BRANCH_NAME": issue_context.branch_name,
            "KAJI_PROVIDER_TYPE": issue_context.provider_type,
            "KAJI_DEFAULT_BRANCH": issue_context.default_branch,
            "KAJI_VERDICT_PATH": str(verdict_path),
            "KAJI_GIT_REMOTE": issue_context.git_remote,
        }
        if pr_context is not None:
            context_env["KAJI_PR_ID"] = str(pr_context.pr_id)
            context_env["KAJI_PR_REF"] = pr_context.pr_ref
        return context_env

    def _resolve_step_verdict(
        self,
        step: Step,
        settings: _ExecutionSettings,
        result: CLIResult,
        attempt_dir: Path,
        verdict_path: Path,
        attempt_started_at: datetime,
    ) -> Verdict:
        """Resolve, log, sanitize, and normalize one attempt verdict."""
        valid_statuses = set(step.on.keys())
        if settings.is_script_like:
            formatter = None
        else:
            assert step.agent is not None
            formatter = create_verdict_formatter(
                agent=step.agent,
                valid_statuses=valid_statuses,
                model=step.model,
                workdir=settings.workdir,
            )
        verdict, source, findings = resolve_verdict(
            attempt_dir=attempt_dir,
            full_output=result.full_output,
            valid_statuses=valid_statuses,
            attempt_started_at=attempt_started_at,
            comment_loader=lambda: self.provider.view_issue(self.run_ctx.canonical_id).comments,
            ai_formatter=formatter,
        )
        self.logger.log_verdict_source(step.id, source, attempt_dir.name)
        if findings:
            self.logger.log_verdict_sanitization(step.id, attempt_dir.name, findings)
        _console.info("verdict detected: %s source=%s status=%s", step.id, source, verdict.status)
        if source != "artifact" or findings:
            write_verdict_yaml(verdict_path, verdict)
        return verdict

    def _record_dispatch_failure(
        self,
        *,
        exc: Exception,
        step: Step,
        attempt_dir: Path,
        attempt_no: int,
        attempt_started_at: datetime | None,
        started_monotonic: float,
        session_id: str | None,
        dispatch: str,
        exit_code: int | None,
        signal_name: str | None,
    ) -> None:
        """Record a synthetic ABORT attempt before the original exception escapes."""
        ended_at = datetime.now(UTC)
        exc_returncode = getattr(exc, "returncode", None)
        if exc_returncode is not None:
            exit_code = exc_returncode
            signal_name = derive_signal(exit_code)
        started_at = attempt_started_at if attempt_started_at is not None else ended_at
        verdict_exception = isinstance(
            exc,
            VerdictNotFound | VerdictParseError | InvalidVerdictValue,
        )
        self.logger.log_failure_event(
            kind="verdict_exception" if verdict_exception else "dispatch_exception",
            step_id=step.id,
            exception_type=type(exc).__name__,
            synthetic=True,
        )
        abort_verdict = Verdict(
            status="ABORT",
            reason="step aborted without a usable verdict",
            evidence=str(exc)[:500],
            suggestion=(
                f"Inspect {attempt_dir.name}/result.json and console.log; "
                "re-run after addressing the abort cause."
            ),
        )
        _record_attempt_end(
            attempt_dir=attempt_dir,
            step_id=step.id,
            attempt=attempt_no,
            verdict=abort_verdict,
            exit_code=exit_code,
            signal=signal_name,
            started_at=started_at,
            ended_at=ended_at,
            step_duration_ms=int((time.monotonic() - started_monotonic) * 1000),
            session_id=session_id,
            dispatch=dispatch,
            error=f"{type(exc).__name__}: {exc}",
            cost=None,
            logger=self.logger,
            state=self.state,
            synthetic=True,
        )


@dataclass
class WorkflowRunner:
    """ワークフロー実行エンジン。"""

    workflow: Workflow
    issue_number: str
    project_root: Path
    artifacts_dir: Path
    config: KajiConfig
    from_step: str | None = None
    single_step: str | None = None
    before_step: str | None = None
    reset_cycle: bool = False
    verbose: bool = True
    # Issue #288: recovery chain identity。handler が child run 起動時に付与する。
    # ``recovery_root`` があれば当該 run は recovery child であり、その failure handler は
    # budget guard で無条件 ``exhausted`` になる。
    recovery_root: str | None = None
    recovery_parent: str | None = None
    # Phase 3-d preflight: ``run()`` 完了後に外部から参照される canonical id。
    # ``cmd_run()`` の成功表示などが利用する。``run()`` 起動前は ``None``。
    canonical_issue_id: str | None = field(default=None, init=False)
    canonical_issue_ref: str | None = field(default=None, init=False)
    # Issue #288: run_dir 採番後に確定する。``cmd_run`` の failure handler は
    # この値が非 None の場合のみ triage を起動する（run_dir 作成前の失敗は対象外）。
    last_run_dir: Path | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        # int / その他から str へ正規化（既存呼び出し互換のため）
        self.issue_number = str(self.issue_number)

    def _resolve_issue_context(self) -> IssueContext:
        """provider 経由で IssueContext を解決する。

        Phase 3-e で fail-fast 化。`cmd_run` 冒頭で `get_provider` の早期
        validation を済ませているため、ここで `config.provider is None` には
        到達しない（型 checker のため assert を残す）。

        ``normalize_id`` を経由して provider 内部 ID に正規化（``1`` /
        ``pc1-1`` / ``local-pc1-1`` / ``gh:N`` を一貫して扱う）してから解決。
        失敗は ``IssueContextResolutionError`` で raise し、agent 起動前に
        exit する（machine_id 不足 / Issue 不在 / frontmatter 不備で半端な
        context のまま Skill 起動を許さない）。
        """
        try:
            provider = get_provider(self.config)
        except ValueError as exc:
            # 明示 provider 設定の不整合（machine_id 不在 / repo 不在等）
            raise IssueContextResolutionError(
                issue_input=self.issue_number,
                provider_type=(self.config.provider.type if self.config.provider else "unset"),
                cause=exc,
            ) from exc

        assert self.config.provider is not None  # for type checker
        provider_type = self.config.provider.type
        machine_id = self.config.provider.local.machine_id if provider_type == "local" else None
        try:
            rid = normalize_id(
                self.issue_number,
                provider_name=provider_type,
                machine_id=machine_id,
            )
        except ValueError as exc:
            raise IssueContextResolutionError(
                issue_input=self.issue_number,
                provider_type=provider_type,
                cause=exc,
            ) from exc

        if rid.kind == "remote_cache":
            # `provider=local` 配下で `gh:N` を `kaji run` 対象にするのは
            # write 系 Skill が走るため意味的に矛盾。明示的に拒否する。
            raise IssueContextResolutionError(
                issue_input=self.issue_number,
                provider_type=provider_type,
                cause=ValueError(
                    "remote_cache (gh:N) issues are read-only and cannot be "
                    "the target of `kaji run`. Use a local issue id "
                    "(e.g. local-<machine>-<n>) or run under provider.type='github'."
                ),
            )

        # provider 別に内部 ID で resolve
        try:
            if isinstance(provider, LocalProvider):
                return provider.resolve_issue_context(rid.value)
            # GitHubProvider: rid.kind == "github"、value は数値文字列
            return provider.resolve_issue_context(rid.value)
        except Exception as exc:
            raise IssueContextResolutionError(
                issue_input=self.issue_number,
                provider_type=provider_type,
                cause=exc,
            ) from exc

    def _resolve_run_issue_context(self) -> RunIssueContext:
        """``kaji run`` 起動時に Issue 識別を 1 度だけ決定する。

        Phase 3-d preflight § 1: state / run log / prompt / success summary が
        共有する canonical id を確定する。Phase 3-e 以降は ``[provider]`` が必須の
        ため、provider 経由 IssueContext の ``issue_id`` / ``issue_ref`` を
        canonical 値として直接採用する（Phase 2-B 互換 fallback は廃止済）。

        legacy raw-id artifacts directory が残っていた場合（例: 補正前の
        ``kaji run ... 1`` で作られた ``.kaji-artifacts/1/``）は WARN を出すが
        自動 migration はしない。raw / canonical の対応は provider config /
        machine_id に依存し、agent が暗黙に move / copy すると別 Issue の state
        を混ぜる事故が起きうるため。
        """
        ctx = self._resolve_issue_context()
        input_id = self.issue_number
        canonical_id = ctx.issue_id
        issue_ref = ctx.issue_ref
        if input_id != canonical_id:
            self._warn_legacy_artifacts(input_id, canonical_id)
        return RunIssueContext(
            input_id=input_id,
            canonical_id=canonical_id,
            issue_ref=issue_ref,
            issue_context=ctx,
        )

    def _resolve_pr_context_safe(
        self, provider: IssueProvider, branch_name: str
    ) -> PRContext | None:
        """provider から `PRContext` を解決。known provider error のみ WARN + None。

        catch する範囲は ``GitHubProviderError`` のみ。
        それ以外（``AttributeError`` / ``TypeError`` 等の実装バグ、
        ``KeyboardInterrupt`` 等の signal 系）は raise を継承する。
        ``docs/reference/python/error-handling.md`` § 基本原則 1「握り潰し禁止」
        「広すぎる catch を避ける」遵守。
        """
        try:
            return provider.resolve_pr_context(branch_name)
        except GitHubProviderError as exc:
            sys.stderr.write(
                f"WARNING: resolve_pr_context for branch {branch_name!r} failed: {exc}\n"
                f"  pr_id / pr_ref will not be auto-injected; "
                f"skill must resolve manually.\n"
            )
            return None

    def _warn_legacy_artifacts(self, raw_id: str, canonical_id: str) -> None:
        """raw-id 側の artifacts directory が残っていれば 1 度 WARN を出す。

        ``SessionState.load_or_create`` は fallback 探索しないため、user に手動
        移動を促す（phase3d-preflight-design § 1 既存 state / artifacts の扱い）。
        """
        legacy_dir = self.artifacts_dir / raw_id
        if not legacy_dir.exists():
            return
        canonical_dir = self.artifacts_dir / canonical_id
        sys.stderr.write(
            f"WARNING: legacy artifact directory exists for raw issue id {raw_id!r}:\n"
            f"  {legacy_dir}\n"
            f"This run will use canonical issue id {canonical_id!r}:\n"
            f"  {canonical_dir}\n"
            f"If you need to resume the old session, move the directory manually "
            f"after confirming it belongs to the same issue.\n"
        )

    def _validate_cycle_reset(self) -> CycleDefinition | None:
        """`--reset-cycle` の前提を検証し、リセット対象 cycle を返す。

        workflow 定義のみを参照する純粋な検証で、state / fs / provider に
        触れない。誤用時に state を一切書き換えない保証はこの分離で成立する
        （state.py への到達より前に呼ぶこと。design § 制約・前提条件）。
        """
        if not self.reset_cycle:
            return None
        if not self.from_step:
            # cmd_run でも弾くが、WorkflowRunner の直接利用に対する防御
            raise WorkflowValidationError("--reset-cycle requires --from <step>")
        if not self.workflow.find_step(self.from_step):
            raise WorkflowValidationError(f"Step '{self.from_step}' not found")
        cycle = self.workflow.find_cycle_for_step(self.from_step)
        if cycle is None:
            raise WorkflowValidationError(
                f"Step '{self.from_step}' does not belong to any cycle (--reset-cycle)"
            )
        return cycle

    def _apply_cycle_reset(
        self, cycle: CycleDefinition | None, state: SessionState, logger: RunLogger
    ) -> None:
        """検証済み cycle の反復回数を 0 に戻す（検証はしない）。"""
        if cycle is None:
            return
        previous = state.cycle_iterations(cycle.name)
        state.reset_cycle(cycle.name)
        logger.log_cycle_reset(cycle.name, previous)
        _console.info("cycle reset: %s (was %d)", cycle.name, previous)

    def _collect_skill_metadata(self) -> dict[str, SkillMetadata | None]:
        """Validate skill availability and return per-step execution metadata."""
        skill_metadata: dict[str, SkillMetadata | None] = {}
        for step in self.workflow.steps:
            if step.exec is not None:
                skill_metadata[step.id] = None
                continue
            assert step.skill is not None
            validate_skill_exists(step.skill, self.project_root, self.config.paths.skill_dir)
            metadata = load_skill_metadata(
                step.skill,
                self.project_root,
                self.config.paths.skill_dir,
            )
            skill_metadata[step.id] = metadata
            if step.agent is None and metadata.exec_script is None:
                raise WorkflowValidationError(
                    f"Step '{step.id}' omits 'agent' but skill '{step.skill}' does "
                    "not declare 'exec_script' in its frontmatter; either set "
                    "'agent' on the step or add 'exec_script' to the skill"
                )
            if metadata.exec_script is not None and (
                step.agent is not None or step.model is not None or step.effort is not None
            ):
                sys.stderr.write(
                    f"WARNING: Step '{step.id}' uses exec_script skill "
                    f"'{step.skill}'; 'agent' / 'model' / 'effort' are ignored.\n"
                )
        return skill_metadata

    def _validate_before_step(self) -> None:
        """Validate a pre-dispatch barrier without touching run artifacts."""
        if self.before_step and self.before_step != "end":
            if not self.workflow.find_step(self.before_step):
                raise WorkflowValidationError(f"Step '{self.before_step}' not found (--before)")

    def _load_session_state(
        self, run_ctx: RunIssueContext
    ) -> tuple[SessionState, IssueContext, Verdict | None]:
        """Load state, backfill a worktree, and return any ambiguous-worktree ABORT."""
        issue_context = run_ctx.issue_context
        state = SessionState.load_or_create(run_ctx.canonical_id, self.artifacts_dir)
        ambiguous_abort: Verdict | None = None
        if state.worktree_dir is None:
            try:
                discovered = discover_existing_worktree(
                    self.project_root,
                    run_ctx.canonical_id,
                    self.config.paths.worktree_prefix,
                )
            except AmbiguousWorktreeError as exc:
                candidates = "\n  ".join(f"{path} ({branch})" for path, branch in exc.candidates)
                sys.stderr.write(
                    f"ERROR: multiple worktrees match issue {run_ctx.canonical_id!r}:\n"
                    f"  {candidates}\n"
                    "  Resolve with `git worktree remove <path>` and re-run.\n"
                )
                ambiguous_abort = Verdict(
                    status="ABORT",
                    reason=f"multiple worktrees match issue {run_ctx.canonical_id}",
                    evidence="candidates:\n  " + candidates,
                    suggestion=(
                        "Resolve the conflict with `git worktree remove <path>` and re-run."
                    ),
                )
                discovered = None
            if discovered is not None:
                state.capture_worktree(discovered[0], discovered[1])
        if state.worktree_dir and state.branch_name:
            issue_context = replace(
                issue_context,
                worktree_dir=state.worktree_dir,
                branch_name=state.branch_name,
                branch_prefix=state.branch_name.split("/", 1)[0],
            )
        return state, issue_context, ambiguous_abort

    def _resolve_start_step(self) -> Step:
        """Resolve and validate the first step before allocating run artifacts."""
        if self.single_step:
            current_step = self.workflow.find_step(self.single_step)
            if current_step is None:
                raise WorkflowValidationError(f"Step '{self.single_step}' not found")
            return current_step
        if self.from_step:
            current_step = self.workflow.find_step(self.from_step)
            if current_step is None:
                raise WorkflowValidationError(f"Step '{self.from_step}' not found")
            return current_step
        return self.workflow.find_start_step()

    def _emit_ambiguous_worktree_abort(
        self,
        verdict: Verdict,
        state: SessionState,
        logger: RunLogger,
        workflow_start: float,
    ) -> SessionState:
        """Persist and emit the pre-loop synthetic ambiguous-worktree ABORT."""
        sys.stdout.write(
            "---VERDICT---\n"
            "status: ABORT\n"
            f"reason: |\n  {verdict.reason}\n"
            f"evidence: |\n  {verdict.evidence}\n"
            f"suggestion: |\n  {verdict.suggestion}\n"
            "---END_VERDICT---\n"
        )
        logger.log_failure_event(kind="ambiguous_worktree", synthetic=True)
        _console.error("workflow abort: %s", verdict.reason)
        state.last_transition_verdict = verdict
        state._persist()
        duration_ms = int((time.monotonic() - workflow_start) * 1000)
        logger.log_workflow_end(
            "ABORT",
            state.cycle_counts,
            total_duration_ms=duration_ms,
            total_cost=None,
            error=None,
        )
        _console.info("workflow end: status=ABORT duration=%dms", duration_ms)
        return state

    def run(self) -> SessionState:
        """ワークフローを実行し、最終状態を返す。

        Returns:
            SessionState: 実行後のセッション状態

        Raises:
            WorkflowValidationError: ワークフロー定義エラー
            MissingResumeSessionError: resume 先のセッション ID が見つからない
            InvalidTransition: verdict に対応する遷移先がない
        """
        skill_metadata = self._collect_skill_metadata()
        validate_workflow(self.workflow)
        self._validate_before_step()
        cycle_reset_target = self._validate_cycle_reset()

        # 2. canonical issue id を確定し、以降の state / run log / prompt /
        #    success summary に一貫適用する（phase3d-preflight § 1）。
        run_ctx = self._resolve_run_issue_context()
        self.canonical_issue_id = run_ctx.canonical_id
        self.canonical_issue_ref = run_ctx.issue_ref
        issue_context = run_ctx.issue_context

        # PR context 注入用に provider を 1 度だけ構築する（step ごとに再構築すると
        # subprocess hit が無駄に増える）。``cmd_run`` 冒頭で `[provider]` を
        # 必須化済みのため、ここで `get_provider` が再度失敗することは想定外。
        provider = get_provider(self.config)

        state, issue_context, ambiguous_abort = self._load_session_state(run_ctx)
        current_step: Step | None = self._resolve_start_step()

        # 5. run ログディレクトリを作成（canonical id ベース）
        run_dir = allocate_run_dir(self.artifacts_dir / run_ctx.canonical_id / "runs")
        self.last_run_dir = run_dir
        # Issue #288: recovery child は起動直後に chain identity を artifact 化する。
        # 親 handler はこれを見て child run を特定し、child 自身の handler は
        # budget guard の入力にする。
        if self.recovery_root:
            write_recovery_chain(
                run_dir / RECOVERY_CHAIN_FILE,
                root_run_id=self.recovery_root,
                parent_run_id=self.recovery_parent or self.recovery_root,
            )
        logger = RunLogger(log_path=run_dir / "run.log")
        logger.log_workflow_start(run_ctx.canonical_id, self.workflow.name)
        _console.info("workflow start: %s issue %s", self.workflow.name, run_ctx.issue_ref)

        total_cost = 0.0
        workflow_start = time.monotonic()
        end_status = "COMPLETE"
        end_error: str | None = None
        last_verdict: Verdict | None = None
        barrier_hit = False
        step_dispatched = False

        if ambiguous_abort is not None:
            return self._emit_ambiguous_worktree_abort(
                ambiguous_abort,
                state,
                logger,
                workflow_start,
            )

        # 6. --reset-cycle の適用（state / logger が確定済み、メインループ前）
        self._apply_cycle_reset(cycle_reset_target, state, logger)

        executor = _StepExecutor(
            workflow=self.workflow,
            config=self.config,
            provider=provider,
            run_ctx=run_ctx,
            run_dir=run_dir,
            logger=logger,
            state=state,
            project_root=self.project_root,
            verbose=self.verbose,
            resolve_pr_context=self._resolve_pr_context_safe,
        )

        # 7. メインループ
        try:
            while current_step and current_step.id != "end":
                # --before barrier: dispatch 直前で停止（開始 step / --from 開始 step も含む）
                if self.before_step and current_step.id == self.before_step:
                    logger.log_barrier_hit(self.before_step)
                    _console.info("barrier hit: %s", self.before_step)
                    barrier_hit = True
                    break

                # Issue #218: physical worktree が確定した瞬間に state へ capture。
                # 同一 run 内で issue-start が新規作成した worktree を捕捉する経路
                # （backfill は旧 state file の救済、こちらは新規 run の確定）。
                if state.worktree_dir is None and Path(issue_context.worktree_dir).is_dir():
                    state.capture_worktree(issue_context.worktree_dir, issue_context.branch_name)
                    # capture 後は state を正本として context を override
                    prefix = issue_context.branch_name.split("/", 1)[0]
                    issue_context = replace(
                        issue_context,
                        branch_prefix=prefix,
                    )

                iteration_started = time.monotonic()
                cycle = self.workflow.find_cycle_for_step(current_step.id)
                step_metadata = skill_metadata[current_step.id]
                dispatch_kind = _dispatch_kind(current_step, step_metadata)

                # サイクル上限チェック
                if cycle and state.cycle_iterations(cycle.name) >= cycle.max_iterations:
                    verdict = Verdict(
                        status=cycle.on_exhaust,
                        reason=f"Cycle '{cycle.name}' exhausted",
                        evidence=f"{cycle.max_iterations} iterations reached",
                        suggestion="手動で確認してください",
                    )
                    logger.log_failure_event(
                        kind="cycle_exhausted",
                        step_id=current_step.id,
                        cycle_name=cycle.name,
                        synthetic=True,
                    )
                    _console.info("cycle exhausted: %s", cycle.name)
                    outcome = _StepOutcome(
                        verdict=verdict,
                        cost=None,
                        dispatched=False,
                        duration_ms=int((time.monotonic() - iteration_started) * 1000),
                        dispatch=dispatch_kind,
                    )
                else:
                    outcome = executor.execute(
                        current_step,
                        step_metadata,
                        issue_context,
                        iteration_started,
                    )

                verdict = outcome.verdict
                duration_ms = outcome.duration_ms
                if not outcome.dispatched:
                    logger.log_step_end(
                        current_step.id,
                        verdict,
                        duration_ms,
                        outcome.cost,
                        dispatch=outcome.dispatch,
                    )
                    state.record_step(current_step.id, verdict)
                last_verdict = verdict
                step_dispatched = True

                if outcome.cost and outcome.cost.usd:
                    total_cost += outcome.cost.usd

                # サイクルカウント
                if cycle and current_step.id == cycle.loop[-1] and verdict.status == "RETRY":
                    state.increment_cycle(cycle.name)
                    logger.log_cycle_iteration(
                        cycle.name,
                        state.cycle_iterations(cycle.name),
                        cycle.max_iterations,
                    )
                    _console.info(
                        "cycle iteration: %s %d/%d",
                        cycle.name,
                        state.cycle_iterations(cycle.name),
                        cycle.max_iterations,
                    )

                # 次のステップを決定
                if self.single_step:
                    # Issue #235: --step は遷移しないので next=end として step end を出す。
                    _console.info(
                        "step end: %s status=%s duration=%dms next=end",
                        current_step.id,
                        verdict.status,
                        duration_ms,
                    )
                    break

                next_step_id = current_step.on.get(verdict.status)
                if next_step_id is None:
                    raise InvalidTransition(current_step.id, verdict.status)

                # Issue #235: next step 解決後に step end progress を出す（next を含めるため）。
                _console.info(
                    "step end: %s status=%s duration=%dms next=%s",
                    current_step.id,
                    verdict.status,
                    duration_ms,
                    next_step_id,
                )

                # --before barrier: dispatch 直前で停止
                if self.before_step and next_step_id == self.before_step:
                    logger.log_barrier_hit(self.before_step)
                    _console.info("barrier hit: %s", self.before_step)
                    barrier_hit = True
                    break

                current_step = self.workflow.find_step(next_step_id)

            # pre-dispatch barrier で停止した場合、前回 run の stale verdict を抑止する
            # （cmd_run が誤って ABORT 報告するのを防ぐ）
            if barrier_hit and not step_dispatched and state.last_transition_verdict is not None:
                state.last_transition_verdict = None
                state._persist()

            # --before 未到達検知（"end" は WARN 対象外、ABORT 終了も自然完了ではないので対象外）
            naturally_completed = last_verdict is None or last_verdict.status != "ABORT"
            if (
                self.before_step
                and not barrier_hit
                and self.before_step != "end"
                and naturally_completed
            ):
                logger.log_barrier_missed(self.before_step)
                _console.warning("barrier missed: %s", self.before_step)
                print(
                    f"WARN: stop point '{self.before_step}' was never reached; "
                    "workflow completed naturally",
                    file=sys.stderr,
                )

            # 正常終了時のステータス判定
            if last_verdict and last_verdict.status == "ABORT":
                end_status = "ABORT"
        except Exception as exc:
            end_status = "ERROR"
            end_error = f"{type(exc).__name__}: {exc}"
            _console.error("workflow error: %s", end_error)
            raise
        finally:
            total_duration_ms = int((time.monotonic() - workflow_start) * 1000)
            logger.log_workflow_end(
                end_status,
                state.cycle_counts,
                total_duration_ms=total_duration_ms,
                total_cost=total_cost if total_cost > 0 else None,
                error=end_error,
            )
            _console.info("workflow end: status=%s duration=%dms", end_status, total_duration_ms)
        return state
