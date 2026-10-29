"""Generate cases.json for rv32i_pctl. Fixed seed. Expected values come from golden_step.

The shared testbench samples after the rising edge, so a step's expected outputs are the
state entered by that edge, using the inputs of that step. A redirect or a trap is visible
on the step that enters EX with those flags. The next step holds the same flags: that edge
clears ex_valid and, for a trap, sets halted. The killed slot is that following step.

Every case starts with reset. Directed cases cover the contract list. Random steps are
legal instruction classes (one class at a time), at least 200 of them.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import golden  # noqa: E402

SEED = 0x503C71
RANDOM_STEPS = 200
HERE = Path(__file__).resolve().parent
FLAGS = ("is_load", "is_store", "is_branch", "is_jal", "is_jalr", "trap", "taken", "misaligned")
NAMES = (
    "ex_valid", "halted", "advance", "redirect", "rf_wen",
    "dmem_valid", "dmem_we", "retire", "trap_now", "dbg_stall",
)

# Hand-checked pictures of one sampled cycle. Not computed from golden.py.
BUBBLE = {
    "ex_valid": 0, "halted": 0, "advance": 1, "redirect": 0, "rf_wen": 0,
    "dmem_valid": 0, "dmem_we": 0, "retire": 0, "trap_now": 0, "dbg_stall": 1,
}
RUN = {
    "ex_valid": 1, "halted": 0, "advance": 1, "redirect": 0, "rf_wen": 1,
    "dmem_valid": 0, "dmem_we": 0, "retire": 1, "trap_now": 0, "dbg_stall": 0,
}
REDIRECT = dict(RUN, redirect=1)
LOAD = dict(RUN, dmem_valid=1)
STORE = dict(LOAD, dmem_we=1)
TRAP = {
    "ex_valid": 1, "halted": 0, "advance": 0, "redirect": 0, "rf_wen": 0,
    "dmem_valid": 0, "dmem_we": 0, "retire": 1, "trap_now": 1, "dbg_stall": 0,
}
HALT = {
    "ex_valid": 0, "halted": 1, "advance": 0, "redirect": 0, "rf_wen": 0,
    "dmem_valid": 0, "dmem_we": 0, "retire": 0, "trap_now": 0, "dbg_stall": 3,
}

REDIRECT_KINDS = {"jal", "jalr", "branch_taken"}
TRAP_KINDS = {"load_mis", "store_mis", "trap"}
KINDS = (
    "plain", "load", "store", "branch_not", "branch_taken",
    "jal", "jalr", "load_mis", "store_mis", "trap",
)


def cin(rst_n: int = 1, **kw: int) -> dict[str, int]:
    unknown = set(kw) - set(FLAGS)
    if unknown:
        raise ValueError(f"unknown flags: {sorted(unknown)}")
    inputs = {"clk": 0, "rst_n": rst_n}
    for name in FLAGS:
        inputs[name] = int(kw.get(name, 0))
    return inputs


def play(raw: list[tuple[bool, dict]]) -> tuple[list[dict], list[dict]]:
    state: dict = {}
    outs: list[dict] = []
    steps: list[dict] = []
    for rst, inputs in raw:
        state, expected = golden.golden_step(state, inputs, rst)
        outs.append(expected)
        steps.append({"inputs": inputs, "rst": rst, "expected": expected})
    return outs, steps


def case(name: str, raw: list[tuple[bool, dict]]) -> dict:
    outs, steps = play(raw)
    assert steps[0]["rst"] is True, name
    assert steps[0]["inputs"]["rst_n"] == 0, name
    return {"name": name, "steps": steps, "_outs": outs}


def expect(out: dict, want: dict) -> None:
    assert set(out) == set(NAMES), sorted(set(out) ^ set(NAMES))
    assert out == want, (out, want)


def check_shape(out: dict) -> None:
    """Relations that follow from one state's outputs, checked on every step."""
    assert set(out) == set(NAMES)
    assert out["dbg_stall"] in (0, 1, 3)
    assert out["advance"] == (0 if out["halted"] or out["trap_now"] else 1)
    assert out["retire"] == (1 if out["ex_valid"] and not out["halted"] else 0)
    if out["redirect"]:
        assert out["ex_valid"] == 1 and out["halted"] == 0 and out["trap_now"] == 0
    if out["dmem_we"]:
        assert out["dmem_valid"] == 1
    if out["halted"]:
        assert out == HALT
    elif out["ex_valid"] == 0:
        assert out == BUBBLE
    elif out["trap_now"]:
        assert out["rf_wen"] == 0 and out["redirect"] == 0 and out["dmem_valid"] == 0
    else:
        assert out["rf_wen"] == 1 and out["dbg_stall"] == 0


