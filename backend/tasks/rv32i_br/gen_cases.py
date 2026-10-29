"""All funct3 boundary pairs and 256 deterministic random steps, seed 0xB12."""

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
    for f3 in range(8):
        add(f"funct3_{f3}_equal_signed_unsigned_corners", [
            {"a": a, "b": b, "funct3": f3} for a in corners for b in corners])
    rng = random.Random(0xB12)
    add("random_seed_b12", [{"a": rng.getrandbits(32), "b": rng.getrandbits(32),
                            "funct3": rng.randrange(8)} for _ in range(256)])
    return cases


if __name__ == "__main__":
    Path(__file__).with_name("cases.json").write_text(
        json.dumps({"cases": build_cases()}, indent=2) + "\n", encoding="utf-8")
