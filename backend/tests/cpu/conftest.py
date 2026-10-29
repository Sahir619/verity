"""Small CPU packs, independent of the static packs being built in other worktrees."""

import pytest

from verity.core.pack import TaskPack, write_manifest
from verity.runs.cpu_job import BLOCKS


@pytest.fixture
def cpu_packs(tmp_path):
    packs = {}
    cores = ("rv32i_core", "rv32i_pipe2_core", "rv32i_cache_core")
    for pack_id in [*(p for p, _ in BLOCKS), "rv32i_pctl", "rv32i_cache", *cores]:
        root = tmp_path / "tasks" / pack_id
        root.mkdir(parents=True)
        (root / "task.toml").write_text(
            f'''schema = "verity.task/1"
id = "{pack_id}"
top = "{pack_id}"
title = "{pack_id}"
summary = "fixture"
spec_text = "fixture"
family = "cpu"
eval_only = true
latency_cycles = 0
test_count = 1
[clock]
name = ""
[reset]
name = ""
[eqy]
enabled = false
[[ports]]
name = "a"
dir = "in"
width = 1
[[ports]]
name = "y"
dir = "out"
width = 1
''', encoding="utf-8",
        )
        (root / "golden.py").write_text("# golden fixture\n", encoding="utf-8")
        (root / "test_dut.py").write_text("# testbench fixture\n", encoding="utf-8")
        (root / "reference.v").write_text(f"module {pack_id}(input logic a, output logic y); assign y = a; endmodule\n", encoding="utf-8")
        if pack_id in cores:
            (root / "blocks.v").write_text("// static blocks must be replaced\n", encoding="utf-8")
            (root / "programs").mkdir()
            (root / "programs" / "example.s").write_text("ebreak\n", encoding="utf-8")
        write_manifest(root)
        packs[pack_id] = TaskPack.load(root)
    return packs
