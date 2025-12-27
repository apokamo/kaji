"""Base workflow class."""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Callable

from src.core.verdict import Verdict

# Type alias for state handlers
StateHandler = Callable[["AgentContext", "SessionState"], Enum]


class AgentContext:
    """Context for agent execution.

    Holds references to AI tools and other shared resources.
    """

    # TODO: Implement with actual tool references
    pass


class SessionState:
    """Runtime session state.

    Tracks execution progress, loop counters, and conversation IDs.
    """

    def __init__(self) -> None:
        self.completed_states: list[str] = []
        self.loop_counters: dict[str, int] = {}
        self.active_conversations: dict[str, str | None] = {}


class WorkflowBase(ABC):
    """Base class for all workflows.

    Subclasses must implement all abstract methods to define
    their specific state machine and handlers.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Workflow name (e.g., 'bugfix', 'design', 'implement')."""
        ...

    @property
    @abstractmethod
    def states(self) -> type[Enum]:
        """State enumeration type for this workflow."""
        ...

    @property
    @abstractmethod
    def initial_state(self) -> Enum:
        """Initial state when workflow starts."""
        ...

    @property
    @abstractmethod
    def terminal_states(self) -> set[Enum]:
        """Set of states that end the workflow."""
        ...

    @abstractmethod
    def get_handler(self, state: Enum) -> StateHandler:
        """Get the handler function for a state.

        Args:
            state: The state to get handler for

        Returns:
            Handler function that processes the state
        """
        ...

    @abstractmethod
    def get_next_state(self, current: Enum, verdict: Verdict) -> Enum:
        """Determine next state based on current state and verdict.

        Args:
            current: Current state
            verdict: VERDICT from AI output

        Returns:
            Next state to transition to
        """
        ...

    @abstractmethod
    def get_prompt_path(self, state: Enum) -> str:
        """Get the prompt file path for a state.

        Args:
            state: The state to get prompt for

        Returns:
            Relative path to prompt file
        """
        ...

    def validate_transition(self, current: Enum, next_state: Enum) -> bool:
        """Validate if a state transition is allowed.

        Args:
            current: Current state
            next_state: Proposed next state

        Returns:
            True if transition is valid
        """
        # Default implementation: allow all transitions
        # Subclasses can override for stricter validation
        return True
