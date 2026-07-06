"""Self-RETRY step の cycle 所属不変条件（Issue #259 項目 3）。

runner は cycle 経由でのみ RETRY 上限を enforce する
(``kaji_harness/runner.py``: increment 条件は
``cycle and current_step.id == cycle.loop[-1] and verdict.status == "RETRY"``)。
したがって ``on.RETRY == 自 step id`` の self-RETRY edge を持つ step が
いずれの cycle の ``loop[-1]`` にも所属していないと、その step の RETRY は
一切カウントされず ``max_iterations`` が効かない（無限ループの恐れ）。

#247 で新設した local 系 workflow (dev-local / docs-local) は dev.yaml が持つ
``implementation`` / ``final-check`` の 1-step cycle を移植し漏れており、この
不変条件に違反していた。本テストは canonical workflow セット
(``.kaji/wf/*.yaml``) 全体に対し「self-RETRY step は cycle.loop 末尾に所属する」
ことを機械的に検証し、同種の移植漏れを再発防止する。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kaji_harness.workflow import load_workflow

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

WORKFLOW_PATHS = sorted((REPO_ROOT / ".kaji" / "wf").glob("*.yaml"))
WORKFLOW_IDS = [str(p.relative_to(REPO_ROOT)) for p in WORKFLOW_PATHS]


@pytest.mark.small
class TestSelfRetryCycleMembership:
    """Small: self-RETRY step の cycle 所属不変条件（YAML パース + assertion のみ）。"""

    def test_workflow_set_not_empty(self) -> None:
        """検証対象が空でないこと（glob 誤りで silently skip するのを防ぐ）。"""
        assert WORKFLOW_PATHS, (
            "`.kaji/wf/` に workflow が 1 つも見つからない。WORKFLOW_PATHS の glob を確認すること。"
        )

    @pytest.mark.parametrize("path", WORKFLOW_PATHS, ids=WORKFLOW_IDS)
    def test_self_retry_steps_are_cycle_tail(self, path: Path) -> None:
        """self-RETRY edge を持つ step は、いずれかの cycle の loop 末尾に所属する。

        runner の increment 条件 (``current_step.id == cycle.loop[-1]``) と対になる
        不変条件。違反すると RETRY がカウントされず ``max_iterations`` が効かない。
        """
        wf = load_workflow(path)
        cycle_tails = {cycle.loop[-1] for cycle in wf.cycles if cycle.loop}

        violations: list[str] = []
        for step in wf.steps:
            if step.on.get("RETRY") == step.id and step.id not in cycle_tails:
                violations.append(step.id)

        assert not violations, (
            f"{path.name}: self-RETRY edge を持つが cycle.loop 末尾に所属しない step が"
            f" 存在する: {sorted(violations)}。runner は cycle 経由でのみ RETRY 上限を"
            " enforce するため、これらの step は無限ループしうる。"
            " 該当 step を 1-step cycle (loop 末尾) に所属させるか、dead な"
            " self-RETRY edge を除去すること。"
        )
