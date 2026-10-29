"""Generate cases.json for rv32i_ctl. Fixed seed. Expected values come from golden_step.

Every case starts with reset. Directed cases walk each path in the state table. Codes 6 and 7
behave as HALT in the golden model. No input sequence from reset can enter them, because the
state table only produces 0 to 5, so that path is checked here by calling golden_step with
those codes directly. Random steps are legal instruction sequences (one class of flag at a time).
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import golden  # noqa: E402

SEED = 0x5232C7
RANDOM_STEPS = 200
HERE = Path(__file__).resolve().parent
FLAGS = ("mem_ready", "is_load", "is_store", "is_branch", "is_jal", "is_jalr", "trap", "taken", "misaligned")
ZEROS = {
    "state": 0, "mem_valid": 0, "mem_we": 0, "ir_we": 0,
    "rf_wen": 0, "pc_we": 0, "pc_sel": 0, "halted": 0,
}


def cin(rst_n: int = 1, **kw: int) -> dict[str, int]:
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
    return {"name": name, "steps": steps, "_outs": outs}


def expect(out: dict, **kw: int) -> None:
    want = dict(ZEROS)
    want.update(kw)
    assert out == want, (out, want)


def reach_exec() -> list[tuple[bool, dict]]:
    return [
        (True, cin(rst_n=0)),
        (False, cin(mem_ready=1)),
        (False, cin()),
    ]


def check_directed(built: dict) -> None:
    fetch = built["fetch_waits_for_mem_ready"]["_outs"]
    expect(fetch[0], state=0, mem_valid=1)
    expect(fetch[1], state=0, mem_valid=1)
    expect(fetch[2], state=0, mem_valid=1)
    expect(fetch[3], state=1)

    ir = built["fetch_ir_we_when_ready"]["_outs"]
    expect(ir[0], state=0, mem_valid=1, ir_we=1)
    expect(ir[1], state=1)

    trap = built["trap_to_halt"]["_outs"]
    expect(trap[2], state=5, halted=1)
    expect(trap[3], state=5, halted=1)
    expect(trap[4], state=0, mem_valid=1)

    load = built["load_aligned"]["_outs"]
    expect(load[3], state=3, mem_valid=1)
    expect(load[4], state=3, mem_valid=1)
    expect(load[5], state=4, rf_wen=1, pc_we=1)
    expect(load[6], state=0, mem_valid=1)

    store = built["store_aligned"]["_outs"]
    expect(store[3], state=3, mem_valid=1, mem_we=1)
    expect(store[4], state=4, rf_wen=1, pc_we=1)

    expect(built["load_misaligned_to_halt"]["_outs"][3], state=5, halted=1)
    expect(built["store_misaligned_to_halt"]["_outs"][3], state=5, halted=1)
    expect(built["branch_taken"]["_outs"][3], state=4, rf_wen=1, pc_we=1, pc_sel=1)
    expect(built["branch_not_taken"]["_outs"][3], state=4, rf_wen=1, pc_we=1)
    expect(built["jal"]["_outs"][3], state=4, rf_wen=1, pc_we=1, pc_sel=1)
    expect(built["jalr"]["_outs"][3], state=4, rf_wen=1, pc_we=1, pc_sel=1)
    halt = built["halt_stays_until_reset"]["_outs"]
    expect(halt[2], state=5, halted=1)
    expect(halt[3], state=5, halted=1)
    expect(halt[4], state=5, halted=1)
    expect(halt[5], state=0, mem_valid=1, ir_we=1)
    expect(built["wb_enters_fetch_with_ir_we"]["_outs"][4], state=0, mem_valid=1, ir_we=1)
    expect(built["plain_op_to_wb"]["_outs"][3], state=4, rf_wen=1, pc_we=1)


def check_codes_6_and_7() -> None:
    """Unreachable from reset. The contract still defines them, so the golden model is checked here."""
    busy = cin(rst_n=1, mem_ready=1, trap=1, is_load=1, is_store=1, is_jal=1, taken=1)
    for code in (6, 7):
        # While the register holds 6 or 7 the outputs match HALT, except state shows that code.
        expect(golden.outputs_of(code, busy), state=code, halted=1)
        assert golden.next_state(code, busy) == 5
        # The shared test samples after the edge, so one step leaves 6 or 7 and shows HALT.
        nxt, out = golden.golden_step({"state": code}, busy, False)
        expect(out, state=5, halted=1)
        assert nxt["state"] == 5


def one_instruction(rng: random.Random, kind: str) -> list[tuple[bool, dict]]:
    steps: list[tuple[bool, dict]] = []
    for _ in range(rng.randint(0, 2)):
        steps.append((False, cin(mem_ready=0)))
    steps.append((False, cin(mem_ready=1)))
    if kind == "trap":
        steps.append((False, cin(trap=1)))
        for _ in range(rng.randint(0, 2)):
            steps.append((False, cin(trap=1, mem_ready=rng.randint(0, 1))))
        steps.append((True, cin(rst_n=0)))
        return steps
    steps.append((False, cin()))
    if kind == "load":
        mis = rng.randint(0, 1)
        steps.append((False, cin(is_load=1, misaligned=mis, mem_ready=0)))
        if mis:
            steps.append((True, cin(rst_n=0)))
            return steps
        if not mis:
            for _ in range(rng.randint(0, 2)):
                steps.append((False, cin(is_load=1, mem_ready=0)))
            steps.append((False, cin(is_load=1, mem_ready=1)))
            steps.append((False, cin(mem_ready=rng.randint(0, 1))))
        return steps
    if kind == "store":
        mis = rng.randint(0, 1)
        steps.append((False, cin(is_store=1, misaligned=mis, mem_ready=0)))
        if mis:
            steps.append((True, cin(rst_n=0)))
            return steps
        if not mis:
            for _ in range(rng.randint(0, 2)):
                steps.append((False, cin(is_store=1, mem_ready=0)))
            steps.append((False, cin(is_store=1, mem_ready=1)))
            steps.append((False, cin(mem_ready=rng.randint(0, 1))))
        return steps
    if kind == "branch":
        taken = rng.randint(0, 1)
        steps.append((False, cin(is_branch=1, taken=taken)))
        steps.append((False, cin(mem_ready=rng.randint(0, 1))))
        return steps
    if kind == "jal":
        steps.append((False, cin(is_jal=1)))
        steps.append((False, cin(mem_ready=rng.randint(0, 1))))
        return steps
    if kind == "jalr":
        steps.append((False, cin(is_jalr=1)))
        steps.append((False, cin(mem_ready=rng.randint(0, 1))))
        return steps
    steps.append((False, cin()))
    steps.append((False, cin(mem_ready=rng.randint(0, 1))))
    return steps


def random_legal(rng: random.Random) -> list[tuple[bool, dict]]:
    kinds = ["alu", "load", "store", "branch", "jal", "jalr", "trap"]
    steps: list[tuple[bool, dict]] = []
    for kind in kinds:
        steps.extend(one_instruction(rng, kind))
    while len(steps) < RANDOM_STEPS:
        steps.extend(one_instruction(rng, rng.choice(kinds)))
    return steps[:RANDOM_STEPS]


def build() -> list[dict]:
    check_codes_6_and_7()
    specs = {
        "fetch_waits_for_mem_ready": [
            (True, cin(rst_n=0, mem_ready=0)),
            (False, cin(mem_ready=0)),
            (False, cin(mem_ready=0)),
            (False, cin(mem_ready=1)),
        ],
        "fetch_ir_we_when_ready": [
            (True, cin(rst_n=0, mem_ready=1)),
            (False, cin(mem_ready=1)),
        ],
        "trap_to_halt": [
            (True, cin(rst_n=0)),
            (False, cin(mem_ready=1)),
            (False, cin(trap=1)),
            (False, cin(trap=1, mem_ready=1, is_load=1)),
            (True, cin(rst_n=0)),
        ],
        "load_aligned": reach_exec() + [
            (False, cin(is_load=1, mem_ready=0)),
            (False, cin(is_load=1, mem_ready=0)),
            (False, cin(is_load=1, mem_ready=1)),
            (False, cin(mem_ready=0)),
        ],
        "store_aligned": reach_exec() + [
            (False, cin(is_store=1, mem_ready=0)),
            (False, cin(is_store=1, mem_ready=1)),
            (False, cin()),
        ],
        "load_misaligned_to_halt": reach_exec() + [
            (False, cin(is_load=1, misaligned=1)),
            (False, cin(is_load=1, misaligned=1, mem_ready=1)),
        ],
        "store_misaligned_to_halt": reach_exec() + [
            (False, cin(is_store=1, misaligned=1)),
            (False, cin(mem_ready=1)),
        ],
        "branch_taken": reach_exec() + [
            (False, cin(is_branch=1, taken=1)),
            (False, cin()),
        ],
        "branch_not_taken": reach_exec() + [
            (False, cin(is_branch=1, taken=0)),
        ],
        "jal": reach_exec() + [
            (False, cin(is_jal=1)),
        ],
        "jalr": reach_exec() + [
            (False, cin(is_jalr=1)),
        ],
        "halt_stays_until_reset": [
            (True, cin(rst_n=0)),
            (False, cin(mem_ready=1)),
            (False, cin(trap=1)),
            (False, cin(mem_ready=1, is_store=1, taken=1)),
            (False, cin(is_jal=1, is_jalr=1)),
            (True, cin(rst_n=0, mem_ready=1)),
        ],
        "wb_enters_fetch_with_ir_we": reach_exec() + [
            (False, cin()),
            (False, cin(mem_ready=1)),
        ],
        "plain_op_to_wb": reach_exec() + [
            (False, cin()),
            (False, cin(mem_ready=0)),
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
        cases.append({"name": name, "steps": item["steps"]})
    assert len(cases) == 15
    return cases


def main() -> None:
    cases = build()
    text = json.dumps({"cases": cases}, indent=2) + "\n"
    (HERE / "cases.json").write_text(text, encoding="utf-8", newline="\n")
    print(f"rv32i_ctl cases={len(cases)} random_steps={RANDOM_STEPS} seed={SEED:#x}")


if __name__ == "__main__":
    main()
