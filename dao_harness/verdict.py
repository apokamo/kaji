"""Verdict parser for dao_harness.

Extracts verdict blocks from CLI output using YAML parsing.
"""

import re

import yaml

from .errors import InvalidVerdictValue, VerdictNotFound, VerdictParseError
from .models import Verdict

VERDICT_PATTERN = re.compile(
    r"---VERDICT---\s*\n(.*?)\n\s*---END_VERDICT---",
    re.DOTALL,
)


def parse_verdict(output: str, valid_statuses: set[str]) -> Verdict:
    """CLI 出力から verdict を抽出・検証する。

    Args:
        output: CLIResult.full_output（アダプタがデコード済みのテキスト）
        valid_statuses: ステップの on フィールドに定義された verdict 値の集合

    Returns:
        Verdict: 抽出された判定結果

    Raises:
        VerdictNotFound: ---VERDICT--- ブロックが見つからない
        VerdictParseError: 必須フィールド欠損またはYAMLパースエラー
        InvalidVerdictValue: status が valid_statuses に含まれない
    """
    match = VERDICT_PATTERN.search(output)
    if not match:
        raise VerdictNotFound(f"No verdict block found. Last 500 chars: {output[-500:]}")

    verdict = _parse_fields(match.group(1))
    _validate(verdict, valid_statuses)
    return verdict


def _parse_fields(block: str) -> Verdict:
    """verdict ブロックを YAML として解析し、4フィールドを抽出。"""
    try:
        fields = yaml.safe_load(block)
    except yaml.YAMLError as e:
        raise VerdictParseError(f"YAML parse error in verdict block: {e}") from e

    if not isinstance(fields, dict):
        raise VerdictParseError(f"Verdict block is not a YAML mapping: {type(fields)}")

    if "status" not in fields:
        raise VerdictParseError("Missing required field: status")
    if "reason" not in fields or not fields["reason"]:
        raise VerdictParseError("Missing required field: reason")
    if "evidence" not in fields or not fields["evidence"]:
        raise VerdictParseError("Missing required field: evidence")

    return Verdict(
        status=str(fields["status"]).strip(),
        reason=str(fields["reason"]).strip(),
        evidence=str(fields["evidence"]).strip(),
        suggestion=str(fields.get("suggestion", "")).strip(),
    )


def _validate(verdict: Verdict, valid_statuses: set[str]) -> None:
    """verdict 値の妥当性を検証。"""
    if verdict.status not in valid_statuses:
        raise InvalidVerdictValue(
            f"'{verdict.status}' not in {valid_statuses}. "
            "This indicates a prompt violation — do not retry."
        )
    if verdict.status in ("ABORT", "BACK") and not verdict.suggestion:
        raise VerdictParseError(f"{verdict.status} verdict requires non-empty suggestion")
