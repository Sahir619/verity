"""Generate cases.json for rv32i_rf. Fixed seed. Expected values come from golden_step.

Coverage: x0 never written, write then read on both ports, all 31 registers, we = 0 holds,
reset clears, the same register on both read ports, plus 200 random steps.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import golden  # noqa: E402

SEED = 0x52321F
RANDOM_STEPS = 200
HERE = Path(__file__).resolve().parent


def rf(**kw: int) -> dict[str, int]:
    inputs = {"clk": 0, "rst_n": 1, "ra1": 0, "ra2": 0, "wa": 0, "wd": 0, "we": 0}
    inputs.update(kw)
    return inputs


def reset(**kw: int) -> dict:
    return {"inputs": rf(rst_n=0, **kw), "rst": True}


def run(**kw: int) -> dict:
    return {"inputs": rf(**kw), "rst": False}


def play(raw: list[dict]) -> list[dict]:
    state: dict = {}
    steps = []
    for step in raw:
        state, expected = golden.golden_step(state, step["inputs"], step["rst"])
        steps.append({"inputs": step["inputs"], "rst": step["rst"], "expected": expected})
    return steps


def case(name: str, raw: list[dict]) -> dict:
    steps = play(raw)
    assert steps and steps[0]["rst"] is True, name
    return {"name": name, "steps": steps}


def hand_checks() -> None:
    """Literal corners, so a golden bug cannot hide behind a generator that calls the same function."""
    outs = [step["expected"] for step in play([
        reset(ra1=1, ra2=2, we=1, wa=1, wd=0xFFFFFFFF),
        run(we=1, wa=0, wd=0xDEADBEEF, ra1=0, ra2=0),
        run(we=1, wa=8, wd=0x01010101, ra1=0, ra2=8),
        run(we=1, wa=0, wd=0xFFFFFFFF, ra1=8, ra2=0),
    ])]
    assert outs[0] == {"rd1": 0, "rd2": 0}
    assert outs[1] == {"rd1": 0, "rd2": 0}
    assert outs[2] == {"rd1": 0, "rd2": 0x01010101}
    assert outs[3] == {"rd1": 0x01010101, "rd2": 0}
    outs = [step["expected"] for step in play([
        reset(),
        run(we=1, wa=7, wd=0x12345678, ra1=7, ra2=7),
        run(we=0, wa=7, wd=0x22222222, ra1=7, ra2=7),
    ])]
    assert outs[1] == {"rd1": 0x12345678, "rd2": 0x12345678}
    assert outs[2] == {"rd1": 0x12345678, "rd2": 0x12345678}
    outs = [step["expected"] for step in play([
        reset(),
        run(we=1, wa=5, wd=0x55555555, ra1=5, ra2=5),
        reset(we=1, wa=5, wd=0x66666666, ra1=5, ra2=5),
        run(we=0, ra1=5, ra2=5),
    ])]
    assert outs[1] == {"rd1": 0x55555555, "rd2": 0x55555555}
    assert outs[2] == {"rd1": 0, "rd2": 0}
    assert outs[3] == {"rd1": 0, "rd2": 0}


def build() -> list[dict]:
    hand_checks()
    writes = [reset()]
    for reg in range(1, 32):
        value = (reg * 0x01010101) & 0xFFFFFFFF
        writes.append(run(we=1, wa=reg, wd=value, ra1=reg, ra2=reg))
    holds = []
    for reg in range(1, 32):
        holds.append(run(we=0, wa=reg, wd=0xFFFFFFFF, ra1=reg, ra2=0 if reg % 2 == 0 else reg))
    holds.append(run(we=1, wa=0, wd=0xABCDEF01, ra1=0, ra2=31))
    rng = random.Random(SEED)
    random_steps = [reset()]
    for _ in range(RANDOM_STEPS):
        rst_n = 0 if rng.randrange(20) == 0 else 1
        random_steps.append({
            "inputs": rf(
                rst_n=rst_n,
                ra1=rng.randrange(32),
                ra2=rng.randrange(32),
                wa=rng.randrange(32),
                wd=rng.randrange(1 << 32),
                we=rng.randrange(2),
            ),
            "rst": rst_n == 0,
        })
    cases = [
        case("reset_clears", [
            reset(),
            run(we=1, wa=1, wd=0xFFFFFFFF, ra1=1, ra2=2),
            run(we=1, wa=2, wd=0xEEEEEEEE, ra1=1, ra2=2),
            reset(ra1=1, ra2=2, we=1, wa=1, wd=0x12345678),
            run(we=0, ra1=1, ra2=2),
        ]),
        case("x0_never_written", [
            reset(),
            run(we=1, wa=0, wd=0xDEADBEEF, ra1=0, ra2=0),
            run(we=1, wa=8, wd=0x01010101, ra1=0, ra2=8),
            run(we=1, wa=0, wd=0xFFFFFFFF, ra1=8, ra2=0),
        ]),
        case("write_then_read_both_ports", [
            reset(),
            run(we=1, wa=12, wd=0x89ABCDEF, ra1=12, ra2=12),
            run(we=1, wa=13, wd=0x01234567, ra1=12, ra2=13),
        ]),
        case("all_31_registers", writes + holds),
        case("we_holds", [
            reset(),
            run(we=1, wa=4, wd=0x22222222, ra1=4, ra2=0),
            run(we=0, wa=4, wd=0x33333333, ra1=4, ra2=4),
            run(we=0, wa=4, wd=0, ra1=4, ra2=0),
        ]),
        case("same_reg_both_ports", [
            reset(),
            run(we=1, wa=21, wd=0x11111111, ra1=21, ra2=21),
            run(we=0, wa=21, wd=0xFFFFFFFF, ra1=21, ra2=21),
        ]),
        case("reset_wins_over_write", [
            reset(),
            run(we=1, wa=5, wd=0x55555555, ra1=5, ra2=5),
            reset(we=1, wa=5, wd=0x66666666, ra1=5, ra2=5),
            run(we=0, ra1=5, ra2=5),
        ]),
        case("random_steps", random_steps),
    ]
    assert len(cases[-1]["steps"]) == RANDOM_STEPS + 1
    assert sum(1 for step in cases[-1]["steps"][1:]) == RANDOM_STEPS
    return cases


def main() -> None:
    cases = build()
    text = json.dumps({"cases": cases}, indent=2) + "\n"
    (HERE / "cases.json").write_text(text, encoding="utf-8", newline="\n")
    print(f"rv32i_rf cases={len(cases)} random_steps={RANDOM_STEPS} seed={SEED:#x}")


if __name__ == "__main__":
    main()
