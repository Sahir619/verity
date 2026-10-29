"""Deterministic orchestrator state machine (builder D). Exposes run_orchestrator(ctx).

Modules: machine (the state machine and the endings), plan_stage (spec, plan, gate), build (verify loop, repair,
synth), evolve (rounds, winner, prove), judge (fitness, elites), feasibility (plan gate rules), events (typed event
emitter), plan_ops (plan block helpers), candidates and state (working state).
"""

from .feasibility import check_plan
from .machine import run_orchestrator

__all__ = ["run_orchestrator", "check_plan"]
