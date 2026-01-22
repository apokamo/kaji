"""Prompt loading and template processing.

This module provides utilities for loading prompt files and
substituting template variables using string.Template for safety.
"""

import logging
import re
from pathlib import Path
from string import Template

logger = logging.getLogger(__name__)

# Maximum inline content length before summarization
MAX_INLINE_CONTENT_LENGTH = 10_000


class PromptLoadError(Exception):
    """Raised when prompt file cannot be loaded or formatted."""

    pass


def extract_template_variables(template_text: str) -> set[str]:
    """Extract all variable names from a string.Template text.

    Args:
        template_text: Template text with ${var} placeholders.

    Returns:
        Set of variable names found in the template.

    Example:
        >>> extract_template_variables("Hello ${name}, your ${item} is ready")
        {'name', 'item'}
    """
    # Match ${identifier} but not $${escaped}
    pattern = r"(?<!\$)\$\{(\w+)\}"
    return set(re.findall(pattern, template_text))


def _load_prompt_from_path(
    path: Path,
    *,
    required_vars: list[str] | None = None,
    **kwargs: str,
) -> str:
    """Load prompt from a specific path and substitute template variables.

    Internal function for testing. Use load_prompt() for production code.

    Args:
        path: Absolute path to the prompt file.
        required_vars: List of variable names that MUST be provided.
        **kwargs: Template variables to substitute.

    Returns:
        Formatted prompt text.

    Raises:
        PromptLoadError: If file not found, required vars missing,
                        or template substitution fails.
    """
    if not path.exists():
        raise PromptLoadError(f"Prompt file not found: {path}")

    try:
        template_text = path.read_text(encoding="utf-8")
    except Exception as e:
        raise PromptLoadError(f"Failed to read prompt {path}: {e}") from e

    # Validate required variables
    if required_vars:
        missing = [var for var in required_vars if var not in kwargs or not kwargs[var]]
        if missing:
            raise PromptLoadError(f"Missing required prompt variables for {path.name}: {missing}")

    # Static analysis: warn about undefined variables
    template_vars = extract_template_variables(template_text)
    provided_vars = set(kwargs.keys())
    undefined_vars = template_vars - provided_vars
    if undefined_vars:
        logger.warning(
            f"Template {path.name} has undefined variables: {undefined_vars}. "
            "These will remain as ${var} in the output."
        )

    try:
        template = Template(template_text)
        # safe_substitute: undefined variables remain as ${varname}
        return template.safe_substitute(**kwargs)
    except Exception as e:
        raise PromptLoadError(f"Failed to process prompt {path}: {e}") from e


def load_prompt(
    relative_path: str,
    *,
    required_vars: list[str] | None = None,
    **kwargs: str,
) -> str:
    """Load prompt file and substitute template variables.

    Uses string.Template which safely handles missing keys and
    allows literal ${...} by using $$.

    Args:
        relative_path: Relative path from src/ directory.
        required_vars: List of variable names that MUST be provided.
                      If any are missing, raises PromptLoadError.
        **kwargs: Template variables to substitute.

    Returns:
        Formatted prompt text.

    Raises:
        PromptLoadError: If file not found, required vars missing,
                        or template substitution fails.
    """
    src_dir = Path(__file__).parent.parent
    path = src_dir / relative_path
    return _load_prompt_from_path(path, required_vars=required_vars, **kwargs)


def summarize_for_prompt(
    content: str,
    max_length: int = MAX_INLINE_CONTENT_LENGTH,
) -> str:
    """Summarize long content for prompt inclusion.

    Args:
        content: Original content.
        max_length: Maximum length before summarization.

    Returns:
        Original content if under limit, otherwise summarized version.
    """
    if len(content) <= max_length:
        return content

    # Calculate head and tail lengths, ensuring they are positive
    delimiter = "\n\n... [中略: 全文は ${design_output_path} を参照] ...\n\n"
    delimiter_length = len(delimiter)
    available = max_length - delimiter_length

    # Ensure minimum lengths
    if available <= 0:
        # max_length too small, just truncate
        return content[: max_length - 3] + "..."

    head_length = available // 2
    tail_length = available - head_length

    return content[:head_length] + delimiter + content[-tail_length:]


# Prompt variable documentation
PROMPT_VARIABLES = {
    "design": {
        "required": ["issue_url", "issue_body"],
        "optional": ["requirements"],
        "description": {
            "issue_url": "GitHub Issue URL",
            "issue_body": "Issue本文（Markdown）",
            "requirements": "追加要件ファイルの内容（オプション、空文字列がデフォルト）",
        },
    },
    "design_review": {
        "required": ["issue_url", "design_output"],
        "optional": ["design_output_path"],
        "description": {
            "issue_url": "GitHub Issue URL",
            "design_output": "設計ドキュメントの内容（または先頭サマリ）",
            "design_output_path": "設計ドキュメントのファイルパス",
        },
    },
}
