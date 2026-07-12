"""Incident identity signature computation (Issue #304, 第1層).

失敗を **識別署名 ``(failure_cause, exception_type, 正規化エラー指紋)``** に写す純関数群。
``classify_failure()`` と同様に fs / provider / subprocess に触れない pure function として
S テストで固定する。

正規化パイプラインは occurrence 固有値（run_id / タイムスタンプ / 絶対パス / issue 参照 /
可変 tail）を除去し、識別的数値（HTTP status / exit code / errno）は allowlist で保持する。
``fingerprint_hash`` は **redaction 後の canonical text** から生成し、署名 marker 経由で
secrets が漏れないことを保証する（#303 決定 E）。
"""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass

from .models import FailureClassification
from .report import mask_secrets
from .snapshot import FailureSnapshot

#: 署名スキーマの版。marker / occurrence 記録に埋め込む。version 不一致は
#: 「一致なし＝新規起票」に倒れる（migration 機構は作らない — #303 決定 E）。
SIGNATURE_SCHEMA_VERSION = 1

#: 正規化後 canonical text の長さ上限（文字）。
FINGERPRINT_LIMIT = 2000

#: あいまい照合（助言専用）の類似度閾値。
SIMILARITY_THRESHOLD = 0.8

#: canonical input / exception_type が空のときのプレースホルダ。
_NO_ERROR_TEXT = "<no-error-text>"
_UNKNOWN_EXCEPTION = "-"

#: 単独で現れても保持する既知 HTTP status（識別的数値）。
_KNOWN_HTTP_STATUS = frozenset(
    {"400", "401", "403", "404", "408", "409", "422", "429", "500", "502", "503", "504", "529"}
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
#: traceback フレーム行（例外メッセージ行は残す）。
_TRACEBACK_FRAME_RE = re.compile(r'^\s*File "[^"]*", line \d+, in .*$', re.MULTILINE)

#: allowlist の識別的数値を守る sentinel。復元まで置換対象から外す。
_SENTINEL_OPEN = "\x00K"
_SENTINEL_CLOSE = "\x00"

#: 文脈付き数値（HTTP <n> / status code <n> / exit code <n> / errno <n> / code <n>）。
_CONTEXT_NUMERIC_RE = re.compile(
    r"(?i)\b(HTTP|status(?:\s+code)?|exit(?:\s+code)?|errno|code)\s+(\d+)"
)
_STANDALONE_HTTP_RE = re.compile(r"(?<!\d)(" + "|".join(sorted(_KNOWN_HTTP_STATUS)) + r")(?!\d)")

#: 可変 payload の tail（``Last N chars:`` 以降）。#301 の 3 再発を同値にする要の規則。
_TAIL_RE = re.compile(r"(?i)Last\s+\d+\s+chars:.*", re.DOTALL)
_RUN_ID_RE = re.compile(r"\b\d{12}(?:-\d{1,4})?\b")
_ISO_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:?\d{2}|Z)?")
_ABS_PATH_RE = re.compile(r"(?<![\w/])(?:/[\w.\-]+)+/?")
_ISSUE_REF_RE = re.compile(r"#\d+")
_PORT_RE = re.compile(r"(?i)\bport\s+\d+")
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_HEX_RE = re.compile(r"\b[0-9a-fA-F]{8,}\b")
_LONG_NUM_RE = re.compile(r"\d{4,}")
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class IncidentSignature:
    """識別署名。``fingerprint_hash`` の 4 値完全一致で同値判定する。"""

    schema_version: int
    cause: str
    exception_type: str
    fingerprint: str
    fingerprint_hash: str

    def matches(self, other: IncidentSignature) -> bool:
        """schema_version / cause / exception_type / fingerprint_hash の完全一致。"""
        return (
            self.schema_version == other.schema_version
            and self.cause == other.cause
            and self.exception_type == other.exception_type
            and self.fingerprint_hash == other.fingerprint_hash
        )


