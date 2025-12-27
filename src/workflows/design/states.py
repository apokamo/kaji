"""Design workflow states."""

from enum import Enum, auto


class DesignState(Enum):
    """Design workflow states.

    Simple 2-state loop:
    - DESIGN: Create/refine design based on requirements
    - DESIGN_REVIEW: Review design quality and completeness
    """

    DESIGN = auto()
    DESIGN_REVIEW = auto()
    COMPLETE = auto()
