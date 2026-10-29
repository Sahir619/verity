"""Bug injectors and the self_test.md mutant table, checked against the real reference designs (no sandbox)."""

import pytest

from verity.core.pack import TaskPack
from verity.core.errors import RtlRejected
from verity.evals.injectors import INJECTORS, inject
from verity.evals.suite_packs import SELF_TEST_NAME, apply_mutant, load_mutants
from verity.config import AGENT_ROOT

ALL_IDS = ["mac4", "alu8", "matmul4", "add8", "cnt8", "fsm_seq", "mm4_int4"]


@pytest.fixture(scope="module")
def mutants():
    return load_mutants(AGENT_ROOT / "tasks" / SELF_TEST_NAME)


def test_every_pack_has_at_least_three_mutants_and_a_width_mutant(mutants) -> None:
    for pack_id in ALL_IDS:
        mine = [m for m in mutants if m.pack_id == pack_id]
        assert len(mine) >= 3, pack_id
        assert any(m.expected == "contract_fail" for m in mine), f"{pack_id} has no width (contract_fail) mutant"
        assert {m.expected for m in mine} <= {"sim_fail", "contract_fail", "lint_fail", "sim_error"}


def test_every_mutant_applies_exactly_once_and_survives_the_rules(packs: dict[str, TaskPack], mutants) -> None:
    for mutant in mutants:
        pack = packs[mutant.pack_id]
        reference = pack.reference_rtl() or ""
        text = apply_mutant(reference, mutant)
        assert text is not None, f"{mutant.pack_id}/{mutant.name}: the find string must occur exactly once in reference.v"
        assert text != reference
        try:
            pack.validate_rtl(text)
        except RtlRejected as exc:  # a rejected mutant would never test the testbench
            pytest.fail(f"{mutant.pack_id}/{mutant.name} is rejected by the coding rules: {exc}")


@pytest.mark.parametrize("pack_id", ALL_IDS)
@pytest.mark.parametrize("injector", sorted(INJECTORS))
def test_an_injection_is_a_real_change_that_still_passes_the_rules(packs: dict[str, TaskPack], pack_id: str, injector: str) -> None:
    pack = packs[pack_id]
    reference = pack.reference_rtl() or ""
    injection = inject(injector, reference, pack)
    if injection is None:
        return  # the family does not apply to this design (for example no reset in add8)
    assert injection.text != reference.replace("\r\n", "\n")
    assert injection.name == injector and injection.description
    pack.validate_rtl(injection.text)


def test_specific_injections(packs: dict[str, TaskPack]) -> None:
    mac4, alu8, cnt8, add8 = packs["mac4"], packs["alu8"], packs["cnt8"], packs["add8"]

    width = inject("wrong_width", mac4.reference_rtl() or "", mac4)
    assert width is not None and "output reg [20:0] acc" in width.text

    off = inject("off_by_one", mac4.reference_rtl() or "", mac4)
    assert off is not None and "cnt == 4'd14" in off.text

    reset = inject("missing_reset", cnt8.reference_rtl() or "", cnt8)
    assert reset is not None and "if (1'b0)" in reset.text
    assert "rst_n" not in reset.text.split("always", 1)[1]  # the reset input is no longer used in the logic

    swap = inject("swapped_operands", alu8.reference_rtl() or "", alu8)
    assert swap is not None and swap.description.startswith("subtraction operands swapped")

    assert inject("missing_reset", add8.reference_rtl() or "", add8) is None  # purely combinational: nothing to disable
    assert inject("swapped_operands", cnt8.reference_rtl() or "", cnt8) is None  # no second data input
