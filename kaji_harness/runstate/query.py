"""Read-only run / series fact queries for monitoring tools (Issue #543).

``kaji watch`` が run と series の内部事実を取得する唯一の境界。中身は #540 の既存の
収集器（``facts`` / ``schema``）の再利用だけで、判定を再実装しない。``kaji status`` の
Issue 指定の経路も ``collect_status`` を通り、CLI と watch が同じ経路を共有する。

facade（``kaji_harness.runstate``）は循環を避けて ``facts`` / ``schema`` を再 export しない
方針のため、本 module も ``kaji_harness.runstate.query`` として直接 import する。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .facts import attempt_dir, collect_issue_facts
from .process import HostProbe
from .schema import JsonDict, issue_to_json, list_series_ids, series_to_json, status_document

__all__ = [
    "StepArtifacts",
    "collect_status",
    "list_series",
    "locate_step_artifacts",
]


@dataclass(frozen=True)
class StepArtifacts:
    """step の artifact の場所（見つからなければ各値 ``None``）。

    Attributes:
        run_dir: run dir の絶対パス。
        attempt_dir: step の attempt dir の絶対パス。
    """

    run_dir: Path | None
    attempt_dir: Path | None


def collect_status(
    artifacts_dir: Path,
    issue_ids: list[str],
    *,
    probe: HostProbe | None = None,
    series: JsonDict | None = None,
    now: datetime | None = None,
) -> JsonDict:
    """``kaji status --json <issue>...`` と同じ schema v1 の文書を返す。

    Args:
        artifacts_dir: ``resolve_artifacts_dir`` の値。
        issue_ids: canonical Issue ID の列（出力順）。
        probe: 生存判定の OS 境界の注入点。
        series: ``--series`` 指定時の series object。
        now: ``generated_at`` の時刻（テスト用）。

    Returns:
        top-level の status 文書。
    """
    documents = [
        issue_to_json(collect_issue_facts(artifacts_dir, issue_id, probe=probe))
        for issue_id in issue_ids
    ]
    return status_document(documents, series=series, now=now)


def list_series(artifacts_dir: Path) -> list[JsonDict]:
    """全 series の ``series`` object（``kaji status --json --series <id>`` と同じ形）を返す。"""
    return [
        series_to_json(artifacts_dir, series_id)[0] for series_id in list_series_ids(artifacts_dir)
    ]


def locate_step_artifacts(
    artifacts_dir: Path,
    issue_id: str,
    run_id: str | None,
    step_id: str | None,
    attempt: int | None,
) -> StepArtifacts:
    """run dir と step の attempt dir を解決する。

    Args:
        artifacts_dir: ``resolve_artifacts_dir`` の値。
        issue_id: canonical Issue ID。
        run_id: run の ID。``None`` なら両方 ``None``。
        step_id: step の ID。``None`` なら ``attempt_dir`` は ``None``。
        attempt: attempt 番号。無ければ最新の attempt。

    Returns:
        見つかった場所。存在しないものは ``None``。
    """
    if run_id is None:
        return StepArtifacts(run_dir=None, attempt_dir=None)
    run_dir = artifacts_dir / issue_id / "runs" / run_id
    if not run_dir.is_dir():
        return StepArtifacts(run_dir=None, attempt_dir=None)
    if step_id is None:
        return StepArtifacts(run_dir=run_dir, attempt_dir=None)
    return StepArtifacts(run_dir=run_dir, attempt_dir=attempt_dir(run_dir, step_id, attempt))
