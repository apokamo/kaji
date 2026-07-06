"""Provider 中立の kaji comment marker（verdict marker）。

cross-skill 契約（BACK 再入検出など）を SKILL.md の散文ではなく CLI / harness
層に置くため（ADR 008 決定 3）、判定コメント本文の 1 行目に決定的な HTML
コメントマーカーを付与する。github / local 両 provider で同一の振る舞いを持つ。

既存の ``providers.github.build_kaji_review_marker`` は PR review state 専用で
GitHubProvider に結合しているのに対し、verdict marker は provider に依存しない
（local でも同じ 1 行目マーカーを永続化する）ため独立モジュールに置く。
"""

from __future__ import annotations

import re

# marker 形式: comment body の 1 行目に置く HTML コメント。GitHub UI 上では
# 不可視のため review 体験を壊さない。local provider では
# ``.kaji/issues/<id>/comments/<seq>-<machine>.md`` の 1 行目に永続化される。
_KAJI_VERDICT_MARKER_PREFIX = "<!-- kaji-verdict: "
_KAJI_VERDICT_MARKER_SUFFIX = " -->"

# 発行元 step の識別子。lowercase 英数字 + ``-`` / ``_``（先頭は英字）。
_VERDICT_STEP_RE = re.compile(r"^[a-z][a-z0-9_-]*$")

# 標準 4 status + ``BACK_*`` 拡張。suffix 文法は
# ``docs/dev/workflow-authoring.md`` § ``BACK_*`` プレフィックス拡張の
# ``[A-Z0-9_]+`` と整合させる（``BACK_`` 単独 / lowercase suffix は不正）。
_VERDICT_STATUS_RE = re.compile(r"^(PASS|RETRY|ABORT|BACK|BACK_[A-Z0-9_]+)$")


def build_kaji_verdict_marker(step: str, status: str) -> str:
    """``step`` / ``status`` から verdict marker 文字列（1 行目のみ、改行なし）を返す。

    Args:
        step: 発行元 step の識別子（例: ``review-code`` / ``final-check`` /
            ``design``）。``^[a-z][a-z0-9_-]*$`` に一致する必要がある。
        status: verdict status。``PASS`` / ``RETRY`` / ``ABORT`` / ``BACK`` /
            ``BACK_<UPPER>``（``BACK_[A-Z0-9_]+`` 文法）のいずれか。

    Returns:
        ``<!-- kaji-verdict: step=<step> status=<status> -->`` 形式の 1 行文字列。

    Raises:
        ValueError: ``step`` / ``status`` が語彙に一致しない（fail-loud）。
    """
    if not _VERDICT_STEP_RE.match(step):
        raise ValueError(
            f"invalid verdict step {step!r}: expected /^[a-z][a-z0-9_-]*$/ "
            "(lowercase identifier, e.g. 'review-code')"
        )
    if not _VERDICT_STATUS_RE.match(status):
        raise ValueError(
            f"invalid verdict status {status!r}: expected one of "
            "PASS / RETRY / ABORT / BACK or BACK_<UPPER> "
            "(grammar BACK_[A-Z0-9_]+)"
        )
    return f"{_KAJI_VERDICT_MARKER_PREFIX}step={step} status={status}{_KAJI_VERDICT_MARKER_SUFFIX}"
