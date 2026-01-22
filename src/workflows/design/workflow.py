"""Design workflow implementation."""

from enum import Enum
from typing import cast

from collections.abc import Callable

from src.core.artifacts import save_artifact, save_jsonl_log
from src.core.errors import LoopLimitExceededError
from src.core.prompts import PromptLoadError, load_prompt, summarize_for_prompt
from src.core.verdict import (
    Verdict,
    create_ai_formatter,
    handle_abort_verdict,
    parse_verdict,
)
from src.workflows.base import AgentContext, SessionState, WorkflowBase

from .states import DesignState

# Type alias for handlers that return Verdict
VerdictHandler = Callable[[AgentContext, SessionState], Verdict]


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

    def get_handler(self, state: Enum) -> VerdictHandler:
        """Get handler for the given state.

        Note: This returns VerdictHandler which returns Verdict instead of
        the base StateHandler which returns Enum. DesignWorkflow handlers
        return Verdict for use with get_next_state().
        """
        handlers: dict[DesignState, VerdictHandler] = {
            DesignState.DESIGN: self._handle_design,
            DesignState.DESIGN_REVIEW: self._handle_design_review,
        }
        design_state = cast(DesignState, state)
        if design_state not in handlers:
            raise ValueError(f"No handler for state: {state}")
        return handlers[design_state]

    def get_next_state(self, current: Enum, verdict: Verdict) -> Enum:
        """Determine next state based on verdict."""
        transitions: dict[tuple[DesignState, Verdict], DesignState] = {
            # DESIGN always goes to DESIGN_REVIEW
            (DesignState.DESIGN, Verdict.PASS): DesignState.DESIGN_REVIEW,
            # DESIGN_REVIEW outcomes
            (DesignState.DESIGN_REVIEW, Verdict.PASS): DesignState.COMPLETE,
            (DesignState.DESIGN_REVIEW, Verdict.RETRY): DesignState.DESIGN,
        }
        key = (cast(DesignState, current), verdict)
        if key not in transitions:
            raise ValueError(f"Invalid transition: {current} + {verdict}")
        return transitions[key]

    def get_prompt_path(self, state: Enum) -> str:
        """Get prompt file path for the state."""
        prompt_files: dict[DesignState, str] = {
            DesignState.DESIGN: "workflows/design/prompts/design.md",
            DesignState.DESIGN_REVIEW: "workflows/design/prompts/design_review.md",
        }
        design_state = cast(DesignState, state)
        if design_state not in prompt_files:
            raise ValueError(f"No prompt for state: {state}")
        return prompt_files[design_state]

    def _handle_design(self, ctx: AgentContext, session: SessionState) -> Verdict:
        """Handle DESIGN state - create detailed design document.

        Args:
            ctx: Agent context with AI tools and issue provider.
            session: Session state for tracking progress.

        Returns:
            Verdict.PASS to proceed to design review.

        Raises:
            LoopLimitExceededError: If loop count exceeds maximum.
        """
        # 1. Loop limit check (prevent infinite RETRY loops)
        if session.is_loop_exceeded("design"):
            raise LoopLimitExceededError(
                state="design",
                count=session.loop_counters.get("design", 0),
                max_count=session.max_loop_count,
            )

        # 2. Ensure artifacts directory
        artifacts_dir = ctx.ensure_artifacts_dir("design")

        # 3. Event log: handler start
        save_jsonl_log(
            artifacts_dir,
            "handler_start",
            {
                "handler": "design",
                "loop_count": session.loop_counters.get("design", 0),
            },
        )

        # 4. Load requirements from context (set by CLI)
        requirements_content = session.get_context("requirements_content", "")

        # 5. Load prompt with template variables
        prompt_vars = {
            "issue_url": ctx.issue_provider.issue_url,
            "issue_body": ctx.issue_provider.get_issue_body(),
            "requirements": requirements_content,
        }
        prompt = load_prompt(
            self.get_prompt_path(DesignState.DESIGN),
            required_vars=["issue_url", "issue_body"],
            **prompt_vars,
        )

        # 6. Event log: AI call start
        save_jsonl_log(
            artifacts_dir,
            "ai_call_start",
            {
                "role": "analyzer",
                "prompt_length": len(prompt),
            },
        )

        # 7. AI call (conversation continuation via role name)
        session_id = session.get_conversation_id("analyzer")
        result, new_session_id = ctx.analyzer.run(
            prompt=prompt,
            context=ctx.issue_provider.issue_url,
            session_id=session_id,
            log_dir=artifacts_dir,
        )

        # 8. Event log: AI call end
        save_jsonl_log(
            artifacts_dir,
            "ai_call_end",
            {
                "role": "analyzer",
                "response_length": len(result),
                "session_id": new_session_id,
            },
        )

        # 9. Save artifacts
        save_artifact(artifacts_dir, "prompt.md", prompt)
        save_artifact(artifacts_dir, "response.md", result)

        # 10. Store design output in session for review
        session.set_context("design_output", result)
        session.set_context("design_output_path", str(artifacts_dir / "response.md"))

        # 11. Update session_id (by role name)
        if new_session_id:
            session.set_conversation_id("analyzer", new_session_id)

        # 12. Increment loop counter
        session.increment_loop("design")

        # 13. Event log: handler end
        save_jsonl_log(
            artifacts_dir,
            "handler_end",
            {
                "handler": "design",
                "verdict": "PASS",
            },
        )

        # 14. DESIGN handler always returns PASS (review determines next step)
        return Verdict.PASS

    def _handle_design_review(self, ctx: AgentContext, session: SessionState) -> Verdict:
        """Handle DESIGN_REVIEW state - review design document.

        Args:
            ctx: Agent context with AI tools and issue provider.
            session: Session state for tracking progress.

        Returns:
            Verdict (PASS, RETRY, or raises for ABORT).

        Raises:
            PromptLoadError: If design output is missing.
            AgentAbortError: If reviewer returns ABORT verdict.
        """
        # 1. Ensure artifacts directory
        log_dir = ctx.ensure_artifacts_dir("design_review")

        # 2. Event log: handler start
        save_jsonl_log(
            log_dir,
            "handler_start",
            {
                "handler": "design_review",
            },
        )

        # 3. Get design artifacts from session
        design_output = session.get_context("design_output", "")
        design_output_path = session.get_context("design_output_path", "")

        if not design_output:
            raise PromptLoadError("Design output not found in session. Run DESIGN first.")

        # 4. Load prompt with design output (summarized if too long)
        design_output_for_prompt = summarize_for_prompt(design_output)

        prompt = load_prompt(
            self.get_prompt_path(DesignState.DESIGN_REVIEW),
            required_vars=["issue_url", "design_output"],
            issue_url=ctx.issue_provider.issue_url,
            design_output=design_output_for_prompt,
            design_output_path=design_output_path,
        )

        # 5. Event log: AI call start
        save_jsonl_log(
            log_dir,
            "ai_call_start",
            {
                "role": "reviewer",
                "prompt_length": len(prompt),
                "design_output_length": len(design_output),
            },
        )

        # 6. AI call (review is new conversation)
        decision, _ = ctx.reviewer.run(
            prompt=prompt,
            context=ctx.issue_provider.issue_url,
            log_dir=log_dir,
        )

        # 7. Event log: AI call end
        save_jsonl_log(
            log_dir,
            "ai_call_end",
            {
                "role": "reviewer",
                "response_length": len(decision),
            },
        )

        # 8. Save artifacts
        save_artifact(log_dir, "prompt.md", prompt)
        save_artifact(log_dir, "response.md", decision)

        # 9. Parse VERDICT with AI formatter fallback
        ai_formatter = create_ai_formatter(ctx.reviewer, context="", log_dir=log_dir)

        # 10. Event log: VERDICT parse start
        save_jsonl_log(
            log_dir,
            "verdict_parse_start",
            {
                "raw_response_length": len(decision),
            },
        )

        verdict = parse_verdict(decision, ai_formatter=ai_formatter, max_retries=2)

        # 11. Save verdict artifact
        save_artifact(log_dir, "verdict.txt", verdict.value)

        # 12. Event log: VERDICT determined (keep original)
        original_verdict = verdict
        save_jsonl_log(
            log_dir,
            "verdict_determined",
            {
                "verdict": verdict.value,
                "original_verdict": original_verdict.value,
            },
        )

        # 13. Handle ABORT verdict (raises exception)
        handle_abort_verdict(verdict, decision)

        # 14. Convert BACK_DESIGN to RETRY for this workflow
        if verdict == Verdict.BACK_DESIGN:
            save_jsonl_log(
                log_dir,
                "verdict_converted",
                {
                    "original": "BACK_DESIGN",
                    "converted_to": "RETRY",
                    "reason": "DesignWorkflow treats BACK_DESIGN as RETRY",
                },
            )
            verdict = Verdict.RETRY

        # 15. Mark completed on PASS
        if verdict == Verdict.PASS:
            session.mark_completed("design_review")
            session.reset_loop("design")

        # 16. Event log: handler end
        save_jsonl_log(
            log_dir,
            "handler_end",
            {
                "handler": "design_review",
                "original_verdict": original_verdict.value,
                "final_verdict": verdict.value,
            },
        )

        return verdict
