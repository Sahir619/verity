"""Contract checks for the cache dependency used by the unified bench."""

import importlib.util
import json

from verity.config import AGENT_ROOT


def model():
    spec = importlib.util.spec_from_file_location("cache_golden", AGENT_ROOT / "tasks/rv32i_cache/golden.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.golden_step


def test_cached_pack_uses_exact_locked_isa_and_dependencies(packs):
    original = packs["rv32i_core"].root
    cached = packs["rv32i_cache_core"].root
    for name in ("golden.py", "rv32i_asm.py"):
        assert (cached / name).read_bytes() == (original / name).read_bytes()
    programs = sorted((original / "programs").glob("*.s"))
    assert {p.name for p in (cached / "programs").glob("*.s")} == {p.name for p in programs}
    for program in programs:
        assert (cached / "programs" / program.name).read_bytes() == program.read_bytes()
    assert packs["rv32i_cache_core"].test_count == 2 * len(programs)
    assert packs["rv32i_cache_core"].ports == packs["rv32i_core"].ports
    expected = ((original / "blocks.v").read_text() + "\n" + (original / "reference.v").read_text()
                + "\n" + (packs["rv32i_cache"].root / "reference.v").read_text())
    assert (cached / "blocks.v").read_text() == expected


def test_cache_vectors_reset_and_replay_without_mutating_state(packs):
    pack = packs["rv32i_cache"]
    cases = json.loads((pack.root / "cases.json").read_text())["cases"]
    assert len(cases) == pack.test_count
    assert len(next(c for c in cases if c["name"] == "random_legal")["steps"]) >= 201
    step_model = model()
    for case in cases:
        assert case["steps"][0]["rst"] and not case["steps"][0]["inputs"]["rst_n"]
        state = {}
        for step in case["steps"]:
            before = repr(state)
            next_state, out = step_model(state, step["inputs"], step["rst"])
            assert repr(state) == before
            assert out == step["expected"]
            state = next_state


def test_acknowledged_byte_write_invalidates_then_reload_uses_new_word():
    step = model()
    _, reset_out = step({}, {"rst_n": 0}, True)
    assert reset_out["mem_valid"] == reset_out["cpu_ready"] == 0
    read = dict(rst_n=1, cpu_valid=1, cpu_addr=0x1004, cpu_wstrb=0)
    state, out = step({}, {**read, "mem_ready": 1, "mem_rdata": 0x11223344}, False)
    state, out = step(state, {**read, "mem_ready": 0, "mem_rdata": 99}, False)
    assert out["cpu_ready"] == 1 and out["cpu_rdata"] == 0x11223344 and out["mem_valid"] == 0
    write = {**read, "cpu_wstrb": 2, "cpu_wdata": 0xAA00, "mem_rdata": 0x11223344}
    state, out = step(state, {**write, "mem_ready": 0}, False)
    assert out["mem_valid"] == 1 and out["cpu_ready"] == 0
    state, out = step(state, {**write, "mem_ready": 1}, False)
    state, out = step(state, {**read, "mem_ready": 0}, False)
    assert out["mem_valid"] == 1 and out["cpu_ready"] == 0
    state, out = step(state, {**read, "mem_ready": 1, "mem_rdata": 0x1122AA44}, False)
    assert out["cpu_rdata"] == 0x1122AA44
    state, out = step(state, {**read, "rst_n": 0}, True)
    assert out["mem_valid"] == 1 and out["cpu_ready"] == 0
