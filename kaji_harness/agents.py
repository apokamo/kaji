"""Agent capability registry for kaji_harness."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentCapabilities:
    """公開 agent の実行 capability を表す。

    Attributes:
        binary: 実行する CLI binary 名。
        supports_resume: workflow の resume を実行できるか。
        supports_interactive_terminal: interactive terminal runnerを利用できるか。
        emits_jsonl: stdout が JSONL event stream か。
        effort_allowed: 許容する effort 値。None は検証を行わない。
    """

    binary: str
    supports_resume: bool
    supports_interactive_terminal: bool
    emits_jsonl: bool
    effort_allowed: frozenset[str] | None


AGENT_CAPABILITIES: dict[str, AgentCapabilities] = {
    "claude": AgentCapabilities(
        binary="claude",
        supports_resume=True,
        supports_interactive_terminal=True,
        emits_jsonl=True,
        effort_allowed=frozenset({"low", "medium", "high", "xhigh", "max"}),
    ),
    "codex": AgentCapabilities(
        binary="codex",
        supports_resume=True,
        supports_interactive_terminal=True,
        emits_jsonl=True,
        effort_allowed=frozenset({"none", "minimal", "low", "medium", "high", "xhigh"}),
    ),
    "antigravity": AgentCapabilities(
        binary="agy",
        supports_resume=False,
        supports_interactive_terminal=True,
        emits_jsonl=False,
        effort_allowed=frozenset({"low", "medium", "high"}),
    ),
}
