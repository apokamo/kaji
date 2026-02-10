"""Bugfix Agent v5 Orchestrator

Note: Python 3.11+ required
"""

import time
from collections.abc import Callable

from bugfix_agent.agent_context import (
    AgentContext,
    create_default_context,
)

# Phase 3: Import CLI utilities, context, and tools
from bugfix_agent.cli import format_jsonl_line, run_cli_streaming

# Phase 2: Import from separated modules
from bugfix_agent.config import get_config_value
from bugfix_agent.context import build_context
from bugfix_agent.errors import (
    AgentAbortError,
    InvalidVerdictValueError,  # Issue #292: 不正値は即 raise
    LoopLimitExceeded,
    ToolError,
    VerdictParseError,
    check_tool_result,  # Re-exported for backward compatibility
)
from bugfix_agent.github import post_issue_comment

# Phase 5: Import state handlers
from bugfix_agent.handlers import (
    handle_detail_design,
    handle_detail_design_review,
    handle_implement,
    handle_implement_review,
    handle_init,
    handle_investigate,
    handle_investigate_review,
    handle_pr_create,
)
from bugfix_agent.prompts import load_prompt
from bugfix_agent.run_logger import RunLogger

# Phase 4: Import state machine, workflow, and context
from bugfix_agent.state import (
    ExecutionConfig,
    ExecutionMode,
    SessionState,
    State,
    infer_result_label,
)
from bugfix_agent.tools import (
    AIToolProtocol,
    ClaudeTool,
    CodexTool,
    GeminiTool,
    MockTool,
)
from bugfix_agent.verdict import (
    # Constants (Issue #292)
    AI_FORMATTER_MAX_INPUT_CHARS,
    FORMATTER_PROMPT,
    RELAXED_PATTERNS,
    # Types
    AIFormatterFunc,
    ReviewResult,
    Verdict,
    _extract_verdict_field,  # Re-export for backward compatibility
    # Functions
    create_ai_formatter,  # Issue #292: Step 3 用 formatter 生成
    handle_abort_verdict,
    parse_verdict,
)

# Note: All components imported from bugfix_agent package (Phase 2-5)

# Explicit re-exports for backward compatibility (ruff F401)
__all__ = [
    # Errors
    "AgentAbortError",
    "InvalidVerdictValueError",
    "LoopLimitExceeded",
    "ToolError",
    "VerdictParseError",
    "check_tool_result",
    # Verdict
    "AI_FORMATTER_MAX_INPUT_CHARS",
    "FORMATTER_PROMPT",
    "RELAXED_PATTERNS",
    "AIFormatterFunc",
    "ReviewResult",
    "Verdict",
    "create_ai_formatter",
    "handle_abort_verdict",
    "parse_verdict",
    "_extract_verdict_field",
    # CLI
    "format_jsonl_line",
    "run_cli_streaming",
    # Context
    "build_context",
    # Tools
    "AIToolProtocol",
    "ClaudeTool",
    "CodexTool",
    "GeminiTool",
    "MockTool",
    # State
    "ExecutionConfig",
    "ExecutionMode",
    "SessionState",
    "State",
    "infer_result_label",
    # Core
    "RunLogger",
    "load_prompt",
    "post_issue_comment",
    "AgentContext",
    "create_default_context",
    # Handlers
    "handle_init",
    "handle_investigate",
    "handle_investigate_review",
    "handle_detail_design",
    "handle_detail_design_review",
    "handle_implement",
    "handle_implement_review",
    "handle_pr_create",
    # Orchestrator
    "STATE_HANDLERS",
    "parse_args",
    "run",
    "list_states",
]


# ==========================================
# 1. State Handler Dispatch
# ==========================================

StateHandler = Callable[[AgentContext, SessionState], State]

STATE_HANDLERS: dict[State, StateHandler] = {
    State.INIT: handle_init,
    State.INVESTIGATE: handle_investigate,
    State.INVESTIGATE_REVIEW: handle_investigate_review,
    State.DETAIL_DESIGN: handle_detail_design,
    State.DETAIL_DESIGN_REVIEW: handle_detail_design_review,
    State.IMPLEMENT: handle_implement,
    State.IMPLEMENT_REVIEW: handle_implement_review,
    State.PR_CREATE: handle_pr_create,
}


# ==========================================
# 2. CLI Parser
# ==========================================


