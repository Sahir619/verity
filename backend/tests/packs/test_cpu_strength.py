"""Keep the CPU references' planted-bug coverage from regressing.

W7 baseline (local Docker, plant's default limit):
    rv32i_imm: 12 caught, 4 equivalent survivors
    rv32i_rf: 19 caught, 1 equivalent survivor
    rv32i_ctl: 18 caught, 2 equivalent survivors
    rv32i_core: planted 20, no equivalent row yet

These are reference.v results, not the separate job winners quoted in the brief.
Every reference survivor is equivalent, so no directed case can kill it. Keep the
locked cases and programs intact and reject any new, unexplained survivor here.

The previous rv32i_core survivors (the EXEC alu_out capture and the MEM mdr
capture) are no longer inside plant's 20 edits. The RVFI compares and the
dbg_stall pins took those slots, so those old exceptions are not reused. The
strength run for the new 20 has not been run.
"""

import shutil
import subprocess

import pytest

from verity.core.mutants import plant
from verity.tools.local_docker import LocalDockerRunner
from verity_test_support import make_settings


# Pin the actual edit as well as its id, so a changed planter or reference cannot
# silently inherit an equivalence exception belonging to a different mutation.
EQUIVALENT = {
    "rv32i_imm": {
        "m04": ("const_plus_one", 7, "20", "21",
                "I sign extension adds a redundant MSB, truncated to 32 bits."),
        "m08": ("const_plus_one", 9, "19", "20",
                "B sign extension adds a redundant MSB, truncated to 32 bits."),
        "m15": ("const_plus_one", 8, "20", "21",
                "S sign extension adds a redundant MSB, truncated to 32 bits."),
        "m16": ("const_plus_one", 11, "11", "12",
                "J sign extension adds a redundant MSB, truncated to 32 bits."),
    },
    "rv32i_rf": {
        "m06": ("const_plus_one", 18, "0", "1",
                "Only hidden regs[0] changes; both read ports force x0 to zero."),
    },
    "rv32i_ctl": {
        "m10": ("const_plus_one", 42, "5", "6",
                "Every case arm, including default, overwrites state_n's initial value."),
        "m18": ("const_plus_one", 69, "7", "0",
                "Earlier FETCH handles 0; unreachable 7 falls to the identical HALT default."),
    },
    "rv32i_core": {},
}
MUTANT_COUNTS = {"rv32i_imm": 16, "rv32i_rf": 20, "rv32i_ctl": 20, "rv32i_core": 20}


@pytest.fixture()
def cpu_strength_runner(tmp_path):
    settings = make_settings(tmp_path / "data")
    if shutil.which(settings.tools.docker_bin) is None:
        pytest.skip("Docker is not installed")
    try:
        probe = subprocess.run(
            [settings.tools.docker_bin, "image", "inspect", settings.tools.image],
            capture_output=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        pytest.skip("Docker or the verity-tools image is not available")
    if probe.returncode != 0:
        pytest.skip("Docker or the verity-tools image is not available")
    return LocalDockerRunner(settings)


@pytest.mark.parametrize("pack_id", MUTANT_COUNTS)
async def test_cpu_reference_catches_every_nonequivalent_mutant(cpu_strength_runner, packs, pack_id):
    pack = packs[pack_id]
    rtl = pack.validate_rtl(pack.reference_rtl() or "")
    mutants = plant(rtl.text, pack)
    assert len(mutants) == MUTANT_COUNTS[pack_id]
    equivalents = EQUIVALENT[pack_id]
    edits = {m.id: m for m in mutants}
    for mid, (rule, line, before, after, _reason) in equivalents.items():
        mutant = edits[mid]
        assert (mutant.rule, mutant.line, mutant.before, mutant.after) == (rule, line, before, after)

    result = await cpu_strength_runner.test_strength(rtl, pack, mutants)
    assert result.status == "ok", result.message
    assert len(result.mutants) == len(mutants)
    assert {row.id for row in result.mutants} == set(edits)
    for row in result.mutants:
        expected = "survived" if row.id in equivalents else "caught"
        assert row.status == expected, (pack_id, edits[row.id], row)
        assert row.total == pack.test_count, (pack_id, row)
        if expected == "survived":
            assert row.passed == pack.test_count
    print(f"{pack_id}: {len(mutants) - len(equivalents)}/{len(mutants)} caught")
    for mid, (*_edit, reason) in equivalents.items():
        print(f"  {mid}: equivalent: {reason}")


BLOCK_EQUIVALENTS = [
    (pack_id, mid)
    for pack_id, equivalents in EQUIVALENT.items()
    if pack_id != "rv32i_core"
    for mid in equivalents
]


@pytest.mark.parametrize("pack_id,mid", BLOCK_EQUIVALENTS)
async def test_block_survivor_equivalence_is_proven(cpu_strength_runner, packs, pack_id, mid):
    """Check the survivor explanations with the real proof tool, beyond simulation."""
    pack = packs[pack_id]
    rtl = pack.validate_rtl(pack.reference_rtl() or "")
    mutant = next(m for m in plant(rtl.text, pack) if m.id == mid)
    result = await cpu_strength_runner.check_equivalence(rtl, pack.validate_rtl(mutant.text), pack)
    assert result.status == "proven", (pack_id, mid, result)
