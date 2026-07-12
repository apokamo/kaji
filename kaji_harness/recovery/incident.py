"""Incident detection / aggregation layer (Issue #304, 第1層).

失敗の識別署名を既存 incident イシューと照合し、新規ならテンプレート起票、既存一致なら
occurrence コメントを追記して再発回数を marker 件数から導出する。完全純コード
（LLM なし）・fail-open・at-least-once。

layering（既存 recovery の延長）:

- marker の render / 厳格 parse、``plan_incident_action`` / テンプレート描画 / あいまい照合は
  **純関数**（S テストで固定）
- ``append_occurrence`` / ``read_occurrences`` は I/O 境界
- ``execute_incident_action`` は provider を呼ぶ副作用境界（handler が fail-open で包む）

count の正本は **対象イシューの全コメント中、hash 一致 valid occurrence marker の
ユニーク ``run_id`` 件数**。可変カウンタは持たない（crash window 二重投稿に耐える —
#303 決定 F）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..providers.models import Comment, Issue
from .report import sanitize_evidence
from .signature import (
    SIMILARITY_THRESHOLD,
    IncidentSignature,
    similarity,
)

#: ローカル occurrence 記録スキーマの版。
OCCURRENCE_SCHEMA_VERSION = 1

#: incident 記録の格納先（``<artifacts_dir>/incidents/occurrences.jsonl``）。
INCIDENTS_DIRNAME = "incidents"
OCCURRENCES_FILE = "occurrences.jsonl"

#: 起票時に必ず付与するラベル（種別キー + status 初期値）。
INCIDENT_LABEL = "incident"
INCIDENT_STATUS_INVESTIGATING = "incident:investigating"
INCIDENT_CAUSE_TRANSIENT = "incident:cause:transient"

#: あいまい候補の列挙上限。
FUZZY_MAX = 5

#: incident ラベル運用ガイドへのリンク。
_LABELS_GUIDE = "docs/dev/incident-labels.md"

_FINGERPRINT_FENCE = "kaji-fingerprint"

_IDENTITY_MARKER_RE = re.compile(
    r"<!-- kaji-incident: schema=(\d+) cause=(\S+) exception=(\S+) hash=([0-9a-f]{64}) -->"
)
_OCCURRENCE_MARKER_RE = re.compile(
    r"<!-- kaji-incident-occurrence: schema=(\d+) hash=([0-9a-f]{64}) "
    r"run_id=(\S+) source_issue=(\S+) -->"
)
_FINGERPRINT_BLOCK_RE = re.compile(
    r"```" + _FINGERPRINT_FENCE + r"\n(.*?)\n```",
    re.DOTALL,
)


# --------------------------------------------------------------------------- #
# marker render / 厳格 parse（純関数）
# --------------------------------------------------------------------------- #


def render_identity_marker(sig: IncidentSignature) -> str:
    """identity marker（イシュー本文 1 行目・照合キー）を返す。"""
    return (
        f"<!-- kaji-incident: schema={sig.schema_version} cause={sig.cause} "
        f"exception={sig.exception_type} hash={sig.fingerprint_hash} -->"
    )


def parse_identity_marker(text: str) -> IncidentSignature | None:
    """本文 1 行目の identity marker を厳格 parse する。

    読めない / 破損した marker は ``None``（照合では「一致なし＝新規起票」に倒れる）。
    fingerprint 実体は本文の fingerprint block から補完する（marker に含めない）。
    """
    first_line = text.lstrip().splitlines()[0] if text.strip() else ""
    m = _IDENTITY_MARKER_RE.fullmatch(first_line.strip())
    if m is None:
        return None
    fingerprint = parse_fingerprint_block(text) or ""
    return IncidentSignature(
        schema_version=int(m.group(1)),
        cause=m.group(2),
        exception_type=m.group(3),
        fingerprint=fingerprint,
        fingerprint_hash=m.group(4),
    )


def render_occurrence_marker(sig: IncidentSignature, *, run_id: str, source_issue: str) -> str:
    """occurrence marker（コメント行頭・1 run_id 1 行）を返す。"""
    return (
        f"<!-- kaji-incident-occurrence: schema={sig.schema_version} "
        f"hash={sig.fingerprint_hash} run_id={run_id} source_issue={source_issue} -->"
    )


@dataclass(frozen=True)
class OccurrenceMarker:
    """厳格 parse 済みの occurrence marker。"""

    schema_version: int
    hash: str
    run_id: str
    source_issue: str


def parse_occurrence_markers(text: str) -> list[OccurrenceMarker]:
    """コメント本文から valid な occurrence marker を全て抽出する（行頭・完全一致のみ）。"""
    out: list[OccurrenceMarker] = []
    for line in text.splitlines():
        # 行全体が marker と完全一致する行のみ採る。引用（``> <!-- ... -->``）等、marker 前に
        # 非空白がある行は fullmatch が弾く（先頭空白のみは許容）。
        m = _OCCURRENCE_MARKER_RE.fullmatch(line.strip())
        if m is None:
            continue
        out.append(
            OccurrenceMarker(
                schema_version=int(m.group(1)),
                hash=m.group(2),
                run_id=m.group(3),
                source_issue=m.group(4),
            )
        )
    return out


def render_fingerprint_block(fingerprint: str) -> str:
    """fingerprint block（あいまい照合・人間デバッグ用の fenced block）を返す。"""
    return f"```{_FINGERPRINT_FENCE}\n{fingerprint}\n```"


def parse_fingerprint_block(text: str) -> str | None:
    """本文の fingerprint block を取り出す。無い / 読めない場合は ``None``。"""
    m = _FINGERPRINT_BLOCK_RE.search(text)
    if m is None:
        return None
    return m.group(1)


# --------------------------------------------------------------------------- #
# 照合規則（純関数）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class IncidentCandidate:
    """search 結果の incident イシューを照合可能な形に落とした候補。"""

    issue_id: str
    state: str
    labels: tuple[str, ...]
    signature: IncidentSignature | None

    @property
    def is_transient(self) -> bool:
        return INCIDENT_CAUSE_TRANSIENT in self.labels


def parse_candidates(issues: list[Issue]) -> list[IncidentCandidate]:
    """search 結果の ``Issue`` を ``IncidentCandidate`` に変換する（identity marker 厳格 parse）。"""
    out: list[IncidentCandidate] = []
    for issue in issues:
        out.append(
            IncidentCandidate(
                issue_id=issue.id,
                state=(issue.state or "open").lower(),
                labels=tuple(label.name for label in issue.labels),
                signature=parse_identity_marker(issue.body),
            )
        )
    return out


@dataclass(frozen=True)
class IncidentAction:
    """照合の結論。``kind`` は ``create`` / ``recur`` / ``create_regression``。"""

    kind: str
    target_id: str | None = None
    regression_of: str | None = None
    also_matched: tuple[str, ...] = ()


def _issue_sort_key(issue_id: str) -> tuple[int, str]:
    """issue 番号最小（最古）選択のための決定論的キー。数値 ID は数値順、他は辞書順。"""
    return (0, f"{int(issue_id):020d}") if issue_id.isdigit() else (1, issue_id)


def plan_incident_action(
    signature: IncidentSignature, candidates: list[IncidentCandidate]
) -> IncidentAction:
    """署名同値の候補を state / ラベルで分岐し、実行アクションを決める（純関数）。

    優先順（上から評価）: open 一致 → closed+transient 一致 → closed 人間 resolve 一致
    （→ regression 新規）→ 一致なし（→ 新規）。同分岐内に複数一致した場合は issue 番号
    最小（最古）を選び、残りを ``also_matched`` に記録する（決定論）。
    """
    matched = [c for c in candidates if c.signature is not None and c.signature.matches(signature)]

    open_matches = [c for c in matched if c.state == "open"]
    if open_matches:
        return _recur(open_matches)

    transient_closed = [c for c in matched if c.state != "open" and c.is_transient]
    if transient_closed:
        return _recur(transient_closed)

    resolved_closed = [c for c in matched if c.state != "open" and not c.is_transient]
    if resolved_closed:
        ordered = sorted(resolved_closed, key=lambda c: _issue_sort_key(c.issue_id))
        return IncidentAction(
            kind="create_regression",
            regression_of=ordered[0].issue_id,
            also_matched=tuple(c.issue_id for c in ordered[1:]),
        )

    return IncidentAction(kind="create")


def _recur(matches: list[IncidentCandidate]) -> IncidentAction:
    ordered = sorted(matches, key=lambda c: _issue_sort_key(c.issue_id))
    return IncidentAction(
        kind="recur",
        target_id=ordered[0].issue_id,
        also_matched=tuple(c.issue_id for c in ordered[1:]),
    )


# --------------------------------------------------------------------------- #
# あいまい照合（助言専用）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FuzzyCandidate:
    """あいまい照合で列挙される関連候補（起票・カウント判断には使わない）。"""

    issue_id: str
    score: float


def compute_fuzzy_candidates(
    signature: IncidentSignature, candidates: list[IncidentCandidate]
) -> list[FuzzyCandidate]:
    """完全一致しなかった候補のうち、同一 exception_type **または** 同一 cause のものに対し
    ``similarity`` を取り、閾値以上を score 降順で最大 ``FUZZY_MAX`` 件列挙する。

    候補側 fingerprint は identity marker parse 時に本文 block から取得済み。block が無い /
    読めない候補（fingerprint 空）は対象から除外する。resolved 済み候補も列挙対象。
    """
    scored: list[FuzzyCandidate] = []
    for cand in candidates:
        sig = cand.signature
        if sig is None or sig.matches(signature):
            continue
        if sig.exception_type != signature.exception_type and sig.cause != signature.cause:
            continue
        if not sig.fingerprint:
            continue
        score = similarity(signature.fingerprint, sig.fingerprint)
        if score >= SIMILARITY_THRESHOLD:
            scored.append(FuzzyCandidate(issue_id=cand.issue_id, score=score))
    scored.sort(key=lambda f: (-f.score, _issue_sort_key(f.issue_id)))
    return scored[:FUZZY_MAX]


# --------------------------------------------------------------------------- #
# ローカル occurrence 記録（I/O 境界）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class OccurrenceRecord:
    """``occurrences.jsonl`` の 1 行。全 provider・全失敗で必ず append する。"""

    schema_version: int
    signature: IncidentSignature
    run_id: str
    source_issue: str
    failed_step: str
    workflow_path: str
    recorded_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "signature": {
                "schema_version": self.signature.schema_version,
                "cause": self.signature.cause,
                "exception_type": self.signature.exception_type,
                "fingerprint": self.signature.fingerprint,
                "fingerprint_hash": self.signature.fingerprint_hash,
            },
            "run_id": self.run_id,
            "source_issue": self.source_issue,
            "failed_step": self.failed_step,
            "workflow_path": self.workflow_path,
            "recorded_at": self.recorded_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> OccurrenceRecord:
        sig_raw = data["signature"]
        if not isinstance(sig_raw, dict):
            raise ValueError("occurrence signature must be a mapping")
        signature = IncidentSignature(
            schema_version=int(str(sig_raw["schema_version"])),
            cause=str(sig_raw["cause"]),
            exception_type=str(sig_raw["exception_type"]),
            fingerprint=str(sig_raw.get("fingerprint", "")),
            fingerprint_hash=str(sig_raw["fingerprint_hash"]),
        )
        return cls(
            schema_version=int(str(data["schema_version"])),
            signature=signature,
            run_id=str(data["run_id"]),
            source_issue=str(data["source_issue"]),
            failed_step=str(data.get("failed_step", "")),
            workflow_path=str(data.get("workflow_path", "")),
            recorded_at=str(data.get("recorded_at", "")),
        )


def occurrences_path(artifacts_dir: Path) -> Path:
    return artifacts_dir / INCIDENTS_DIRNAME / OCCURRENCES_FILE


def append_occurrence(artifacts_dir: Path, record: OccurrenceRecord) -> None:
    """occurrence を jsonl に 1 行 append する（親ディレクトリは必要なら作成）。"""
    path = occurrences_path(artifacts_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        f.flush()


def read_occurrences(artifacts_dir: Path) -> list[OccurrenceRecord]:
    """occurrence jsonl を読む。parse できない行は skip する（fail-open）。"""
    path = occurrences_path(artifacts_dir)
    if not path.is_file():
        return []
    out: list[OccurrenceRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            if isinstance(data, dict):
                out.append(OccurrenceRecord.from_dict(data))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    return out


# --------------------------------------------------------------------------- #
# テンプレート描画（純関数、LLM なし）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class IncidentContext:
    """テンプレート描画・execute に渡す 1 回分の occurrence 情報。"""

    signature: IncidentSignature
    run_id: str
    source_issue: str
    source_issue_ref: str
    failed_step: str
    workflow_path: str
    evidence: tuple[str, ...] = ()
    error_excerpt: str = ""
    fuzzy: tuple[FuzzyCandidate, ...] = ()


def _fuzzy_lines(fuzzy: tuple[FuzzyCandidate, ...]) -> list[str]:
    if not fuzzy:
        return ["- 関連候補なし（あいまい照合の閾値未満）。"]
    return [f"- `#{f.issue_id}`（score {f.score:.2f}）" for f in fuzzy]


def render_incident_issue(
    ctx: IncidentContext, *, regression_of: str | None = None
) -> tuple[str, str]:
    """新規 incident イシューの ``(title, body)`` を返す（identity marker が本文 1 行目）。

    本文に occurrence marker は置かない（count の正本はコメントのみ）。auto-close hazard
    pattern（``Fixes #N`` 等）を含めない。
    """
    sig = ctx.signature
    title = f"incident: {sig.cause} / {sig.exception_type} — {sig.fingerprint[:64]}"
    rows = [
        ("cause", sig.cause),
        ("exception_type", sig.exception_type),
        ("schema", str(sig.schema_version)),
        ("hash", sig.fingerprint_hash),
        ("初回 run_id", ctx.run_id),
        ("発生元 issue", f"`{ctx.source_issue_ref}`"),
        ("failed_step", ctx.failed_step or "n/a"),
        ("workflow", f"`{ctx.workflow_path}`" if ctx.workflow_path else "n/a"),
    ]
    lines = [
        render_identity_marker(sig),
        "",
        "## インシデント概要",
        "",
        "| 項目 | 値 |",
        "|------|----|",
    ]
    lines += [f"| {k} | {v} |" for k, v in rows]
    lines += ["", "## 識別指紋", "", render_fingerprint_block(sig.fingerprint)]
    lines += ["", "## 根拠", ""]
    if ctx.evidence:
        lines += [f"- {sanitize_evidence(item)}" for item in ctx.evidence]
    else:
        lines.append("- 根拠 artifact を収集できなかった。")
    if ctx.error_excerpt:
        lines += ["", "```", sanitize_evidence(ctx.error_excerpt), "```"]
    lines += ["", "## 関連の可能性（助言専用・起票判断には使わない）", ""]
    lines += _fuzzy_lines(ctx.fuzzy)
    if regression_of is not None:
        lines += [
            "",
            "## 関連",
            "",
            f"- 過去に人間が resolve 済みの同一署名イシュー `#{regression_of}` の"
            "リグレッションの可能性がある。",
        ]
    lines += ["", "---", "", f"ラベル運用ガイド: [`{_LABELS_GUIDE}`]({_LABELS_GUIDE})"]
    return title, "\n".join(lines) + "\n"


def render_occurrence_comment(
    ctx: IncidentContext, *, marker_run_ids: list[str], count: int
) -> str:
    """occurrence コメント本文を返す（marker が行頭・1 run_id 1 行、backfill 分を含む）。"""
    sig = ctx.signature
    markers = [
        render_occurrence_marker(sig, run_id=rid, source_issue=ctx.source_issue)
        for rid in marker_run_ids
    ]
    lines = [*markers, "", "## インシデント再発", "", "| 項目 | 値 |", "|------|----|"]
    lines += [
        f"| 今回 run_id | `{ctx.run_id}` |",
        f"| 発生元 issue | `{ctx.source_issue_ref}` |",
        f"| failed_step | `{ctx.failed_step or 'n/a'}` |",
        f"| 再発回数 (N) | `{count}` |",
    ]
    if len(marker_run_ids) > 1:
        backfilled = ", ".join(r for r in marker_run_ids if r != ctx.run_id)
        lines += ["", f"backfill した過去 run_id: {backfilled}"]
    lines += ["", "## 根拠", ""]
    if ctx.evidence:
        lines += [f"- {sanitize_evidence(item)}" for item in ctx.evidence]
    else:
        lines.append("- 根拠 artifact を収集できなかった。")
    if ctx.error_excerpt:
        lines += ["", "```", sanitize_evidence(ctx.error_excerpt), "```"]
    if ctx.fuzzy:
        lines += ["", "## 関連の可能性（助言専用）", ""]
        lines += _fuzzy_lines(ctx.fuzzy)
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# count 導出 / backfill（純関数）
# --------------------------------------------------------------------------- #


def posted_run_ids(comments: list[Comment], fingerprint_hash: str) -> set[str]:
    """対象イシューの全コメント中、hash 一致 valid occurrence marker のユニーク run_id 集合。"""
    seen: set[str] = set()
    for comment in comments:
        for marker in parse_occurrence_markers(comment.body):
            if marker.hash == fingerprint_hash:
                seen.add(marker.run_id)
    return seen


def backfill_run_ids(
    *,
    current_run_id: str,
    local_records: list[OccurrenceRecord],
    fingerprint_hash: str,
    posted: set[str],
) -> list[str]:
    """今回 + ローカル記録のうち、remote に未投稿の run_id を投稿順に列挙する（重複排除）。

    起票失敗 → ローカル記録 → 次回失敗時の照合で拾う、を専用 flush キューなしで成立させる。
    """
    ordered: list[str] = []
    seen: set[str] = set()

    def _add(run_id: str) -> None:
        if run_id and run_id not in seen and run_id not in posted:
            seen.add(run_id)
            ordered.append(run_id)

    _add(current_run_id)
    for rec in local_records:
        if rec.signature.fingerprint_hash == fingerprint_hash:
            _add(rec.run_id)
    return ordered


# --------------------------------------------------------------------------- #
# provider を呼ぶ副作用境界（handler が fail-open で包む）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class IncidentOutcome:
    """execute の結果。``action`` は ``created`` / ``recurred`` / ``regression_created``。"""

    incident_ref: str
    action: str
    count: int
    also_matched: tuple[str, ...] = field(default_factory=tuple)


def execute_incident_action(
    provider: object,
    *,
    action: IncidentAction,
    ctx: IncidentContext,
    local_records: list[OccurrenceRecord],
    existing_comments: list[Comment],
) -> IncidentOutcome:
    """照合結論を provider 操作に落とす。

    - ``create`` / ``create_regression``: ``create_issue`` → 直後に初回 occurrence コメント
      （起票 run の marker + ローカル backfill 分）を投稿する（投稿後 N ≥ 1）。
    - ``recur``: 追記先 1 件へ occurrence コメント 1 通（今回 + backfill markers）を追記する。

    provider は ``create_issue`` / ``comment_issue`` を持つ ``IssueProvider`` を想定する。
    例外は呼び出し側（handler）が fail-open で捕捉する。
    """
    fingerprint_hash = ctx.signature.fingerprint_hash

    if action.kind == "recur":
        target_id = action.target_id
        assert target_id is not None
        posted = posted_run_ids(existing_comments, fingerprint_hash)
        marker_run_ids = backfill_run_ids(
            current_run_id=ctx.run_id,
            local_records=local_records,
            fingerprint_hash=fingerprint_hash,
            posted=posted,
        )
        count = len(posted | set(marker_run_ids))
        # crash window: 今回 run_id が既に投稿済みでも N を汚さない（marker は再投稿しない）。
        if not marker_run_ids:
            marker_run_ids = [ctx.run_id]
        body = render_occurrence_comment(ctx, marker_run_ids=marker_run_ids, count=count)
        comment = provider.comment_issue(target_id, body)  # type: ignore[attr-defined]
        return IncidentOutcome(
            incident_ref=comment.ref or target_id,
            action="recurred",
            count=count,
            also_matched=action.also_matched,
        )

    # create / create_regression
    regression_of = action.regression_of if action.kind == "create_regression" else None
    title, body = render_incident_issue(ctx, regression_of=regression_of)
    issue = provider.create_issue(  # type: ignore[attr-defined]
        title=title,
        body=body,
        labels=[INCIDENT_LABEL, INCIDENT_STATUS_INVESTIGATING],
    )
    # 起票直後の初回 occurrence コメント（remote は空集合 = 全 backfill 対象）。
    marker_run_ids = backfill_run_ids(
        current_run_id=ctx.run_id,
        local_records=local_records,
        fingerprint_hash=fingerprint_hash,
        posted=set(),
    )
    if not marker_run_ids:
        marker_run_ids = [ctx.run_id]
    count = len(set(marker_run_ids))
    comment_body = render_occurrence_comment(ctx, marker_run_ids=marker_run_ids, count=count)
    provider.comment_issue(issue.id, comment_body)  # type: ignore[attr-defined]
    return IncidentOutcome(
        incident_ref=issue.id,
        action="regression_created" if regression_of is not None else "created",
        count=count,
        also_matched=action.also_matched,
    )
