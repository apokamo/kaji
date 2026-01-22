"""Context setup for Design workflow.

This module handles the initialization of workflow context,
including loading input files and storing them in session state.
"""

from argparse import Namespace
from pathlib import Path

from src.core.artifacts import save_artifact
from src.workflows.base import AgentContext, SessionState


def setup_workflow_context(
    args: Namespace,
    ctx: AgentContext,
    session: SessionState,
) -> None:
    """Set up workflow context from CLI arguments.

    This function handles:
    - Loading requirements file content (if provided)
    - Storing content in session for handler access
    - Saving a copy to artifacts for audit trail

    Args:
        args: Parsed CLI arguments with 'issue' and optionally 'input'.
        ctx: Agent context with artifacts directory.
        session: Session state for storing context.
    """
    # Handle optional requirements file
    requirements_content = ""
    if hasattr(args, "input") and args.input:
        input_path = Path(args.input)
        if input_path.exists():
            requirements_content = input_path.read_text(encoding="utf-8")

            # Save to artifacts for audit trail
            input_artifacts_dir = ctx.ensure_artifacts_dir("input")
            save_artifact(
                input_artifacts_dir,
                "requirements.md",
                requirements_content,
            )

    # Store in session for handler access via ${requirements}
    session.set_context("requirements_content", requirements_content)
