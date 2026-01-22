"""Base workflow class."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.core.context import AgentContext
from src.core.verdict import Verdict

# Type alias for state handlers
StateHandler = Callable[[AgentContext, "SessionState"], Enum]

__all__ = [
    "AgentContext",
    "SessionState",
    "StateHandler",
    "WorkflowBase",
]


@dataclass
class SessionState:
    """Runtime session state.

    Tracks execution progress, loop counters, and conversation IDs.

    Attributes:
        completed_states: List of completed state names.
        loop_counters: Dictionary of state name to loop count.
        active_conversations: Dictionary of role to conversation ID.
        max_loop_count: Maximum number of loops allowed per state.
    """

    completed_states: list[str] = field(default_factory=list)
    loop_counters: dict[str, int] = field(default_factory=dict)
    active_conversations: dict[str, str | None] = field(default_factory=dict)
    max_loop_count: int = 3
    _context: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate initialization parameters."""
        if self.max_loop_count < 0:
            raise ValueError(f"max_loop_count must be >= 0, got {self.max_loop_count}")

    def increment_loop(self, state_name: str) -> int:
        """Increment loop counter for a state and return the new value.

        Args:
            state_name: Name of the state to increment counter for.

        Returns:
            New loop count after increment.
        """
        self.loop_counters[state_name] = self.loop_counters.get(state_name, 0) + 1
        return self.loop_counters[state_name]

    def reset_loop(self, state_name: str) -> None:
        """Reset loop counter for a state to 0.

        Note: This sets the counter to 0, not deletes the key.

        Args:
            state_name: Name of the state to reset counter for.
        """
        self.loop_counters[state_name] = 0

    def is_loop_exceeded(self, state_name: str) -> bool:
        """Check if loop count has reached or exceeded the maximum.

        Args:
            state_name: Name of the state to check.

        Returns:
            True if counter >= max_loop_count, False otherwise.
        """
        return self.loop_counters.get(state_name, 0) >= self.max_loop_count

    def set_conversation_id(self, role: str, conv_id: str | None) -> None:
        """Set conversation ID for a role.

        Args:
            role: Agent role name (e.g., 'reviewer', 'implementer').
            conv_id: Conversation ID or None to clear.
        """
        self.active_conversations[role] = conv_id

    def get_conversation_id(self, role: str) -> str | None:
        """Get conversation ID for a role.

        Args:
            role: Agent role name.

        Returns:
            Conversation ID or None if not set.
        """
        return self.active_conversations.get(role)

    def mark_completed(self, state_name: str) -> None:
        """Mark a state as completed.

        Duplicate calls for the same state are ignored.

        Args:
            state_name: Name of the state to mark as completed.
        """
        if state_name not in self.completed_states:
            self.completed_states.append(state_name)

    def is_completed(self, state_name: str) -> bool:
        """Check if a state has been completed.

        Args:
            state_name: Name of the state to check.

        Returns:
            True if state is completed, False otherwise.
        """
        return state_name in self.completed_states

    def set_context(self, key: str, value: Any) -> None:
        """Set a context value for cross-handler communication.

        Args:
            key: Context key (e.g., "design_output", "requirements_content").
            value: Any serializable value.

        Example:
            session.set_context("design_output", result)
        """
        self._context[key] = value

    def get_context(self, key: str, default: Any = None) -> Any:
        """Get a context value.

        Args:
            key: Context key.
            default: Default value if key not found.

        Returns:
            Stored value or default.

        Example:
            design_output = session.get_context("design_output", "")
        """
        return self._context.get(key, default)

    def clear_context(self, key: str) -> None:
        """Remove a context value.

        Args:
            key: Context key to remove.
        """
        self._context.pop(key, None)


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