def _protect_allowlist(text: str) -> str:
    """識別的数値を sentinel で包み、occurrence 置換から守る。"""

    def _wrap_context(match: re.Match[str]) -> str:
        return f"{match.group(1)} {_SENTINEL_OPEN}{match.group(2)}{_SENTINEL_CLOSE}"

    protected = _CONTEXT_NUMERIC_RE.sub(_wrap_context, text)

    def _wrap_http(match: re.Match[str]) -> str:
        return f"{_SENTINEL_OPEN}{match.group(1)}{_SENTINEL_CLOSE}"

    return _STANDALONE_HTTP_RE.sub(_wrap_http, protected)


def _restore_allowlist(text: str) -> str:
    return text.replace(_SENTINEL_OPEN, "").replace(_SENTINEL_CLOSE, "")


def normalize_error_text(text: str) -> str:
    """正規化パイプライン（redaction → ANSI/traceback 除去 → allowlist 保護 →
    occurrence 固有値の置換 → allowlist 復元 → 空白正規化 → 切り詰め）。

    各段は S テストの fixture で固定する。``fingerprint_hash`` はこの戻り値から生成する。
    """
    # 1. redaction を必ず先に適用する（hash は redaction 後の text から生成）。
    out = mask_secrets(text)
    # 2. ANSI エスケープ除去。
    out = _ANSI_RE.sub("", out)
    # 3. traceback フレーム行除去（例外メッセージ行は残す）。
    out = _TRACEBACK_FRAME_RE.sub("", out)
    # 3.5. 可変 payload の tail を先に潰す。allowlist 保護より前に行うのは、``Last 500 chars:``
    #      の桁数 500 が既知 HTTP status として誤保護され tail 正規表現の ``\d+`` を割るのを防ぐため。
    out = _TAIL_RE.sub("<TAIL>", out)
    # 4. 保持 allowlist を sentinel 化して守る。
    out = _protect_allowlist(out)
    # 5. occurrence 固有値の置換。
    out = _ISO_TS_RE.sub("<TS>", out)
    out = _RUN_ID_RE.sub("<RUN_ID>", out)
    out = _UUID_RE.sub("<HEX>", out)
    out = _HEX_RE.sub("<HEX>", out)
    out = _ISSUE_REF_RE.sub("<ISSUE>", out)
    out = _PORT_RE.sub("port <N>", out)
    out = _ABS_PATH_RE.sub("<PATH>", out)
    out = _LONG_NUM_RE.sub("<N>", out)
    # 6. allowlist sentinel の復元。
    out = _restore_allowlist(out)
    # 7. 空白正規化。
    out = _WS_RE.sub(" ", out).strip()
    # 8. 切り詰め。
    return out[:FINGERPRINT_LIMIT]


def _canonical_input(snapshot: FailureSnapshot) -> str:
    """canonical input の優先順位: ``attempt_error`` 主・``workflow_end_error`` 従（連結しない）。"""
    if snapshot.attempt_error:
        return snapshot.attempt_error
    if snapshot.workflow_end_error:
        return snapshot.workflow_end_error
    return ""


def _exception_type(snapshot: FailureSnapshot) -> str:
    event = snapshot.failure_event
    if event is not None and event.exception_type:
        return event.exception_type
    wf_type = snapshot.workflow_end_exception_type
    if wf_type:
        return wf_type
    return _UNKNOWN_EXCEPTION


def compute_signature(
    snapshot: FailureSnapshot, classification: FailureClassification
) -> IncidentSignature:
    """``FailureSnapshot`` + ``FailureClassification`` から識別署名を算出する（純関数）。"""
    raw = _canonical_input(snapshot)
    fingerprint = normalize_error_text(raw) if raw else _NO_ERROR_TEXT
    fingerprint_hash = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
    return IncidentSignature(
        schema_version=SIGNATURE_SCHEMA_VERSION,
        cause=classification.cause,
        exception_type=_exception_type(snapshot),
        fingerprint=fingerprint,
        fingerprint_hash=fingerprint_hash,
    )


def similarity(current: str, candidate: str) -> float:
    """あいまい照合の類似度（助言専用）。

    ``difflib.SequenceMatcher`` の ``ratio()`` は autojunk 等のヒューリスティクスにより
    引数順で結果が変わりうるため、**第1引数 = 今回の fingerprint、第2引数 = 候補側
    fingerprint** を公開契約として固定する。
    """
    return difflib.SequenceMatcher(None, a=current, b=candidate).ratio()
