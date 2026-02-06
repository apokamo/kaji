"""Verdict parsing and constants for Bugfix Agent v5 (Issue #194 Protocol)

This module provides VERDICT handling with hybrid fallback parsing (Issue #292):
- Verdict: Enum with 4 verdict types (PASS, RETRY, BACK_DESIGN, ABORT)
- parse_verdict: Hybrid fallback parser (Step 1-3)
- ReviewResult: Legacy alias for backward compatibility (deprecated)

Hybrid Fallback Strategy:
- Step 1: Strict Parse - "Result: <STATUS>" pattern
- Step 2: Relaxed Parse - Multiple patterns (Status:, **Status**:, ステータス: etc.)
- Step 3: AI Formatter Retry - Uses AI to reformat malformed output

Design Principle (Issue #292 Review):
- Parser returns Verdict enum only (including ABORT)
- AgentAbortError is raised by the orchestrator, not the parser
- InvalidVerdictValueError is raised immediately (no fallback for invalid values)
- This separation ensures single responsibility and reusability
"""

import re
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from .errors import (  # noqa: I001
    AgentAbortError,
    InvalidVerdictValueError,
    VerdictParseError,
)

if TYPE_CHECKING:
    from .tools.base import AIToolProtocol

# Type alias for AI formatter function
AIFormatterFunc = Callable[[str], str]

# Constants (Issue #292: Magic number elimination)
AI_FORMATTER_MAX_INPUT_CHARS: int = 8000
"""AI Formatter に渡す最大入力文字数。
約2000トークン相当。モデルのコンテキスト長とコストを考慮した値。
"""


class Verdict(Enum):
    """VERDICT判定キーワード (Issue #194 統一プロトコル)

    4種類の判定結果を定義:
    - PASS: 成功・次ステートへ進行
    - RETRY: 同ステート再実行（軽微な問題）
    - BACK_DESIGN: 設計見直しが必要 → DETAIL_DESIGN
    - ABORT: 続行不能・即座に終了（環境/外部要因）
    """

    PASS = "PASS"
    RETRY = "RETRY"
    BACK_DESIGN = "BACK_DESIGN"
    ABORT = "ABORT"


# Step 2: Relaxed Parse Patterns (Issue #292)
# All patterns explicitly match only valid Verdict values (no wildcards)
# Note: After #293, Result: is the standard format. Status: patterns kept for legacy/edge cases.
RELAXED_PATTERNS: list[str] = [
    # VERDICT 標準フォーマット（#293 で統一）
    r"Result:\s*(PASS|RETRY|BACK_DESIGN|ABORT)",
    # リスト形式 (- Result: PASS)
    r"-\s*Result:\s*(PASS|RETRY|BACK_DESIGN|ABORT)",
    # Legacy: Status 形式（旧 Review Result フォーマット）
    r"Status:\s*(PASS|RETRY|BACK_DESIGN|ABORT)",
    r"-\s*Status:\s*(PASS|RETRY|BACK_DESIGN|ABORT)",
    r"\*\*Status\*\*:\s*(PASS|RETRY|BACK_DESIGN|ABORT)",
    # 日本語
    r"ステータス:\s*(PASS|RETRY|BACK_DESIGN|ABORT)",
    # 代入形式 (Status = PASS / Result = PASS)
    r"Status\s*=\s*(PASS|RETRY|BACK_DESIGN|ABORT)",
    r"Result\s*=\s*(PASS|RETRY|BACK_DESIGN|ABORT)",
]

# Step 3: AI Formatter Prompt (Issue #292)
# Note: Output format uses "Result:" to match _parse_verdict_strict()
FORMATTER_PROMPT: str = """以下の出力からVERDICTを抽出し、正確なフォーマットで出力してください。

## 入力
{raw_output}

## 出力フォーマット（厳密に従ってください）
## VERDICT
- Result: <PASS|RETRY|BACK_DESIGN|ABORT のいずれか1つ>
- Reason: <1行の要約>
- Evidence: <詳細>
- Suggestion: <次のアクション>

重要: Result行は必ず "- Result: " で始め、4つの値のいずれかを出力してください。
"""


