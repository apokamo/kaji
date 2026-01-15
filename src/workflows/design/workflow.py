"""Design workflow implementation."""

from enum import Enum

from src.core.verdict import Verdict
from src.workflows.base import AgentContext, SessionState, StateHandler, WorkflowBase

from .states import DesignState


class DesignWorkflow(WorkflowBase):
    """Design workflow: DESIGN <-> DESIGN_REVIEW loop.

    This workflow focuses on creating and refining detailed designs
    based on requirements input.
    """

    @property
    def name(self) -> str:
        return "design"

    @property
    def states(self) -> type[Enum]:
        return DesignState

    @property
    def initial_state(self) -> Enum:
        return DesignState.DESIGN

    @property
    def terminal_states(self) -> set[Enum]:
        return {DesignState.COMPLETE}

    def get_handler(self, state: Enum) -> StateHandler:
        """Get handler for the given state."""
        handlers: dict[DesignState, StateHandler] = {
            DesignState.DESIGN: self._handle_design,
            DesignState.DESIGN_REVIEW: self._handle_design_review,
        }
        if state not in handlers:
            raise ValueError(f"No handler for state: {state}")
        return handlers[state]

    def get_next_state(self, current: Enum, verdict: Verdict) -> Enum:
        """Determine next state based on verdict."""
        transitions: dict[tuple[DesignState, Verdict], DesignState] = {
            # DESIGN always goes to DESIGN_REVIEW
            (DesignState.DESIGN, Verdict.PASS): DesignState.DESIGN_REVIEW,
            # DESIGN_REVIEW outcomes
            (DesignState.DESIGN_REVIEW, Verdict.PASS): DesignState.COMPLETE,
            (DesignState.DESIGN_REVIEW, Verdict.RETRY): DesignState.DESIGN,
        }
        key = (current, verdict)
        if key not in transitions:
            raise ValueError(f"Invalid transition: {current} + {verdict}")
        return transitions[key]

    def get_prompt_path(self, state: Enum) -> str:
        """Get prompt file path for the state."""
        prompt_files: dict[DesignState, str] = {
            DesignState.DESIGN: "workflows/design/prompts/design.md",
            DesignState.DESIGN_REVIEW: "workflows/design/prompts/design_review.md",
        }
        if state not in prompt_files:
            raise ValueError(f"No prompt for state: {state}")
        return prompt_files[state]

    def _handle_design(self, ctx: AgentContext, session: SessionState) -> Enum:
        """Handle DESIGN state.

        TODO: Implement actual design generation logic.
        """
        # Placeholder - will call analyzer AI
        return DesignState.DESIGN_REVIEW

    def _handle_design_review(self, ctx: AgentContext, session: SessionState) -> Enum:
        """Handle DESIGN_REVIEW state.

        TODO: Implement actual design review logic.
        """
        # Placeholder - will call reviewer AI and parse verdict
        return DesignState.COMPLETE