def parse_args() -> ExecutionConfig:
    """CLI引数をパースして実行設定を生成

    Returns:
        ExecutionConfig: 実行設定

    Raises:
        SystemExit: 引数エラー時
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="bugfix_agent_orchestrator",
        description="Bugfix Agent v5 Orchestrator - AI-driven bug fixing workflow automation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # 通常実行（FULL モード）
  %(prog)s --issue https://github.com/apokamo/kamo2/issues/182

  # 単一ステート実行（SINGLE モード）
  %(prog)s -i https://github.com/apokamo/kamo2/issues/182 --state INVESTIGATE
  %(prog)s -i https://github.com/apokamo/kamo2/issues/182 --state DETAIL_DESIGN_REVIEW

  # 範囲実行（FROM_END モード）
  %(prog)s -i https://github.com/apokamo/kamo2/issues/182 --from IMPLEMENT
  %(prog)s -i https://github.com/apokamo/kamo2/issues/182 --from QA

  # ツール指定（デフォルトモデル）
  %(prog)s -i https://github.com/apokamo/kamo2/issues/182 -s INIT --tool codex
  %(prog)s -i https://github.com/apokamo/kamo2/issues/182 -s INIT -t gemini

  # ツール＆モデル指定
  %(prog)s -i https://github.com/apokamo/kamo2/issues/182 -s INIT --tool-model codex:o4-mini
  %(prog)s -i https://github.com/apokamo/kamo2/issues/182 -s INIT -tm gemini:gemini-2.5-flash

  # ステート一覧表示
  %(prog)s --list-states
        """,
    )

    # --issue オプション
    parser.add_argument(
        "--issue",
        "-i",
        type=str,
        help="Target issue URL (例: https://github.com/apokamo/kamo2/issues/182)",
    )

    # --state オプション (SINGLE モード)
    parser.add_argument(
        "--state",
        "-s",
        type=str,
        help="Run single state only (例: INVESTIGATE, DETAIL_DESIGN_REVIEW)",
    )

    # --from オプション (FROM_END モード)
    parser.add_argument(
        "--from",
        "-f",
        dest="from_state",
        type=str,
        help="Run from state to COMPLETE (例: IMPLEMENT, QA)",
    )

    # --list-states オプション
    parser.add_argument(
        "--list-states", "-l", action="store_true", help="List available states and exit"
    )

    # --tool オプション (ツール指定)
    parser.add_argument(
        "--tool",
        "-t",
        type=str,
        choices=["codex", "gemini", "claude"],
        help="Override tool for single state execution (codex, gemini, claude)",
    )

    # --tool-model オプション (ツール:モデル指定)
    parser.add_argument(
        "--tool-model",
        "-tm",
        type=str,
        metavar="TOOL:MODEL",
        help="Override tool and model (e.g., codex:o4-mini, gemini:gemini-2.5-flash)",
    )

    args = parser.parse_args()

    # --list-states の場合は特別処理（後続で実装）
    if args.list_states:
        # main() で処理するためのマーカー
        return ExecutionConfig(mode=ExecutionMode.FULL, issue_url="__LIST_STATES__")

    # --issue が必須（--list-states 以外）
    if not args.issue:
        parser.error("--issue is required (except for --list-states)")

    # --state と --from の排他制約
    if args.state and args.from_state:
        parser.error("--state and --from are mutually exclusive")

    # --tool と --tool-model の排他制約
    if args.tool and args.tool_model:
        parser.error("--tool and --tool-model are mutually exclusive")

    # --tool-model のパース（tool:model 形式）
    tool_override: str | None = None
    model_override: str | None = None

    if args.tool:
        tool_override = args.tool
    elif args.tool_model:
        if ":" not in args.tool_model:
            parser.error("--tool-model must be in format TOOL:MODEL (e.g., codex:o4-mini)")
        parts = args.tool_model.split(":", 1)
        tool_override = parts[0]
        model_override = parts[1]
        if tool_override not in ("codex", "gemini", "claude"):
            parser.error(f"Invalid tool: {tool_override}. Must be codex, gemini, or claude")

    # issue_number を抽出
    issue_number = int(args.issue.rstrip("/").split("/")[-1])

    # モード判定
    if args.state:
        # SINGLE モード
        try:
            target_state = State[args.state]
        except KeyError:
            parser.error(f"Invalid state: {args.state}. Use --list-states to see valid states.")
        return ExecutionConfig(
            mode=ExecutionMode.SINGLE,
            target_state=target_state,
            issue_url=args.issue,
            issue_number=issue_number,
            tool_override=tool_override,
            model_override=model_override,
        )
    elif args.from_state:
        # FROM_END モード
        try:
            target_state = State[args.from_state]
        except KeyError:
            parser.error(
                f"Invalid state: {args.from_state}. Use --list-states to see valid states."
            )
        return ExecutionConfig(
            mode=ExecutionMode.FROM_END,
            target_state=target_state,
            issue_url=args.issue,
            issue_number=issue_number,
            tool_override=tool_override,
            model_override=model_override,
        )
    else:
        # FULL モード（デフォルト）
        return ExecutionConfig(
            mode=ExecutionMode.FULL,
            issue_url=args.issue,
            issue_number=issue_number,
            tool_override=tool_override,
            model_override=model_override,
        )


# ==========================================
# 3. Main Orchestrator
# ==========================================


