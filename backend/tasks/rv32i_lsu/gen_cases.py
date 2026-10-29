"""Generate cases.json for rv32i_lsu. Fixed seed. Expected values come from golden_step.

Coverage: every funct3, every addr_lo, sign and zero extension with the lane top bit 0 and 1,
misaligned accesses, and the fixed sdata lane pattern. Plus 200 random steps.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import golden  # noqa: E402

SEED = 0x523215
RANDOM_STEPS = 200
WDATA = 0xA1B2C3D4
HERE = Path(__file__).resolve().parent


def once(addr_lo: int, funct3: int, rdata: int, wdata: int = WDATA) -> dict:
    inputs = {"addr_lo": addr_lo, "funct3": funct3, "rdata": rdata, "wdata": wdata}
    _, expected = golden.golden_step({}, inputs, False)
    return {"inputs": inputs, "rst": False, "expected": expected}


def rdata_for(funct3: int, addr_lo: int, top: int) -> int:
    size = funct3 & 3
    if size == 0:
        byte = 0x5A | (0x80 if top else 0)
        byte = (byte & 0x7F) | (0x80 if top else 0)
        return (byte << (8 * addr_lo)) & 0xFFFFFFFF
    if size == 1:
        half = 0x3C5A | (0x8000 if top else 0)
        half = (half & 0x7FFF) | (0x8000 if top else 0)
        return (half << (8 * addr_lo)) & 0xFFFFFFFF
    if size == 2:
        word = 0x13579BDF | (0x80000000 if top else 0)
        return (word & 0x7FFFFFFF) | (0x80000000 if top else 0)
    return 0xFFFFFFFF if top else 0x00000000


def hand_checks() -> None:
    def check(addr, funct3, rdata, wdata, ld, wstrb, sdata, mis):
        step = once(addr, funct3, rdata, wdata)
        got = step["expected"]
        want = {"ld": ld, "wstrb": wstrb, "sdata": sdata, "misaligned": mis}
        assert got == want, (addr, funct3, got, want)

    check(0, 0b000, 0x00000080, WDATA, 0xFFFFFF80, 0b0001, 0xD4D4D4D4, 0)
    check(0, 0b000, 0x0000007F, WDATA, 0x0000007F, 0b0001, 0xD4D4D4D4, 0)
    check(1, 0b000, 0x00008000, WDATA, 0xFFFFFF80, 0b0010, 0xD4D4D4D4, 0)
    check(3, 0b000, 0x80000000, WDATA, 0xFFFFFF80, 0b1000, 0xD4D4D4D4, 0)
    check(0, 0b100, 0x00000080, WDATA, 0x00000080, 0, 0xD4D4D4D4, 0)
    check(0, 0b001, 0x00008000, WDATA, 0xFFFF8000, 0b0011, 0xC3D4C3D4, 0)
    check(0, 0b001, 0x00007FFF, WDATA, 0x00007FFF, 0b0011, 0xC3D4C3D4, 0)
    check(2, 0b001, 0x80000000, WDATA, 0xFFFF8000, 0b1100, 0xC3D4C3D4, 0)
    check(1, 0b001, 0x00800000, WDATA, 0, 0, 0xC3D4C3D4, 1)
    check(2, 0b101, 0x80000000, WDATA, 0x00008000, 0, 0xC3D4C3D4, 0)
    check(0, 0b010, 0x80000001, WDATA, 0x80000001, 0b1111, WDATA, 0)
    check(1, 0b010, 0x80000001, WDATA, 0, 0, WDATA, 1)
    check(2, 0b010, 0x80000001, WDATA, 0, 0, WDATA, 1)
    check(0, 0b011, 0xFFFFFFFF, WDATA, 0, 0, 0, 1)
    check(0, 0b110, 0xFFFFFFFF, WDATA, 0, 0, WDATA, 0)
    check(1, 0b110, 0xFFFFFFFF, WDATA, 0, 0, WDATA, 1)
    check(3, 0b111, 0xFFFFFFFF, 0x000000AB, 0, 0, 0, 1)
    check(0, 0b000, 0, 0x000000AB, 0, 0b0001, 0xABABABAB, 0)
    check(0, 0b001, 0, 0x0000CDEF, 0, 0b0011, 0xCDEFCDEF, 0)


def build() -> list[dict]:
    hand_checks()
    cases = []
    for funct3 in range(8):
        for addr_lo in range(4):
            for top in (0, 1):
                cases.append({
                    "name": f"f{funct3}_a{addr_lo}_t{top}",
                    "steps": [once(addr_lo, funct3, rdata_for(funct3, addr_lo, top))],
                })
    rng = random.Random(SEED)
    random_steps = []
    for _ in range(RANDOM_STEPS):
        random_steps.append(once(
            rng.randrange(4),
            rng.randrange(8),
            rng.randrange(1 << 32),
            rng.randrange(1 << 32),
        ))
    cases.append({"name": "random_steps", "steps": random_steps})
    assert len(cases) == 65
    assert len(cases[-1]["steps"]) == RANDOM_STEPS
    names = {item["name"] for item in cases}
    assert len(names) == len(cases)
    return cases


def main() -> None:
    cases = build()
    text = json.dumps({"cases": cases}, indent=2) + "\n"
    (HERE / "cases.json").write_text(text, encoding="utf-8", newline="\n")
    print(f"rv32i_lsu cases={len(cases)} random_steps={RANDOM_STEPS} seed={SEED:#x}")


if __name__ == "__main__":
    main()
