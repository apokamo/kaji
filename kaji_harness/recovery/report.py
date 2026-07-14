"""Triage report rendering (Issue #288).

Issue コメント本文と stderr サマリを、``RecoveryDecision`` から機械生成する純関数群。
LLM は使わない（決定 6）。自由記述は cause ごとの固定文面に限定し、可変部は構造化
フィールドの埋め込みだけにする。

引用文字列は必ず ``sanitize_evidence()`` を通す。credential 形跡を伏字化し、
``EVIDENCE_LIMIT`` 文字で切ることで、stderr に混入した token を Issue へ転写しない。
本文は auto-close hazard pattern（``Fixes #N`` 等）を含まない
（docs/dev/shared_skill_rules.md § auto close keyword 回避規約）。
"""

from __future__ import annotations

import re

from .models import RECOVERY_BUDGET, RECOVERY_WAIT_SECONDS, RecoveryDecision

#: 各根拠の引用上限（文字数）。
EVIDENCE_LIMIT = 500

_MASK = "***"

#: credential 形跡の伏字化ルール。順に適用する。
_CREDENTIAL_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{4,}"), _MASK),
    (re.compile(r"github_pat_[A-Za-z0-9_]{4,}"), _MASK),
    (re.compile(r"(?i)\bbearer\s+\S+"), f"Bearer {_MASK}"),
    (
        re.compile(r"(?i)\b(authorization|api[-_]?key|secret|token|password)\b(\s*[:=]\s*)\S+"),
        rf"\1\2{_MASK}",
    ),
]

_CAUSE_DESCRIPTIONS: dict[str, str] = {
    "dispatch_failure": (
        "step の dispatch が異常終了した（CLI / script プロセスの失敗、または timeout）。"
        "attempt-level retry は既に使い切られている。"
    ),
    "verdict_resolution_failure": (
        "dispatch は成功したが、verdict を artifact / comment / stdout のいずれからも"
        "解決できなかった。"
    ),
    "cycle_exhausted": (
        "cycle が `max_iterations` に到達した。これは安全弁の正常作動であり、"
        "自動再開の対象にしない。"
    ),
    "agent_declared_abort": (
        "agent が正規の ABORT verdict を返した。安全停止・手動確認要求であり、"
        "自動再開の対象にしない。"
    ),
    "ambiguous_worktree_abort": (
        "同一 Issue に複数の worktree が該当したため、runner が dispatch 前に停止した。"
    ),
    "config_or_definition_error": (
        "config / workflow 定義 / workdir / resume session の不備で run が終了した。"
        "入力を修正しない限り再実行しても同じ結果になる。"
    ),
    "kaji_bug_suspected": (
        "run artifact と runner event の間に決定論的な矛盾を検出した。"
        "kaji harness 側の不具合が疑われる。"
    ),
    "runtime_error": "上記いずれにも該当しない例外で run が終了した。",
    "unknown_external_error": (
        "外部 CLI / provider 由来と判別できるが、既知 pattern に一致しない opaque な"
        "エラーで終了した。解釈せずエラー文字列をそのまま引用する。"
    ),
    "user_precondition_error": (
        "実行前提を満たさない状態で run を起動した（既知のユーザー操作ミス）。"
        "原因と対処はエラー文に含まれており、障害調査を要さないため incident 起票の"
        "対象外とする。前提を満たして再実行すれば解消する。"
    ),
    "external_upstream_anomaly": "外部 upstream の異常（予約分類）。",
}


def mask_secrets(text: str) -> str:
    """credential 形跡を伏字化する。"""
    masked = text
    for pattern, replacement in _CREDENTIAL_RULES:
        masked = pattern.sub(replacement, masked)
    return masked


def truncate(text: str, limit: int = EVIDENCE_LIMIT) -> str:
    """``limit`` 文字を超える場合は切り詰めて省略記号を付ける。"""
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def sanitize_evidence(text: str, limit: int = EVIDENCE_LIMIT) -> str:
    """masking → truncation の順で引用文字列を整形する。

    truncation を後段にするのは、切り詰めで壊れた token の断片が伏字化を素通り
    しないようにするため。
    """
    return truncate(mask_secrets(text), limit)


def _na(value: str | None) -> str:
    return value if value else "n/a"


def _auto_recovery_cell(decision: RecoveryDecision) -> str:
    attempted = "true" if decision.auto_recovery_attempted else "false"
    return f"attempted: {attempted}, attempt_no: {decision.auto_recovery_attempt_no}"


def _next_action_lines(decision: RecoveryDecision) -> list[str]:
    """cause / decision 別の固定次アクション候補。"""
    cause = decision.classification.cause
    command = decision.resume_command
    lines: list[str] = []
    match decision.decision:
        case "resume":
            wait_minutes = RECOVERY_WAIT_SECONDS // 60
            lines.append(
                f"- 自動再開が予約されている。`{decision.resume_scheduled_at}` "
                f"（決定時刻 + {wait_minutes} 分）に child run を起動する。"
            )
            lines.append("- 先に手動で `kaji run` を起動した場合、自動再開は中止される。")
        case "exhausted":
            lines.append(
                f"- この recovery chain は自動再開の budget（1 chain {RECOVERY_BUDGET} 回）を"
                "消費済み。分類が誤っている可能性を人手で確認する。"
            )
        case "cancelled_newer_run_detected":
            lines.append(
                "- より新しい run が起動済みのため自動再開を中止した。そちらの結果を確認する。"
            )
        case "cancelled_interrupted":
            lines.append("- ウェイト中に中断された。必要なら手動で再開コマンドを実行する。")
        case "bug_issue_created":
            lines.append("- 起票した bug issue で harness 側の不具合を追跡する。")
        case _:
            pass

    if cause == "cycle_exhausted":
        lines.append(
            "- cycle 上限の解除が妥当と判断できる場合のみ、手動で "
            "`kaji run <workflow> <issue> --from <step> --reset-cycle` を実行する"
            "（自動付与はしない）。"
        )
    if command and decision.decision != "resume":
        lines.append(f"- 手動再開コマンド候補: `{command}`")
    if decision.discarded_resume_session:
        lines.append(
            "- `resume:` step の session 引継ぎを破棄し、session 生成元 step から再開する。"
        )
    if not lines:
        lines.append("- run artifact を確認し、原因を解消してから手動で再実行する。")
    if cause not in ("cycle_exhausted",):
        lines.append(
            "- cycle 残量が不足する場合は手動で `--reset-cycle` の要否を検討する"
            "（handler は自動付与しない）。"
        )
    return lines


