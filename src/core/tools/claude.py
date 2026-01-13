"""Claude Code CLI wrapper."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from src.core.tools._cli import run_cli_streaming
from src.core.tools.errors import (
    AIToolExecutionError,
    AIToolNotFoundError,
    AIToolTimeoutError,
)


class ClaudeTool:
    """Claude Code CLI wrapper.

    Provides interface to interact with Claude via the Claude Code CLI.
    """

    def __init__(
        self,
        model: str = "sonnet",
        timeout: int = 600,
        permission_mode: str = "default",
        skip_permissions: bool = False,
        verbose: bool = True,
    ) -> None:
        """Initialize ClaudeTool.

        Args:
            model: Model name ("sonnet", "opus", "haiku")
            timeout: Timeout in seconds
            permission_mode: Permission mode ("default", "acceptEdits",
                "bypassPermissions", "delegate", "dontAsk", "plan")
            skip_permissions: If True, add --dangerously-skip-permissions flag.
                Only use in trusted, sandboxed environments.
            verbose: Enable real-time output display
        """
        self.model = model
        self.timeout = timeout
        self.permission_mode = permission_mode
        self.skip_permissions = skip_permissions
        self.verbose = verbose

    def run(
        self,
        prompt: str,
        context: str | list[str] = "",
        session_id: str | None = None,
        log_dir: Path | None = None,
    ) -> tuple[str, str | None]:
        """Execute Claude Code CLI.

        Args:
            prompt: Prompt to send to Claude
            context: Additional context (text or list of texts)
            session_id: Optional session ID for conversation continuity
            log_dir: Optional directory to save execution logs

        Returns:
            Tuple of (response_text, new_session_id)

        Raises:
            AIToolNotFoundError: Claude CLI not installed
            AIToolTimeoutError: Execution timed out
            AIToolExecutionError: CLI exited with non-zero code
        """
        # Build prompt with context
        context_str = _build_context(context)
        full_prompt = f"{prompt}\n\nContext:\n{context_str}" if context_str else prompt

        # Build CLI arguments
        args = ["claude", "-p", "--output-format", "stream-json", "--verbose"]
        if self.model:
            args += ["--model", self.model]
        if self.permission_mode != "default":
            args += ["--permission-mode", self.permission_mode]
        if session_id:
            args += ["-r", session_id]
        if self.skip_permissions:
            args.append("--dangerously-skip-permissions")
        args.append(full_prompt)

        # Execute
        try:
            stdout, stderr, returncode = run_cli_streaming(
                args,
                timeout=self.timeout if self.timeout > 0 else None,
                verbose=self.verbose,
                log_dir=log_dir,
                tool_name="claude",
            )
            if returncode != 0:
                raise AIToolExecutionError(
                    f"Claude CLI exited with code {returncode}",
                    stderr=stderr,
                    returncode=returncode,
                )
        except FileNotFoundError as e:
            raise AIToolNotFoundError("Claude CLI not found. Is 'claude' installed?") from e
        except subprocess.TimeoutExpired as e:
            raise AIToolTimeoutError(f"Claude CLI timed out after {self.timeout}s") from e

        # Parse output
        return self._parse_json_output(stdout, session_id)

    def _parse_json_output(self, stdout: str, session_id: str | None) -> tuple[str, str | None]:
        """Parse CLI output extracting JSON result.

        Args:
            stdout: CLI standard output
            session_id: Original session ID (fallback)

        Returns:
            Tuple of (response_text, session_id)
        """
        for line in stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                payload: dict[str, Any] = json.loads(line)
                if payload.get("type") == "result":
                    return self._extract_from_payload(payload, session_id)
            except json.JSONDecodeError:
                continue

        # Parse failure: return raw output
        return stdout.strip(), session_id

    def _extract_from_payload(
        self, payload: dict[str, Any], session_id: str | None
    ) -> tuple[str, str | None]:
        """Extract response and session ID from payload.

        Args:
            payload: Parsed JSON payload
            session_id: Original session ID (fallback)

        Returns:
            Tuple of (response_text, session_id)
        """
        result = payload.get("result", "")
        new_session_id = payload.get("session_id") or session_id
        return result, new_session_id


def _build_context(context: str | list[str]) -> str:
    """Convert context to string.

    Args:
        context: Text content (string or list)

    Returns:
        Joined context string
    """
    if isinstance(context, str):
        return context
    if isinstance(context, list):
        return "\n".join(context)
    return ""
