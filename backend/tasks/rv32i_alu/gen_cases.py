"""All operations, boundary cross-products, masked shifts and 256 seed 0xA10 steps."""

import json
import random
from pathlib import Path

from golden import golden_step


def build_cases() -> list[dict]:
    cases = []

    def add(name, vectors):
        steps = []
        for inputs in vectors:
            _, expected = golden_step({}, inputs, False)
            steps.append({"inputs": inputs, "rst": False, "expected": expected})
        cases.append({"name": name, "steps": steps})

    corners = (0, 1, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF)
    for op in range(16):
        add(f"op_{op}_boundary_cross_product", [
            {"a": a, "b": b, "op": op} for a in corners for b in corners])
        add(f"op_{op}_shift_amount_and_high_bits", [
            {"a": a, "b": high | amount, "op": op}
            for a in corners for amount in (0, 1, 31)
            for high in (0, 0x20, 0x80000000, 0xFFFFFFE0)])
    rng = random.Random(0xA10)
    add("random_seed_a10", [{"a": rng.getrandbits(32), "b": rng.getrandbits(32),
                            "op": rng.randrange(16)} for _ in range(256)])
    return cases


if __name__ == "__main__":
    Path(__file__).with_name("cases.json").write_text(
        json.dumps({"cases": build_cases()}, indent=2) + "\n", encoding="utf-8")
