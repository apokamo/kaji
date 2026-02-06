"""Bugfix Agent v5 Package

This package contains the modularized components of the bugfix agent orchestrator.
"""

__version__ = "5.0.0"

# Phase 2: Independent modules (low dependencies)
from .agent_context import AgentContext, create_default_context

# Phase 3: CLI utilities and context
from .cli import format_jsonl_line, run_cli_streaming
from .config import CONFIG_PATH, get_config_value, get_workdir, load_config
from .context import build_context
from .errors import (
    AgentAbortError,
    LoopLimitExceeded,
    ToolError,
    VerdictParseError,
    check_tool_result,
)
from .github import post_issue_comment

# Phase 5: State handlers
from .handlers import (
    handle_detail_design,
    handle_detail_design_review,
    handle_implement,
    handle_implement_review,
    handle_init,
    handle_investigate,
    handle_investigate_review,
    handle_pr_create,
)
from .prompts import (
    COMMON_PROMPT_FILE,
    PROMPT_DIR,
    VERDICT_REQUIRED_STATES,
    load_prompt,
)
from .run_logger import RunLogger

# Phase 4: State machine, workflow, and context
from .state import (
    ExecutionConfig,
    ExecutionMode,
    SessionState,
    State,
    infer_result_label,
)

# Phase 3: Tool implementations
from .tools import AIToolProtocol, ClaudeTool, CodexTool, GeminiTool, MockTool
from .verdict import ReviewResult, Verdict, parse_verdict

__all__ = [
    # Version
    "__version__",
    # Config
    "CONFIG_PATH",
    "load_config",
    "get_config_value",
    "get_workdir",
    # Errors
    "ToolError",
    "LoopLimitExceeded",
    "VerdictParseError",
    "AgentAbortError",
    "check_tool_result",
    # Verdict
    "Verdict",
    "parse_verdict",
    "ReviewResult",
    # CLI utilities
    "run_cli_streaming",
    "format_jsonl_line",
    # Context
    "build_context",
    # Tools
    "AIToolProtocol",
    "MockTool",
    "GeminiTool",
    "CodexTool",
    "ClaudeTool",
    # State machine
    "State",
    "ExecutionMode",
    "ExecutionConfig",
    "SessionState",
    "infer_result_label",
    # Run logger
    "RunLogger",
    # Prompts
    "PROMPT_DIR",
    "COMMON_PROMPT_FILE",
    "VERDICT_REQUIRED_STATES",
    "load_prompt",
    # GitHub
    "post_issue_comment",
    # Agent context
    "AgentContext",
    "create_default_context",
    # State handlers
    "handle_init",
    "handle_investigate",
    "handle_investigate_review",
    "handle_detail_design",
    "handle_detail_design_review",
    "handle_implement",
    "handle_implement_review",
    "handle_pr_create",
]
