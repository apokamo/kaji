"""Tests for design workflow."""

import pytest

from src.core.verdict import Verdict
from src.workflows.design import DesignWorkflow
from src.workflows.design.states import DesignState


class TestDesignWorkflow:
    """Tests for DesignWorkflow class."""

    def test_name(self) -> None:
        workflow = DesignWorkflow()
        assert workflow.name == "design"

    def test_states(self) -> None:
        workflow = DesignWorkflow()
        assert workflow.states == DesignState

    def test_initial_state(self) -> None:
        workflow = DesignWorkflow()
        assert workflow.initial_state == DesignState.DESIGN

    def test_terminal_states(self) -> None:
        workflow = DesignWorkflow()
        assert workflow.terminal_states == {DesignState.COMPLETE}

    def test_get_handler_design(self) -> None:
        workflow = DesignWorkflow()
        handler = workflow.get_handler(DesignState.DESIGN)
        assert callable(handler)

    def test_get_handler_design_review(self) -> None:
        workflow = DesignWorkflow()
        handler = workflow.get_handler(DesignState.DESIGN_REVIEW)
        assert callable(handler)

    def test_get_handler_invalid_state(self) -> None:
        workflow = DesignWorkflow()
        with pytest.raises(ValueError):
            workflow.get_handler(DesignState.COMPLETE)

    def test_next_state_design_pass(self) -> None:
        workflow = DesignWorkflow()
        next_state = workflow.get_next_state(DesignState.DESIGN, Verdict.PASS)
        assert next_state == DesignState.DESIGN_REVIEW

    def test_next_state_review_pass(self) -> None:
        workflow = DesignWorkflow()
        next_state = workflow.get_next_state(DesignState.DESIGN_REVIEW, Verdict.PASS)
        assert next_state == DesignState.COMPLETE

    def test_next_state_review_retry(self) -> None:
        workflow = DesignWorkflow()
        next_state = workflow.get_next_state(DesignState.DESIGN_REVIEW, Verdict.RETRY)
        assert next_state == DesignState.DESIGN

    def test_next_state_invalid_transition(self) -> None:
        workflow = DesignWorkflow()
        with pytest.raises(ValueError):
            workflow.get_next_state(DesignState.DESIGN, Verdict.RETRY)

    def test_get_prompt_path_design(self) -> None:
        workflow = DesignWorkflow()
        path = workflow.get_prompt_path(DesignState.DESIGN)
        assert "design.md" in path

    def test_get_prompt_path_review(self) -> None:
        workflow = DesignWorkflow()
        path = workflow.get_prompt_path(DesignState.DESIGN_REVIEW)
        assert "design_review.md" in path
