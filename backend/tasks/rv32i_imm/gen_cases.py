"""Regenerate locked cases from the contract, with seed 0x1AA and 256 random steps."""

import json
import random
from pathlib import Path

from golden import golden_step


def build_cases() -> list[dict]:
    cases = []

    def add(name, words):
        steps = []
        for instr in words:
            inputs = {"instr": instr}
            _, expected = golden_step({}, inputs, False)
            steps.append({"inputs": inputs, "rst": False, "expected": expected})
        cases.append({"name": name, "steps": steps})

    # One-hot and one-cold instruction payloads isolate every immediate bit,
    # including the sign bit, and prove that register/funct3 bits are ignored.
    for opcode in (0x03, 0x13, 0x67, 0x23, 0x63, 0x37, 0x17, 0x6F):
        payloads = [0, 0xFFFFFF80, 0x80000000, 0x7FFFFF80, 0x55555580, 0xAAAAAA80]
        payloads += [1 << bit for bit in range(7, 32)]
        payloads += [0xFFFFFF80 ^ (1 << bit) for bit in range(7, 32)]
        add(f"opcode_{opcode:02x}_bit_mapping", [p | opcode for p in payloads])
    add("every_unlisted_opcode", [p | op for op in range(128)
        if op not in (0x03, 0x13, 0x67, 0x23, 0x63, 0x37, 0x17, 0x6F)
        for p in (0, 0xFFFFFF80, 0x80000000)])
    rng = random.Random(0x1AA)
    add("random_seed_1aa", [rng.getrandbits(32) for _ in range(256)])
    return cases


if __name__ == "__main__":
    Path(__file__).with_name("cases.json").write_text(
        json.dumps({"cases": build_cases()}, indent=2) + "\n", encoding="utf-8")