def _parse_verdict_strict(text: str) -> Verdict:
    """Step 1: 厳密パース - "Result: <STATUS>" パターンのみ

    Args:
        text: パース対象のテキスト

    Returns:
        Verdict: パースされた判定結果

    Raises:
        VerdictParseError: Result行が見つからない、または不正な値
    """
    match = re.search(r"Result:\s*(\w+)", text, re.IGNORECASE)
    if not match:
        raise VerdictParseError("No VERDICT Result found in output")

    result_str = match.group(1).upper()

    try:
        return Verdict(result_str)
    except ValueError as e:
        valid_values = [v.value for v in Verdict]
        # InvalidVerdictValueError is NOT recoverable - indicates prompt violation
        raise InvalidVerdictValueError(
            f"Invalid VERDICT value: {result_str}. Valid values: {valid_values}"
        ) from e


def _parse_verdict_relaxed(text: str) -> Verdict:
    """Step 2: 緩和パース - 複数パターンで探索

    All patterns are restricted to valid Verdict values only.
    No wildcards - prevents matching invalid values like "PENDING".

    Args:
        text: パース対象のテキスト

    Returns:
        Verdict: パースされた判定結果

    Raises:
        VerdictParseError: 全パターンで見つからない場合
    """
    for pattern in RELAXED_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            result_str = match.group(1).upper()
            # Pattern guarantees valid value, no ValueError possible
            return Verdict(result_str)

    raise VerdictParseError("No valid verdict found (relaxed patterns exhausted)")


def parse_verdict(
    text: str,
    ai_formatter: AIFormatterFunc | None = None,
    max_retries: int = 2,
) -> Verdict:
    """VERDICT/Review Resultをパース（ハイブリッドフォールバック対応）

    3ステップのフォールバック戦略:
    - Step 1: Strict Parse - "Result: <STATUS>" パターン
    - Step 2: Relaxed Parse - 複数パターン（Status:, **Status**:, ステータス: 等）
    - Step 3: AI Formatter Retry - 最大 max_retries 回

    Design Note (Issue #292 Review):
        - パーサーは Verdict enum を返すのみ。Verdict.ABORT が返された場合、
          AgentAbortError の送出は呼び出し元（オーケストレーター）の責務。
        - InvalidVerdictValueError（不正な値）は即座に raise され、フォールバック対象外。
          これはフォーマット問題ではなくプロンプト違反/実装バグを示すため。

    Expected formats:
        ## VERDICT / ## Review Result
        - Result/Status: PASS | RETRY | BACK_DESIGN | ABORT
        - Reason/Summary: <判定理由>
        - Evidence/Details: <判定根拠>
        - Suggestion/Next Action: <次のアクション提案>

    Args:
        text: パース対象のテキスト
        ai_formatter: AI整形関数 (optional, Step 3用)
        max_retries: Step 3 最大リトライ回数 (default: 2, must be >= 1)

    Returns:
        Verdict: パースされた判定結果（ABORT含む）

    Raises:
        InvalidVerdictValueError: 不正な VERDICT 値（フォールバック対象外）
        VerdictParseError: 全ステップ失敗時
        ValueError: max_retries < 1 の場合
    """
    # Validate max_retries
    if max_retries < 1:
        raise ValueError(f"max_retries must be >= 1, got {max_retries}")

    # Step 1: Strict Parse
    try:
        return _parse_verdict_strict(text)
    except InvalidVerdictValueError:
        raise  # Invalid value is NOT recoverable - re-raise immediately
    except VerdictParseError:
        pass  # No Result found - continue to Step 2

    # Step 2: Relaxed Parse
    try:
        return _parse_verdict_relaxed(text)
    except VerdictParseError:
        pass  # Continue to Step 3

    # Step 3: AI Formatter Retry
    if ai_formatter is None:
        raise VerdictParseError(
            "All parse attempts failed (Step 1-2). Provide ai_formatter for Step 3 retry."
        )

    # Truncate input for AI formatter using head+tail strategy
    # VERDICT is often at the end, so tail is important
    truncate_delimiter = "\n...[truncated]...\n"
    # Ensure head + delimiter + tail <= MAX (Issue #292 Review: guarantee upper bound)
    usable_chars = AI_FORMATTER_MAX_INPUT_CHARS - len(truncate_delimiter)
    if usable_chars <= 0:
        raise ValueError(
            f"AI_FORMATTER_MAX_INPUT_CHARS ({AI_FORMATTER_MAX_INPUT_CHARS}) "
            f"must be > delimiter length ({len(truncate_delimiter)})"
        )
    half_limit = usable_chars // 2
    if len(text) > AI_FORMATTER_MAX_INPUT_CHARS:
        truncated_text = text[:half_limit] + truncate_delimiter + text[-half_limit:]
    else:
        truncated_text = text

    last_error: VerdictParseError | None = None
    for _attempt in range(max_retries):
        try:
            formatted = ai_formatter(truncated_text)
            # Try strict first, then relaxed (Issue #292 Review: LLM may use Status: format)
            try:
                return _parse_verdict_strict(formatted)
            except InvalidVerdictValueError:
                raise  # Invalid value is NOT recoverable
            except VerdictParseError:
                return _parse_verdict_relaxed(formatted)
        except InvalidVerdictValueError:
            raise  # Invalid value is NOT recoverable
        except VerdictParseError as e:
            last_error = e
            continue
        # Note: ai_formatter communication errors propagate to caller

    raise VerdictParseError(
        f"All {max_retries} AI formatter attempts failed. Last error: {last_error}"
    )


