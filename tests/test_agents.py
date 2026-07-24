"""Tests for the agent capability registry."""

from __future__ import annotations

import pytest

from kaji_harness.agents import AGENT_CAPABILITIES


@pytest.mark.small
def test_agent_capability_registry_matches_public_contract() -> None:
    """全 agent の capability と既存挙動を一元的に固定する。"""
    assert set(AGENT_CAPABILITIES) == {"claude", "codex", "antigravity"}

    antigravity = AGENT_CAPABILITIES["antigravity"]
    assert antigravity.binary == "agy"
    assert antigravity.supports_resume is False
    assert antigravity.supports_interactive_terminal is True
    assert antigravity.emits_jsonl is False
    assert antigravity.effort_allowed == frozenset({"low", "medium", "high"})

    assert AGENT_CAPABILITIES["claude"].supports_resume is True
    assert AGENT_CAPABILITIES["codex"].supports_interactive_terminal is True