def check_directed(built: dict) -> None:
    expect(built["first_cycle_after_reset"]["_outs"][0], BUBBLE)

    plain = built["plain_run"]["_outs"]
    expect(plain[0], BUBBLE)
    for step in plain[1:]:
        expect(step, RUN)

    for name in ("jal_then_killed_slot", "jalr_then_killed_slot", "branch_taken_then_killed_slot"):
        outs = built[name]["_outs"]
        expect(outs[0], BUBBLE)
        expect(outs[1], REDIRECT)
        expect(outs[2], BUBBLE)  # the killed slot: ex_valid 0, only advance
        expect(outs[3], RUN)

    not_taken = built["branch_not_taken"]["_outs"]
    expect(not_taken[0], BUBBLE)
    expect(not_taken[1], RUN)
    expect(not_taken[2], RUN)
    expect(not_taken[3], RUN)

    load = built["load_aligned"]["_outs"]
    expect(load[0], BUBBLE)
    expect(load[1], LOAD)
    expect(load[2], RUN)

    store = built["store_aligned"]["_outs"]
    expect(store[0], BUBBLE)
    expect(store[1], STORE)
    expect(store[2], RUN)

    for name in ("load_misaligned", "store_misaligned", "trap_to_halt", "trap_beats_redirect"):
        outs = built[name]["_outs"]
        expect(outs[0], BUBBLE)
        expect(outs[1], TRAP)
        expect(outs[2], HALT)

    stays = built["halt_stays_until_reset"]["_outs"]
    expect(stays[0], BUBBLE)
    expect(stays[1], TRAP)
    expect(stays[2], HALT)
    expect(stays[3], HALT)
    expect(stays[4], HALT)
    expect(stays[5], BUBBLE)

    during = built["reset_during_halt"]["_outs"]
    expect(during[0], BUBBLE)
    expect(during[1], TRAP)
    expect(during[2], HALT)
    expect(during[3], BUBBLE)
    expect(during[4], RUN)

    side = built["side_flags_ignored"]["_outs"]
    expect(side[0], BUBBLE)
    expect(side[1], RUN)

    both = built["load_then_store"]["_outs"]
    expect(both[0], BUBBLE)
    expect(both[1], LOAD)
    expect(both[2], STORE)


def flags_for(kind: str, rng: random.Random) -> dict[str, int]:
    if kind == "plain":
        return cin(taken=rng.randint(0, 1), misaligned=rng.randint(0, 1))
    if kind == "jal":
        return cin(is_jal=1)
    if kind == "jalr":
        return cin(is_jalr=1)
    if kind == "branch_taken":
        return cin(is_branch=1, taken=1)
    if kind == "branch_not":
        return cin(is_branch=1, taken=0)
    if kind == "load":
        return cin(is_load=1)
    if kind == "store":
        return cin(is_store=1)
    if kind == "load_mis":
        return cin(is_load=1, misaligned=1)
    if kind == "store_mis":
        return cin(is_store=1, misaligned=1)
    if kind == "trap":
        return cin(trap=1, is_jal=rng.randint(0, 1), taken=rng.randint(0, 1))
    raise AssertionError(kind)


def emit(kind: str, rng: random.Random, phase: str) -> tuple[list[tuple[bool, dict]], str]:
    """One legal instruction, held across the edge that must see it. phase is bubble, valid or halt."""
    if phase == "halt":
        return [(True, cin(rst_n=0))], "bubble"
    flags = flags_for(kind, rng)
    if kind in REDIRECT_KINDS:
        if phase == "bubble":
            return [(False, flags), (False, flags)], "bubble"
        return [(False, flags)], "bubble"
    if kind in TRAP_KINDS:
        if phase == "bubble":
            return [(False, flags), (False, flags)], "halt"
        return [(False, flags)], "halt"
    return [(False, flags)], "valid"