def render_triage_comment(*, decision: RecoveryDecision, issue_ref: str) -> str:
    """Issue に投稿する機械生成 triage report を返す。

    kaji-verdict マーカーは付与しない（step verdict ではないため、``issue-design``
    Step 1.6 の BACK 検出母集団を汚さない）。
    """
    classification = decision.classification
    rows = [
        ("workflow", f"`{_na(decision.workflow_path)}`"),
        ("issue", f"`{issue_ref}`"),
        ("run_id", f"`{decision.run_id}`"),
        ("recovery_root_run_id", f"`{_na(decision.recovery_root_run_id)}`"),
        ("recovery_parent_run_id", f"`{_na(decision.recovery_parent_run_id)}`"),
        ("failed_step", f"`{_na(decision.failed_step)}`"),
        ("classification", f"`{classification.cause}`"),
        ("synthetic", f"`{str(classification.synthetic).lower()}`"),
        ("decision", f"`{decision.decision}`"),
        ("auto_recovery", f"`{_auto_recovery_cell(decision)}`"),
        ("resume_command", f"`{_na(decision.resume_command)}`"),
        ("resume_scheduled_at", f"`{_na(decision.resume_scheduled_at)}`"),
        ("discarded_resume_session", f"`{str(decision.discarded_resume_session).lower()}`"),
        ("child_run_status", f"`{decision.recovery_child_final_status or 'pending'}`"),
    ]

    lines = ["## Workflow failure triage", "", "| 項目 | 値 |", "|------|----|"]
    lines += [f"| {key} | {value} |" for key, value in rows]

    lines += ["", "### 原因（機械判定）", ""]
    lines.append(_CAUSE_DESCRIPTIONS[classification.cause])
    if decision.reason:
        lines += ["", f"判定理由: {sanitize_evidence(decision.reason)}"]
    if decision.bug_issue:
        bug_id = decision.bug_issue.get("id", "")
        bug_url = decision.bug_issue.get("url", "")
        suffix = f" ({bug_url})" if bug_url else ""
        lines += ["", f"起票した bug issue: `{bug_id}`{suffix}"]

    lines += ["", "### 根拠", ""]
    if decision.evidence:
        lines += [f"- {sanitize_evidence(item)}" for item in decision.evidence]
    else:
        lines.append("- 根拠 artifact を収集できなかった。")

    lines += ["", "### 次アクション", ""]
    lines += _next_action_lines(decision)
    return "\n".join(lines) + "\n"


def render_child_result_comment(*, decision: RecoveryDecision, issue_ref: str) -> str:
    """自動再開した child run の終了結果を報告する follow-up コメントを返す。

    triage コメントは child 起動前に投稿するため ``child_run_status`` が常に ``pending``
    になる。Issue から自動再開の成否を追跡できるよう、child 終了後にこの 1 通を足す。
    """
    rows = [
        ("issue", f"`{issue_ref}`"),
        ("run_id", f"`{decision.run_id}`"),
        ("recovery_root_run_id", f"`{_na(decision.recovery_root_run_id)}`"),
        ("child_run_id", f"`{_na(decision.recovery_child_run_id)}`"),
        ("child_run_status", f"`{_na(decision.recovery_child_final_status)}`"),
        ("resume_started_at", f"`{_na(decision.resume_started_at)}`"),
    ]
    lines = ["## Workflow auto recovery result", "", "| 項目 | 値 |", "|------|----|"]
    lines += [f"| {key} | {value} |" for key, value in rows]
    lines += [
        "",
        "自動再開の budget（1 recovery chain "
        f"{RECOVERY_BUDGET} 回）は消費済み。以降の再開は手動で判断する。",
    ]
    return "\n".join(lines) + "\n"


def render_stderr_summary(decision: RecoveryDecision) -> str:
    """既存終端表示の直後に出す数行の failure triage サマリを返す。"""
    classification = decision.classification
    lines = [
        "--- failure triage ---",
        f"failed_step:    {_na(decision.failed_step)}",
        f"classification: {classification.cause} "
        f"(synthetic={str(classification.synthetic).lower()})",
        f"decision:       {decision.decision}",
        f"comment:        {_na(decision.triage_comment_ref)}",
    ]
    if decision.resume_scheduled_at:
        lines.append(f"resume_scheduled_at: {decision.resume_scheduled_at}")
    action = decision.resume_command or "run artifact を確認して手動で再実行する"
    lines.append(f"next action:    {action}")
    return "\n".join(lines) + "\n"
