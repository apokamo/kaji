"""Deterministic helpers for ``/run-watched`` (Issue #543 A2・A3).

本 module は ``plan`` の分類・承認の要否・workflow の解決・series-id の生成を担う。
``execute``（commit / push / ラベル / タブ）の段は後続の実装で追加する。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml

ExclusionReason = Literal[
    "closed",
    "state_label",
    "state_conflict",
    "workflow_mismatch",
    "wf_unresolvable",
    "wf_ambiguous",
    "run_alive_or_unknown",
    "worktree_or_pr",
]
_BLOCKING_STATE_LABELS = frozenset(
    {
        "state:planned",
        "state:running",
        "state:aborted",
        "state:done",
        "state:hold",
        "state:needs-maintainer",
    }
)


class WorkflowResolutionError(ValueError):
    """workflow を一意に解決できない。

    Attributes:
        reason: ``unresolvable`` / ``ambiguous``。
    """

    def __init__(self, reason: Literal["unresolvable", "ambiguous"], message: str) -> None:
        self.reason = reason
        super().__init__(message)


def _workflow_files(wf_root: Path) -> list[Path]:
    return sorted(path for path in wf_root.rglob("*.yaml") if path.is_file())


def resolve_workflow_arg(value: str, repo_root: Path) -> Path:
    """引数（stem・ファイル名・パス）を workflow YAML に解決する。

    Raises:
        WorkflowResolutionError: 0 個（unresolvable）または 2 個以上（ambiguous）に一致する場合。
    """
    candidate = (repo_root / value).resolve()
    if ("/" in value or value.endswith(".yaml")) and candidate.is_file():
        return candidate
    stem = Path(value).name.removesuffix(".yaml")
    matches = [p for p in _workflow_files(repo_root / ".kaji" / "wf") if p.stem == stem]
    if not matches:
        raise WorkflowResolutionError("unresolvable", f"workflow not found: {value}")
    if len(matches) > 1:
        raise WorkflowResolutionError("ambiguous", f"workflow is ambiguous: {value}")
    return matches[0]


def resolve_wf_label(name: str, repo_root: Path) -> Path:
    """``wf:<name>`` を ``name:`` が一致する workflow YAML（ちょうど 1 個）に解決する。

    Raises:
        WorkflowResolutionError: 0 個（unresolvable）または 2 個以上（ambiguous）の場合。
    """
    matches: list[Path] = []
    for path in _workflow_files(repo_root / ".kaji" / "wf"):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(data, dict) and data.get("name") == name:
            matches.append(path)
    if not matches:
        raise WorkflowResolutionError("unresolvable", f"wf:{name} matches no workflow")
    if len(matches) > 1:
        raise WorkflowResolutionError("ambiguous", f"wf:{name} matches several workflows")
    return matches[0]


def make_series_id(issues: list[int], now: datetime) -> str:
    """``rw-<最初の issue>-<最後の issue>-<YYMMDDHHMM>`` を返す。"""
    return f"rw-{issues[0]}-{issues[-1]}-{now.strftime('%y%m%d%H%M')}"


@dataclass(frozen=True)
class IssueFacts:
    """``plan`` の分類に使う 1 Issue の事実。

    Attributes:
        issue: Issue 番号。
        state: ``open`` / ``closed``。
        labels: 付いているラベル。
        any_run_alive: #540 の値（``None`` は判定不能）。
        launching: #540 の値（``None`` は判定不能）。
        has_worktree_or_pr: worktree または open の PR が残っている。
    """

    issue: int
    state: str
    labels: tuple[str, ...]
    any_run_alive: bool | None
    launching: bool | None
    has_worktree_or_pr: bool


def classify(
    facts: IssueFacts, *, workflow_arg: Path | None, wf_label_path: Path | None
) -> ExclusionReason | None:
    """除外の理由（なければ ``None``）を返す。

    Args:
        facts: Issue の事実。
        workflow_arg: 引数で指定された workflow（解決済み）。
        wf_label_path: ``wf:*`` ラベルから解決した workflow（ラベルがなければ ``None``）。
    """
    if facts.state.lower() != "open":
        return "closed"
    state_labels = [label for label in facts.labels if label.startswith("state:")]
    if len(state_labels) >= 2:
        return "state_conflict"
    if any(label in _BLOCKING_STATE_LABELS for label in state_labels):
        return "state_label"
    if workflow_arg is not None and wf_label_path is not None and workflow_arg != wf_label_path:
        return "workflow_mismatch"
    if facts.any_run_alive is not False or facts.launching is not False:
        return "run_alive_or_unknown"
    if facts.has_worktree_or_pr:
        return "worktree_or_pr"
    return None


def approval_required(*, chosen_by_skill: bool, order_ambiguous: bool, excluded: int) -> bool:
    """承認が要るか（skill が workflow を選んだ・順番が曖昧・除外が 1 件以上）。"""
    return chosen_by_skill or order_ambiguous or excluded > 0
