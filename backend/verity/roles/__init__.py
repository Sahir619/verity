"""LLM roles: planner, rtl_gen, repair, optimizer + prompts/ (builder B).

Each role is an async function with the signature in verity.core.protocols (PlanFn, GenerateRtlFn, RepairFn,
OptimizeFn). They talk only to the LLMGateway they are given (reserve, call, retry, commit all happen there).
"""

from ..core.context import Roles


def default_roles() -> Roles:
    """The four production roles. Imported lazily so importing verity.roles stays cheap."""
    from .optimizer import optimize
    from .planner import plan
    from .repair import repair
    from .rtl_gen import generate_rtl

    return Roles(plan=plan, generate_rtl=generate_rtl, repair=repair, optimize=optimize)


__all__ = ["default_roles"]
