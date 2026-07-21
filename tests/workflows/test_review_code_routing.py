"""Routing invariants for the review-code step across all workflows.

Issue: #192 — issue-review-code Step 1.4 hard gate (Pre-Handoff Review 証跡欠落)
が発行する差し戻し verdict は design ではなく implement へ向かわなければならない。
過去のバグでは Step 1.4 が bare `BACK` を発行し、`review-code.on.BACK: design`
routing が approve 済み設計を再起動して ABORT を誘発した。

ここでは bare `BACK`（設計問題用）と `BACK_IMPLEMENT`（Step 1.4 用）の routing
不変条件を、official workflow セット（`.kaji/wf/official/**/*.yaml`）について機械的に
検証する。custom workflow は利用者所有のため対象外とする。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kaji_harness.workflow import load_workflow

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

WORKFLOW_PATHS = sorted(
    (REPO_ROOT / ".kaji" / "wf" / "official").rglob("*.yaml"),
)


def _review_code_workflows() -> list[Path]:
    """review-code skill を採用する workflow ファイルのみを返す。"""
    paths: list[Path] = []
    for path in WORKFLOW_PATHS:
        wf = load_workflow(path)
        if any(step.skill == "issue-review-code" for step in wf.steps):
            paths.append(path)
    return paths


REVIEW_CODE_WORKFLOW_PATHS = _review_code_workflows()
REVIEW_CODE_WORKFLOW_IDS = [str(p.relative_to(REPO_ROOT)) for p in REVIEW_CODE_WORKFLOW_PATHS]


class TestReviewCodeRouting:
    """Medium: review-code routing 不変条件（repo 上の official YAML を読む）。"""

    @pytest.mark.medium
    def test_at_least_one_review_code_workflow_exists(self) -> None:
        """検証対象が空でないこと（glob 誤りで silently skip するのを防ぐ）。"""
        assert REVIEW_CODE_WORKFLOW_PATHS, (
            "review-code を採用する workflow が 1 つも見つからない。"
            "WORKFLOW_PATHS の glob を確認すること。"
        )

    @pytest.mark.medium
    @pytest.mark.parametrize("path", REVIEW_CODE_WORKFLOW_PATHS, ids=REVIEW_CODE_WORKFLOW_IDS)
    def test_review_code_has_back_implement(self, path: Path) -> None:
        """review-code.on は BACK_IMPLEMENT key を持つ（Step 1.4 差し戻し経路）。"""
        wf = load_workflow(path)
        review_code = next(s for s in wf.steps if s.skill == "issue-review-code")
        assert "BACK_IMPLEMENT" in review_code.on, (
            f"{path.name}: review-code.on に BACK_IMPLEMENT が無い。"
            "Step 1.4 hard gate の差し戻し先が定義されていない。"
        )

    @pytest.mark.medium
    @pytest.mark.parametrize("path", REVIEW_CODE_WORKFLOW_PATHS, ids=REVIEW_CODE_WORKFLOW_IDS)
    def test_back_implement_routes_to_implement_step(self, path: Path) -> None:
        """BACK_IMPLEMENT の routing 先が issue-implement step を指す。"""
        wf = load_workflow(path)
        review_code = next(s for s in wf.steps if s.skill == "issue-review-code")
        target_id = review_code.on["BACK_IMPLEMENT"]
        target = wf.find_step(target_id)
        assert target is not None, (
            f"{path.name}: BACK_IMPLEMENT routing 先 '{target_id}' が存在しない。"
        )
        assert target.skill == "issue-implement", (
            f"{path.name}: BACK_IMPLEMENT は implement step を指すべきだが "
            f"'{target_id}' は skill={target.skill!r} を指している。"
        )

    @pytest.mark.medium
    @pytest.mark.parametrize("path", REVIEW_CODE_WORKFLOW_PATHS, ids=REVIEW_CODE_WORKFLOW_IDS)
    def test_bare_back_routes_to_design_step(self, path: Path) -> None:
        """bare BACK key がある場合、その先は issue-design step を指す。"""
        wf = load_workflow(path)
        review_code = next(s for s in wf.steps if s.skill == "issue-review-code")
        if "BACK" not in review_code.on:
            pytest.skip(f"{path.name}: review-code に bare BACK key 無し（design 用途未使用）")
        target_id = review_code.on["BACK"]
        target = wf.find_step(target_id)
        assert target is not None, f"{path.name}: bare BACK routing 先 '{target_id}' が存在しない。"
        assert target.skill == "issue-design", (
            f"{path.name}: bare BACK は design step を指すべきだが "
            f"'{target_id}' は skill={target.skill!r} を指している。"
        )
