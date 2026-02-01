"""Tool factory for state-based tool creation.

This module provides functions to create AI tools based on state configuration.
"""

from src.core.config import StateConfig, get_state_config, get_tool_config
from src.core.tools.protocol import AIToolProtocol


def create_tool_for_state(state_name: str) -> AIToolProtocol:
    """Create a tool based on state configuration.

    Args:
        state_name: State name (e.g., "INIT", "INVESTIGATE")

    Returns:
        AIToolProtocol implementation configured for the state.

    Raises:
        ValueError: If state is not configured or agent is unknown.

    Example:
        # config.toml:
        # [states.INIT]
        # agent = "claude"
        # model = "opus"

        tool = create_tool_for_state("INIT")
        assert isinstance(tool, ClaudeTool)
        assert tool.model == "opus"
    """
    state_config = get_state_config(state_name)
    if state_config is None:
        raise ValueError(f"State '{state_name}' is not configured in config.toml")

    return _create_tool_from_config(state_config)


def _create_tool_from_config(state_config: StateConfig) -> AIToolProtocol:
    """Create a tool from StateConfig with inheritance logic.

    Inheritance priority:
    1. StateConfig explicit values (model, timeout)
    2. [tools.{agent}] section values
    3. Tool's hardcoded defaults

    Args:
        state_config: State configuration with agent and optional overrides.

    Returns:
        Configured AIToolProtocol implementation.

    Raises:
        ValueError: If agent is unknown.
    """
    agent = state_config.agent

    # Get tool-level defaults from [tools.{agent}]
    tool_config = get_tool_config(agent)

    if agent == "claude":
        from src.core.tools.claude import ClaudeTool

        # Apply inheritance: state > tools > hardcoded defaults
        # Use 'is not None' to preserve explicit 0/empty values
        model = state_config.model if state_config.model is not None else tool_config.get("model")
        timeout = (
            state_config.timeout if state_config.timeout is not None else tool_config.get("timeout")
        )
        permission_mode = tool_config.get("permission_mode")

        return ClaudeTool(
            model=model,
            timeout=timeout,
            permission_mode=permission_mode,
        )

    # Future: Add codex, gemini support here
    # elif agent == "codex":
    #     from src.core.tools.codex import CodexTool
    #     return CodexTool(...)
    # elif agent == "gemini":
    #     from src.core.tools.gemini import GeminiTool
    #     return GeminiTool(...)

    raise ValueError(f"Unknown agent: '{agent}'. Supported agents: claude")
