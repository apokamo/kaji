"""Small tests for ``build_kaji_verdict_marker`` (Issue #261).

検証観点: マーカー文字列の決定性と語彙検証の fail-loud 性（純粋関数・外部依存
なし）。producer/consumer 契約の正本が CLI/harness 層（コード）にあることの
回帰保護（ADR 008 決定 3）。
"""

from __future__ import annotations

import pytest

from kaji_harness.providers.markers import build_kaji_verdict_marker

pytestmark = pytest.mark.small


class TestBuildKajiVerdictMarker:
    """マーカー合成の決定性。"""

    def test_returns_single_line_no_newline(self) -> None:
        marker = build_kaji_verdict_marker("review-code", "BACK")
        assert marker == "<!-- kaji-verdict: step=review-code status=BACK -->"
        assert "\n" not in marker

    def test_step_and_status_embedded_verbatim(self) -> None:
        marker = build_kaji_verdict_marker("final-check", "BACK_DESIGN")
        assert "step=final-check" in marker
        assert "status=BACK_DESIGN" in marker


class TestStatusVocabulary:
    """status 語彙の受理 / 拒否（workflow-authoring.md の BACK_* 文法と整合）。"""

    @pytest.mark.parametrize(
        "status",
        ["PASS", "RETRY", "ABORT", "BACK", "BACK_DESIGN", "BACK_IMPLEMENT", "BACK_FALLBACK"],
    )
    def test_accepts_standard_and_back_extensions(self, status: str) -> None:
        marker = build_kaji_verdict_marker("design", status)
        assert f"status={status}" in marker

    @pytest.mark.parametrize(
        "status",
        [
            "back",  # lowercase
            "Back",  # mixed-case
            "BACK_",  # suffix 空
            "BACK_design",  # lowercase suffix
            "",  # 空文字
            "APPROVE",  # 未知語
            "PASS ",  # 末尾スペース
            "BACK DESIGN",  # スペース入り
        ],
    )
    def test_rejects_invalid_status(self, status: str) -> None:
        with pytest.raises(ValueError, match="invalid verdict status"):
            build_kaji_verdict_marker("design", status)


class TestStepVocabulary:
    """step 語彙の受理 / 拒否。"""

    @pytest.mark.parametrize(
        "step", ["review-code", "final-check", "design", "implement", "a0_b-c"]
    )
    def test_accepts_lowercase_identifier(self, step: str) -> None:
        marker = build_kaji_verdict_marker(step, "PASS")
        assert f"step={step}" in marker

    @pytest.mark.parametrize(
        "step",
        [
            "Review-Code",  # 大文字
            "",  # 空文字
            "1step",  # 先頭数字
            "review code",  # スペース入り
            "-lead",  # 先頭ハイフン
        ],
    )
    def test_rejects_invalid_step(self, step: str) -> None:
        with pytest.raises(ValueError, match="invalid verdict step"):
            build_kaji_verdict_marker(step, "PASS")
