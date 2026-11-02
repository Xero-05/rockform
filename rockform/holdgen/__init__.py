"""Procedural climbing holds generated to a measured holding-area difficulty."""
__version__ = "0.2.0"     # the portable hold generator's release version
from .shapes import HOLD_TYPES, HOLD_INFO, build
from .holding import GripCone, holding_area, pole_figure, difficulty_from_area
from .generate import HoldRequest, GeneratedHold, generate, parse_description

__all__ = ["HOLD_TYPES", "HOLD_INFO", "build", "GripCone", "holding_area", "pole_figure", "difficulty_from_area",
           "HoldRequest", "GeneratedHold", "generate", "parse_description"]