def _extract_verdict_field(text: str, field_name: str) -> str | None:
    """VERDICTセクションから指定フィールドを抽出

    Args:
        text: パース対象のテキスト
        field_name: フィールド名 (e.g., "Reason", "Evidence", "Suggestion",
                    "Summary", "Details", "Next Action")

    Returns:
        フィールド値。見つからない場合はNone
    """
    pattern = rf"{field_name}:\s*(.+?)(?=\n-|\n##|\Z)"
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


def handle_abort_verdict(verdict: Verdict, raw_output: str) -> Verdict:
    """ABORT verdict を処理し、例外を送出 (Issue #292 責務分離)

    パーサーは Verdict enum を返すのみで、AgentAbortError の送出は
    この関数（オーケストレーター側）の責務。

    Args:
        verdict: parse_verdict() の戻り値
        raw_output: パース元の生テキスト（reason/suggestion抽出用）

    Returns:
        Verdict: ABORT以外の場合はそのまま返す

    Raises:
        AgentAbortError: verdict が ABORT の場合
    """
    if verdict == Verdict.ABORT:
        reason = (
            _extract_verdict_field(raw_output, "Summary")
            or _extract_verdict_field(raw_output, "Reason")
            or "No reason provided"
        )
        suggestion = (
            _extract_verdict_field(raw_output, "Next Action")
            or _extract_verdict_field(raw_output, "Suggestion")
            or ""
        )
        raise AgentAbortError(reason, suggestion)
    return verdict


def create_ai_formatter(
    tool: "AIToolProtocol",
    *,
    context: str = "",
    log_dir: Path | None = None,
) -> AIFormatterFunc:
    """AI ツールを使用した formatter 関数を生成

    Step 3 は"最後の砦"なので、ログと文脈を保持することが重要。

    Args:
        tool: AI ツール（reviewer など）。run() メソッドを持つこと。
        context: AI ツールに渡すコンテキスト（例: ctx.issue_url）
        log_dir: ログ出力先ディレクトリ（監査・再現性のため）

    Returns:
        AIFormatterFunc: parse_verdict の ai_formatter 引数に渡す関数
    """

    def formatter(raw_output: str) -> str:
        prompt = FORMATTER_PROMPT.format(raw_output=raw_output)
        result: str
        result, _ = tool.run(prompt=prompt, context=context, log_dir=log_dir)
        return result

    return formatter


# Legacy alias for backward compatibility (will be removed in Phase 3)
class ReviewResult(Enum):
    """[DEPRECATED] Use Verdict instead. Will be removed in Phase 3."""

    PASS = "PASS"
    BLOCKED = "BLOCKED"  # → Verdict.RETRY
    FIX_REQUIRED = "FIX_REQUIRED"  # → Verdict.RETRY
    DESIGN_FIX = "DESIGN_FIX"  # → Verdict.BACK_DESIGN

    @classmethod
    def contains(cls, text: str, result: "ReviewResult") -> bool:
        """テキストにレビュー結果が含まれるか判定"""
        return result.value in text
