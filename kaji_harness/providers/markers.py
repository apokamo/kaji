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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

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

_VERDICT_META_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_VERDICT_META_VALUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
_VERDICT_MARKER_RE = re.compile(
    r"^<!-- kaji-verdict: "
    r"step=(?P<step>[a-z][a-z0-9_-]*) "
    r"status=(?P<status>PASS|RETRY|ABORT|BACK|BACK_[A-Z0-9_]+)"
    r"(?P<meta>(?: [a-z][a-z0-9_]*=[A-Za-z0-9][A-Za-z0-9._/-]*)*) -->$"
)


@dataclass(frozen=True)
class KajiVerdictMarker:
    """Parsed provider-neutral verdict marker.

    Attributes:
        step: Producing workflow step.
        status: Verdict status.
        meta: Validated metadata keyed by stable machine-readable names.
    """

    step: str
    status: str
    meta: dict[str, str]


def build_kaji_verdict_marker(
    step: str,
    status: str,
    metadata: Mapping[str, str] | None = None,
) -> str:
    """``step`` / ``status`` から verdict marker 文字列（1 行目のみ、改行なし）を返す。

    Args:
        step: 発行元 step の識別子（例: ``review-code`` / ``final-check`` /
            ``design``）。``^[a-z][a-z0-9_-]*$`` に一致する必要がある。
        status: verdict status。``PASS`` / ``RETRY`` / ``ABORT`` / ``BACK`` /
            ``BACK_<UPPER>``（``BACK_[A-Z0-9_]+`` 文法）のいずれか。
        metadata: Optional machine-readable metadata. Keys and values use the
            restricted marker grammar and are sorted for deterministic output.

    Returns:
        ``<!-- kaji-verdict: step=<step> status=<status> -->`` 形式の 1 行文字列。

    Raises:
        ValueError: ``step`` / ``status`` / metadata が語彙に一致しない（fail-loud）。
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
    meta_parts: list[str] = []
    for key, value in sorted((metadata or {}).items()):
        if not _VERDICT_META_KEY_RE.fullmatch(key):
            raise ValueError(f"invalid verdict metadata key {key!r}: expected /^[a-z][a-z0-9_]*$/")
        if not _VERDICT_META_VALUE_RE.fullmatch(value):
            raise ValueError(
                f"invalid verdict metadata value for {key!r}: "
                "expected /^[A-Za-z0-9][A-Za-z0-9._/-]*$/"
            )
        meta_parts.append(f"{key}={value}")
    metadata_suffix = f" {' '.join(meta_parts)}" if meta_parts else ""
    return (
        f"{_KAJI_VERDICT_MARKER_PREFIX}step={step} status={status}"
        f"{metadata_suffix}{_KAJI_VERDICT_MARKER_SUFFIX}"
    )


def parse_kaji_verdict_marker(line: str) -> KajiVerdictMarker | None:
    """Parse one complete verdict marker line, failing closed on malformed input.

    Args:
        line: The first line of an Issue comment.

    Returns:
        Parsed marker, or ``None`` when the complete line is not valid.
    """
    match = _VERDICT_MARKER_RE.fullmatch(line)
    if match is None:
        return None
    metadata: dict[str, str] = {}
    for token in match.group("meta").strip().split():
        key, value = token.split("=", 1)
        if key in metadata:
            return None
        metadata[key] = value
    return KajiVerdictMarker(
        step=match.group("step"),
        status=match.group("status"),
        meta=metadata,
    )


def resolve_verdict_marker(
    step: str | None,
    status: str | None,
    metadata_args: Sequence[str] | None = None,
) -> str | None:
    """Resolve optional CLI verdict flags to a marker line.

    Args:
        step: The producing workflow step, or ``None`` when flags are omitted.
        status: The verdict status, or ``None`` when flags are omitted.
        metadata_args: Repeated CLI ``key=value`` values.

    Returns:
        A marker string, or ``None`` when both flags are omitted.

    Raises:
        ValueError: Exactly one flag is supplied or either value is invalid.
    """
    if step is None and status is None and not metadata_args:
        return None
    if step is None or status is None:
        raise ValueError(
            "--verdict-step and --verdict-status must be specified together "
            "when --verdict-meta is used"
        )
    metadata: dict[str, str] = {}
    for raw in metadata_args or ():
        key, separator, value = raw.partition("=")
        if not separator:
            raise ValueError(f"invalid --verdict-meta {raw!r}: expected key=value")
        if key in metadata:
            raise ValueError(f"duplicate verdict metadata key: {key!r}")
        metadata[key] = value
    return build_kaji_verdict_marker(step, status, metadata)
