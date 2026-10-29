"""Exhaustive funct3/funct7 legality, every opcode, and 256 seed 0xDEC steps."""

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

    # All 128 funct7 values for each funct3, including immediate high bits
    # that must be ignored for the six non-shift OP-IMM operations.
    for opcode in (0x13, 0x33):
        for f3 in range(8):
            add(f"opcode_{opcode:02x}_funct3_{f3}_every_funct7", [
                (f7 << 25) | (31 << 20) | (17 << 15) | (f3 << 12) | (9 << 7) | opcode
                for f7 in range(128)])
    for opcode in (0x37, 0x17, 0x6F, 0x67, 0x63, 0x03, 0x23, 0x0F):
        add(f"group_{opcode:02x}_all_funct3", [
            (f7 << 25) | payload | (f3 << 12) | opcode
            for f3 in range(8) for f7 in (0, 1, 0x20, 0x7F)
            for payload in (0, 0x01FF8F80)])
    add("every_unlisted_opcode", [p | op for op in range(128)
        if op not in (0x37, 0x17, 0x6F, 0x67, 0x63, 0x03, 0x23, 0x0F, 0x13, 0x33)
        for p in (0, 0xFFFFFF80)])
    add("ecall_ebreak_and_system", [0x00000073, 0x00100073, 0xC0002073, 0xFFFFFFFF])
    rng = random.Random(0xDEC)
    add("random_seed_dec", [rng.getrandbits(32) for _ in range(256)])
    return cases


if __name__ == "__main__":
    Path(__file__).with_name("cases.json").write_text(
        json.dumps({"cases": build_cases()}, indent=2) + "\n", encoding="utf-8")
