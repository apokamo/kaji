"""AI Tool implementations for Bugfix Agent v5

This subpackage contains the AI tool wrappers:
- AIToolProtocol: Interface for all AI tools
- MockTool: Test mock implementation
- GeminiTool: Analyzer (Issue analysis, documentation, long-context)
- CodexTool: Reviewer (Code review, judgment, web search)
- ClaudeTool: Implementer (File operations, command execution)
"""

from .base import AIToolProtocol, MockTool
from .claude import ClaudeTool
from .codex import CodexTool
from .gemini import GeminiTool

__all__ = [
    "AIToolProtocol",
    "MockTool",
    "GeminiTool",
    "CodexTool",
    "ClaudeTool",
]
