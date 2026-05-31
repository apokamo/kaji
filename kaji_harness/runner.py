"""Workflow execution runner for kaji_harness.

Main loop that executes workflow steps sequentially,
manages state transitions, and handles cycle limits.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from .cli import execute_cli
from .config import KajiConfig
from .errors import (
    InvalidTransition,
    IssueContextResolutionError,
    MissingResumeSessionError,
    WorkdirNotFoundError,
    WorkflowValidationError,
)
from .logger import RunLogger
from .models import CostInfo, Verdict, Workflow
from .prompt import build_prompt
from .providers import IssueContext, IssueProvider, PRContext, get_provider, normalize_id
from .providers.github import GitHubProviderError
from .providers.local import LocalProvider
from .script_exec import execute_script
from .skill import SkillMetadata, load_skill_metadata, validate_skill_exists
from .state import SessionState
from .verdict import create_verdict_formatter, parse_verdict
from .workflow import validate_workflow
from .worktree_discovery import AmbiguousWorktreeError, discover_existing_worktree


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
    verbose: bool = True
    # Phase 3-d preflight: ``run()`` 完了後に外部から参照される canonical id。
    # ``cmd_run()`` の成功表示などが利用する。``run()`` 起動前は ``None``。
    canonical_issue_id: str | None = field(default=None, init=False)
    canonical_issue_ref: str | None = field(default=None, init=False)

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

    def run(self) -> SessionState:
        """ワークフローを実行し、最終状態を返す。

        Returns:
            SessionState: 実行後のセッション状態

        Raises:
            WorkflowValidationError: ワークフロー定義エラー
            MissingResumeSessionError: resume 先のセッション ID が見つからない
            InvalidTransition: verdict に対応する遷移先がない
        """
        execution_policy = self.workflow.execution_policy

        # 0. 全ステップのスキル存在を事前検証 + skill metadata 整合チェック (L2)
        skill_metadata: dict[str, SkillMetadata] = {}
        for step in self.workflow.steps:
            validate_skill_exists(step.skill, self.project_root, self.config.paths.skill_dir)
            metadata = load_skill_metadata(
                step.skill, self.project_root, self.config.paths.skill_dir
            )
            skill_metadata[step.id] = metadata
            # L2 preflight: agent 省略の妥当性は metadata 依存
            if step.agent is None and metadata.exec_script is None:
                raise WorkflowValidationError(
                    f"Step '{step.id}' omits 'agent' but skill '{step.skill}' does "
                    "not declare 'exec_script' in its frontmatter; either set "
                    "'agent' on the step or add 'exec_script' to the skill"
                )
            # exec_script 経路では agent / model / effort は無視される（warning）
            if metadata.exec_script is not None and (
                step.agent is not None or step.model is not None or step.effort is not None
            ):
                sys.stderr.write(
                    f"WARNING: Step '{step.id}' uses exec_script skill "
                    f"'{step.skill}'; 'agent' / 'model' / 'effort' are ignored.\n"
                )

        # 1. ワークフロー定義のバリデーション
        validate_workflow(self.workflow)

        # 1.5. --before 指定 step の存在検証（"end" は許容）
        if self.before_step and self.before_step != "end":
            if not self.workflow.find_step(self.before_step):
                raise WorkflowValidationError(f"Step '{self.before_step}' not found (--before)")

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

        # 3. issue-scoped な状態をロード（canonical id ベース）
        state = SessionState.load_or_create(run_ctx.canonical_id, self.artifacts_dir)

        # Issue #218: backfill → override 経路。
        # 旧 kaji 版で作られた state file には worktree_dir / branch_name が無いため、
        # ``git worktree list --porcelain`` から既存 worktree を発見して state に
        # 焼き込み、以降は state を正本として label 由来 path を override する。
        ambiguous_abort: Verdict | None = None
        if state.worktree_dir is None:
            try:
                discovered = discover_existing_worktree(
                    self.project_root,
                    run_ctx.canonical_id,
                    self.config.paths.worktree_prefix,
                )
            except AmbiguousWorktreeError as exc:
                cand_str = "\n  ".join(f"{p} ({b})" for p, b in exc.candidates)
                sys.stderr.write(
                    f"ERROR: multiple worktrees match issue {run_ctx.canonical_id!r}:\n"
                    f"  {cand_str}\n"
                    f"  Resolve with `git worktree remove <path>` and re-run.\n"
                )
                ambiguous_abort = Verdict(
                    status="ABORT",
                    reason=f"multiple worktrees match issue {run_ctx.canonical_id}",
                    evidence="candidates:\n  " + cand_str,
                    suggestion="Resolve the conflict with `git worktree remove <path>` and re-run.",
                )
                discovered = None
            if discovered is not None:
                state.capture_worktree(discovered[0], discovered[1])

        if state.worktree_dir and state.branch_name:
            prefix = state.branch_name.split("/", 1)[0]
            issue_context = replace(
                issue_context,
                worktree_dir=state.worktree_dir,
                branch_name=state.branch_name,
                branch_prefix=prefix,
            )

        # 4. run ログディレクトリを作成（canonical id ベース）
        run_dir = (
            self.artifacts_dir
            / run_ctx.canonical_id
            / "runs"
            / datetime.now().strftime("%y%m%d%H%M")
        )
        run_dir.mkdir(parents=True, exist_ok=True)
        logger = RunLogger(log_path=run_dir / "run.log")
        logger.log_workflow_start(run_ctx.canonical_id, self.workflow.name)

        # 4. 開始ステップの決定
        if self.single_step:
            current_step = self.workflow.find_step(self.single_step)
            if not current_step:
                raise WorkflowValidationError(f"Step '{self.single_step}' not found")
        elif self.from_step:
            current_step = self.workflow.find_step(self.from_step)
            if not current_step:
                raise WorkflowValidationError(f"Step '{self.from_step}' not found")
        else:
            current_step = self.workflow.find_start_step()

        total_cost = 0.0
        workflow_start = time.monotonic()
        end_status = "COMPLETE"
        end_error: str | None = None
        last_verdict: Verdict | None = None
        barrier_hit = False
        step_dispatched = False

        # Issue #218: backfill 段階で多重候補 ABORT を検出していたら、
        # main loop に入らず ABORT verdict を emit して run 全体を停止する。
        if ambiguous_abort is not None:
            sys.stdout.write(
                "---VERDICT---\n"
                "status: ABORT\n"
                f"reason: |\n  {ambiguous_abort.reason}\n"
                f"evidence: |\n  {ambiguous_abort.evidence}\n"
                f"suggestion: |\n  {ambiguous_abort.suggestion}\n"
                "---END_VERDICT---\n"
            )
            last_verdict = ambiguous_abort
            end_status = "ABORT"
            total_duration_ms = int((time.monotonic() - workflow_start) * 1000)
            logger.log_workflow_end(
                end_status,
                state.cycle_counts,
                total_duration_ms=total_duration_ms,
                total_cost=total_cost if total_cost > 0 else None,
                error=None,
            )
            return state

        # 5. メインループ
        try:
            while current_step and current_step.id != "end":
                # --before barrier: dispatch 直前で停止（開始 step / --from 開始 step も含む）
                if self.before_step and current_step.id == self.before_step:
                    logger.log_barrier_hit(self.before_step)
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

                start_time = time.monotonic()
                cycle = self.workflow.find_cycle_for_step(current_step.id)
                step_metadata = skill_metadata[current_step.id]
                is_exec_script = step_metadata.exec_script is not None

                # サイクル上限チェック
                if cycle and state.cycle_iterations(cycle.name) >= cycle.max_iterations:
                    verdict = Verdict(
                        status=cycle.on_exhaust,
                        reason=f"Cycle '{cycle.name}' exhausted",
                        evidence=f"{cycle.max_iterations} iterations reached",
                        suggestion="手動で確認してください",
                    )
                    cost: CostInfo | None = None
                else:
                    metadata = step_metadata

                    # PR context は step ごとに最新状態を見る（`i-pr` step 実行後など、
                    # workflow 中に MR が新規作成されるケースを反映するため）。
                    pr_context = self._resolve_pr_context_safe(provider, issue_context.branch_name)

                    # セッション ID の取得（exec_script では使わない）
                    session_id = (
                        state.get_session_id(current_step.resume) if current_step.resume else None
                    )
                    if current_step.resume and session_id is None:
                        raise MissingResumeSessionError(current_step.id, current_step.resume)

                    # ログディレクトリ
                    step_log_dir = run_dir / current_step.id
                    step_log_dir.mkdir(parents=True, exist_ok=True)

                    # exec_script では agent / model / effort を null として記録する
                    # （設計書 § 副作用: ignored fields を null 化して経路判別を明示）。
                    logger.log_step_start(
                        current_step.id,
                        None if is_exec_script else current_step.agent,
                        None if is_exec_script else current_step.model,
                        None if is_exec_script else current_step.effort,
                        session_id,
                        dispatch="exec_script" if is_exec_script else "agent",
                    )

                    # タイムアウト解決: workflow.default_timeout → config.execution.default_timeout
                    default_timeout = (
                        self.workflow.default_timeout
                        if self.workflow.default_timeout is not None
                        else self.config.execution.default_timeout
                    )
                    resolved_timeout = (
                        current_step.timeout
                        if current_step.timeout is not None
                        else default_timeout
                    )

                    # workdir 解決: step.workdir → workflow.workdir → project_root
                    raw_workdir = current_step.workdir or self.workflow.workdir
                    effective_workdir = Path(raw_workdir) if raw_workdir else self.project_root
                    if not effective_workdir.is_dir():
                        raise WorkdirNotFoundError(current_step.id, effective_workdir)

                    if is_exec_script:
                        assert metadata.exec_script is not None
                        # context env 注入。canonical_id / step_id / worktree
                        # 関連を script に渡す。
                        context_env: dict[str, str] = {
                            "KAJI_ISSUE_ID": run_ctx.canonical_id,
                            "KAJI_ISSUE_REF": run_ctx.issue_ref,
                            "KAJI_STEP_ID": current_step.id,
                            "KAJI_WORKTREE_DIR": issue_context.worktree_dir,
                            "KAJI_BRANCH_NAME": issue_context.branch_name,
                            "KAJI_PROVIDER_TYPE": issue_context.provider_type,
                            "KAJI_DEFAULT_BRANCH": issue_context.default_branch,
                        }
                        context_env["KAJI_GIT_REMOTE"] = issue_context.git_remote
                        if pr_context is not None:
                            context_env["KAJI_PR_ID"] = str(pr_context.pr_id)
                            context_env["KAJI_PR_REF"] = pr_context.pr_ref

                        result = execute_script(
                            step=current_step,
                            module=metadata.exec_script,
                            env=context_env,
                            workdir=effective_workdir,
                            log_dir=step_log_dir,
                            timeout=resolved_timeout,
                            verbose=self.verbose,
                        )
                    else:
                        # プロンプト構築（canonical id を渡す）
                        prompt = build_prompt(
                            current_step,
                            run_ctx.canonical_id,
                            state,
                            self.workflow,
                            issue_context=issue_context,
                            pr_context=pr_context,
                        )

                        # CLI 実行
                        result = execute_cli(
                            step=current_step,
                            prompt=prompt,
                            workdir=effective_workdir,
                            session_id=session_id,
                            log_dir=step_log_dir,
                            execution_policy=execution_policy,
                            verbose=self.verbose,
                            default_timeout=default_timeout,
                        )

                    # セッション ID を保存
                    if result.session_id:
                        state.save_session_id(current_step.id, result.session_id)
                    cost = result.cost

                    # verdict をパース
                    valid = set(current_step.on.keys())
                    if is_exec_script:
                        # exec_script 経路では AI formatter fallback を呼ばない
                        # (fabrication 防止 + 決定論性維持)
                        formatter = None
                    else:
                        assert current_step.agent is not None  # L2 で確定
                        formatter = create_verdict_formatter(
                            agent=current_step.agent,
                            valid_statuses=valid,
                            model=current_step.model,
                            workdir=effective_workdir,
                        )
                    verdict = parse_verdict(
                        result.full_output,
                        valid_statuses=valid,
                        ai_formatter=formatter,
                    )

                # ログ記録 + 状態更新
                duration_ms = int((time.monotonic() - start_time) * 1000)
                logger.log_step_end(
                    current_step.id,
                    verdict,
                    duration_ms,
                    cost,
                    dispatch="exec_script" if is_exec_script else "agent",
                )
                state.record_step(current_step.id, verdict)
                last_verdict = verdict
                step_dispatched = True

                if cost and cost.usd:
                    total_cost += cost.usd

                # サイクルカウント
                if cycle and current_step.id == cycle.loop[-1] and verdict.status == "RETRY":
                    state.increment_cycle(cycle.name)
                    logger.log_cycle_iteration(
                        cycle.name,
                        state.cycle_iterations(cycle.name),
                        cycle.max_iterations,
                    )

                # 次のステップを決定
                if self.single_step:
                    break

                next_step_id = current_step.on.get(verdict.status)
                if next_step_id is None:
                    raise InvalidTransition(current_step.id, verdict.status)

                # --before barrier: dispatch 直前で停止
                if self.before_step and next_step_id == self.before_step:
                    logger.log_barrier_hit(self.before_step)
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
        return state