def random_legal(rng: random.Random) -> list[tuple[bool, dict]]:
    steps: list[tuple[bool, dict]] = []
    phase = "bubble"
    for kind in KINDS:
        extra, phase = emit(kind, rng, phase)
        steps.extend(extra)
    while len(steps) < RANDOM_STEPS:
        extra, phase = emit(rng.choice(KINDS), rng, phase)
        steps.extend(extra)
    return steps[:RANDOM_STEPS]


def build() -> list[dict]:
    jal = cin(is_jal=1)
    jalr = cin(is_jalr=1)
    taken = cin(is_branch=1, taken=1)
    not_taken = cin(is_branch=1, taken=0)
    load = cin(is_load=1)
    store = cin(is_store=1)
    load_mis = cin(is_load=1, misaligned=1)
    store_mis = cin(is_store=1, misaligned=1)
    trap = cin(trap=1)
    trap_jal = cin(trap=1, is_jal=1)
    wild = cin(is_load=1, is_store=1, is_branch=1, is_jal=1, is_jalr=1, trap=1, taken=1, misaligned=1)
    specs = {
        "first_cycle_after_reset": [
            (True, cin(rst_n=0)),
        ],
        "plain_run": [
            (True, cin(rst_n=0)),
            (False, cin()),
            (False, cin()),
            (False, cin()),
            (False, cin()),
        ],
        "jal_then_killed_slot": [
            (True, cin(rst_n=0)),
            (False, jal),
            (False, jal),
            (False, cin()),
        ],
        "jalr_then_killed_slot": [
            (True, cin(rst_n=0)),
            (False, jalr),
            (False, jalr),
            (False, cin()),
        ],
        "branch_taken_then_killed_slot": [
            (True, cin(rst_n=0)),
            (False, taken),
            (False, taken),
            (False, cin()),
        ],
        "branch_not_taken": [
            (True, cin(rst_n=0)),
            (False, not_taken),
            (False, not_taken),
            (False, cin()),
        ],
        "load_aligned": [
            (True, cin(rst_n=0)),
            (False, load),
            (False, cin()),
        ],
        "store_aligned": [
            (True, cin(rst_n=0)),
            (False, store),
            (False, cin()),
        ],
        "load_misaligned": [
            (True, cin(rst_n=0)),
            (False, load_mis),
            (False, load_mis),
        ],
        "store_misaligned": [
            (True, cin(rst_n=0)),
            (False, store_mis),
            (False, store_mis),
        ],
        "trap_to_halt": [
            (True, cin(rst_n=0)),
            (False, trap),
            (False, trap),
        ],
        "halt_stays_until_reset": [
            (True, cin(rst_n=0)),
            (False, trap),
            (False, trap),
            (False, wild),
            (False, cin(is_jal=1, is_jalr=1, taken=1, misaligned=1)),
            (True, cin(rst_n=0)),
        ],
        "reset_during_halt": [
            (True, cin(rst_n=0)),
            (False, trap),
            (False, trap),
            (True, cin(rst_n=0, trap=1, is_jal=1)),
            (False, cin()),
        ],
        "trap_beats_redirect": [
            (True, cin(rst_n=0)),
            (False, trap_jal),
            (False, trap_jal),
        ],
        "side_flags_ignored": [
            (True, cin(rst_n=0)),
            (False, cin(taken=1, misaligned=1)),
        ],
        "load_then_store": [
            (True, cin(rst_n=0)),
            (False, load),
            (False, store),
        ],
    }
    built = {name: case(name, raw) for name, raw in specs.items()}
    check_directed(built)
    rng = random.Random(SEED)
    random_raw = [(True, cin(rst_n=0))] + random_legal(rng)
    built["random_legal"] = case("random_legal", random_raw)
    assert len(built["random_legal"]["steps"]) == RANDOM_STEPS + 1
    order = list(specs) + ["random_legal"]
    cases = []
    for name in order:
        item = built[name]
        for out in item["_outs"]:
            check_shape(out)
        cases.append({"name": name, "steps": item["steps"]})
    assert len(cases) == 17
    return cases


def main() -> None:
    cases = build()
    text = json.dumps({"cases": cases}, indent=2) + "\n"
    (HERE / "cases.json").write_text(text, encoding="utf-8", newline="\n")
    print(f"rv32i_pctl cases={len(cases)} random_steps={RANDOM_STEPS} seed={SEED:#x}")


if __name__ == "__main__":
    main()