def run(config: ExecutionConfig, ctx: AgentContext | None = None) -> None:
    """オーケストレーターのメインエントリポイント（Phase 2 対応）

    Args:
        config: 実行設定（モード、対象ステート、Issue URL）
        ctx: AgentContext（None なら create_default_context で生成）
             テスト時は tests.utils.context.create_test_context() で生成したものを渡す

    Raises:
        ToolError: AI ツールがエラーを返した場合
        TypeError: ctx が AgentContext でない場合
        ValueError: ハンドラが見つからない場合
    """
    # コンテキスト初期化（依存性注入対応）
    if ctx is not None and not isinstance(ctx, AgentContext):
        raise TypeError(f"ctx must be AgentContext, got {type(ctx).__name__}")
    if ctx is None:
        ctx = create_default_context(
            config.issue_url,
            tool_override=config.tool_override,
            model_override=config.model_override,
        )
    session_state = SessionState()
    logger = ctx.logger

    # JSONL ロガー初期化
    logger.log_run_start(config.issue_url, ctx.run_timestamp)

    print(f"=== 🚀 Bugfix Agent v5 Started (mode={config.mode.name}) ===")
    print(f"Issue: {config.issue_url}")
    if config.tool_override:
        tool_info = config.tool_override
        if config.model_override:
            tool_info += f":{config.model_override}"
        print(f"Tool Override: {tool_info}")
    print(f"Artifacts: {ctx.artifacts_dir}")

    # 開始ステート決定
    if config.mode == ExecutionMode.FULL:
        current = State.INIT
    else:
        # SINGLE / FROM_END モード
        if config.target_state is None:
            raise ValueError(f"target_state is required for mode {config.mode.name}")
        current = config.target_state
        print(f"Starting from: {current.name}")

    # Circuit Breaker の設定
    max_loop_count = get_config_value("agent.max_loop_count", 5)
    state_transition_delay = get_config_value("agent.state_transition_delay", 1.0)

    # メインループ
    try:
        while current != State.COMPLETE:
            print(f"\n📍 State: {current.name}")
            time.sleep(state_transition_delay)

            # Circuit Breaker: ループ回数制限チェック
            for loop_name, count in session_state.loop_counters.items():
                if count >= max_loop_count:
                    raise LoopLimitExceeded(
                        f"{loop_name} exceeded max limit ({count} >= {max_loop_count})"
                    )

            # ハンドラ取得
            handler = STATE_HANDLERS.get(current)
            if handler is None:
                raise ValueError(f"No handler for state: {current}")

            # ハンドラ実行（ログ出力）
            logger.log_state_enter(current.name)
            next_state = handler(ctx, session_state)
            result_label = infer_result_label(current, next_state)
            logger.log_state_exit(current.name, result_label, next_state.name)

            # SINGLE モードは1回で終了
            if config.mode == ExecutionMode.SINGLE:
                print(f">>> SINGLE mode: stopping after {current.name}")
                break

            # 次のステートへ遷移
            current = next_state

        # 正常完了
        logger.log_run_end("COMPLETE", session_state.loop_counters)
        print("\n=== ✨ Workflow Completed Successfully! ===")
        print(f"Loop counters: {session_state.loop_counters}")

    except LoopLimitExceeded as e:
        # ループ制限超過時は停止してログ出力
        logger.log_state_exit(current.name, "LOOP_LIMIT", current.name)
        logger.log_run_end("LOOP_LIMIT", session_state.loop_counters, error=str(e))
        print(f"\n=== ⚠️ Workflow Stopped (Circuit Breaker): {e} ===")
        raise

    except ToolError as e:
        # ツールエラー時は停止してログ出力（停止ステートを記録）
        logger.log_state_exit(current.name, "ERROR", current.name)
        logger.log_run_end("ERROR", session_state.loop_counters, error=str(e))
        print(f"\n=== ❌ Workflow Failed: {e} ===")
        raise


def list_states() -> None:
    """ステート一覧を表示して終了"""
    print("\nAvailable States:")
    print("  INIT                    Issue 必須項目確認（再現環境/手順/期待挙動）")
    print("  INVESTIGATE             再現実行、期待値との差、原因仮説 → Issue 追記")
    print("  INVESTIGATE_REVIEW      INVESTIGATE 成果物レビュー")
    print("  DETAIL_DESIGN           詳細設計・テストケース一覧 → Issue 追記")
    print("  DETAIL_DESIGN_REVIEW    DETAIL_DESIGN 成果物レビュー")
    print("  IMPLEMENT               ブランチ作成、実装、テスト実行 → Issue 追記")
    print("  IMPLEMENT_REVIEW        IMPLEMENT 成果物レビュー（QA統合）")
    print("  PR_CREATE               gh pr create 実行、PR URL 共有")
    print("  COMPLETE                ワークフロー完了")
    print("\nOutput: Issue 本文追記 + <STATE> Update コメント")
    print("Artifacts: test-artifacts/bugfix-agent/<issue-number>/<YYMMDDhhmm>/<state>/")
    print("\nUsage:")
    print("  --state <STATE>   Run single state only")
    print("  --from <STATE>    Run from state to COMPLETE")
    print("  --issue <URL>     Target issue URL (required)")


if __name__ == "__main__":
    import sys

    # CLI引数をパース
    config = parse_args()

    # --list-states の場合は一覧表示して終了
    if config.issue_url == "__LIST_STATES__":
        list_states()
        sys.exit(0)

    # 通常実行
    run(config)
