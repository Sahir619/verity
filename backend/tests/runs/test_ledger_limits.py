"""More RunLedger limits than test_ledger.py: token caps, the wall clock, and the Reservation arithmetic."""

import pytest

from verity.config import load_settings
from verity.core.types import BudgetExhausted, BudgetLimits, Usage
from verity.runs.ledger import RunLedger

ULTRA = "nvidia/Nemotron-3-Ultra-550b-a55b"
SUPER = "nvidia/nemotron-3-super-120b-a12b"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def ledger(clock: Clock | None = None, **limits: float) -> RunLedger:
    settings = load_settings()
    return RunLedger(BudgetLimits.from_settings(settings, limits), settings, clock=clock or Clock())


def test_input_token_cap_counts_open_holds() -> None:
    led = ledger(max_input_tokens=1000)
    led.reserve(role="planner", model=SUPER, max_input_tokens=600, max_output_tokens=10)
    with pytest.raises(BudgetExhausted) as err:
        led.reserve(role="planner", model=SUPER, max_input_tokens=600, max_output_tokens=10)
    assert err.value.limit == "max_input_tokens"


def test_output_token_cap() -> None:
    led = ledger(max_output_tokens=1000)
    with pytest.raises(BudgetExhausted) as err:
        led.reserve(role="rtl_gen", model=SUPER, max_input_tokens=10, max_output_tokens=1001)
    assert err.value.limit == "max_output_tokens"


def test_wall_clock_blocks_new_calls_and_is_reported_as_exhausted() -> None:
    clock = Clock()
    led = ledger(clock, max_wall_s=60)
    led.reserve(role="planner", model=SUPER, max_input_tokens=10, max_output_tokens=10)
    assert led.exhausted() is None
    clock.now += 61
    assert led.exhausted() == "max_wall_s"
    with pytest.raises(BudgetExhausted) as err:
        led.reserve(role="planner", model=SUPER, max_input_tokens=10, max_output_tokens=10)
    assert err.value.limit == "max_wall_s"
    assert led.snapshot().wall_s == pytest.approx(61.0, abs=0.1)


def test_unknown_model_is_priced_with_the_fallback() -> None:
    led = ledger(max_usd=0.001)
    # fallback = Ultra prices: 1,000 output tokens cost $0.003 > $0.001.
    with pytest.raises(BudgetExhausted) as err:
        led.reserve(role="optimizer", model="some/unknown-model", max_input_tokens=1, max_output_tokens=1000)
    assert err.value.limit == "max_usd"


def test_pack_budget_may_only_lower_a_limit() -> None:
    settings = load_settings()
    lowered = BudgetLimits.from_settings(settings, {"max_candidates": 4, "max_usd": 99.0})
    assert lowered.max_candidates == 4
    assert lowered.max_usd == settings.budget.run.max_usd  # a pack cannot raise the cap


def test_committed_usage_moves_the_exhaustion_signal() -> None:
    led = ledger(max_usd=0.01)
    r = led.reserve(role="planner", model=SUPER, max_input_tokens=100, max_output_tokens=100)
    led.commit(r, Usage(model=SUPER, input_tokens=100, output_tokens=100, usd=0.02))
    assert led.exhausted() == "max_usd"
