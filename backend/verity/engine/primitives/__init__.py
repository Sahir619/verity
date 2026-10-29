"""Parametric hardware primitives library."""

from .arithmetic import (
    build_balanced_adder_tree,
    build_parallel_multipliers,
    unpack_lane,
)
from .control import (
    bits_needed,
    build_cycle_counter,
)
from .storage import (
    build_mux2,
    build_pipeline_stage,
)

__all__ = [
    "unpack_lane",
    "build_parallel_multipliers",
    "build_balanced_adder_tree",
    "bits_needed",
    "build_cycle_counter",
    "build_pipeline_stage",
    "build_mux2",
]
