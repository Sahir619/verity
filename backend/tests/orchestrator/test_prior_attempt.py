"""A CPU core retry's prior lines reach every first RTL generation of the orchestrator, and nothing else changes."""

import inspect

from verity.core.context import RunContext
from verity.orchestrator import build, machine


def test_both_first_generation_paths_pass_the_prior_lines() -> None:
    # One sample and k parallel samples (R4) are both first versions; the restart from the plan (R5) is not.
    assert "**build.prior_kwargs(ctx)" in inspect.getsource(machine._baseline_stage)
    assert "**prior_kwargs(ctx)" in inspect.getsource(build.sample_baseline)
    assert "prior_kwargs" not in inspect.getsource(build._restart)


def test_prior_kwargs_is_empty_without_lines_so_a_role_without_the_argument_keeps_working() -> None:
    class Ctx:
        prior_attempt: tuple[str, ...] = ()

    ctx = Ctx()
    assert build.prior_kwargs(ctx) == {}  # type: ignore[arg-type]
    ctx.prior_attempt = ("tests passed: 3 of 12",)
    assert build.prior_kwargs(ctx) == {"prior_attempt": ("tests passed: 3 of 12",)}  # type: ignore[arg-type]
    assert RunContext.__dataclass_fields__["prior_attempt"].default == ()
