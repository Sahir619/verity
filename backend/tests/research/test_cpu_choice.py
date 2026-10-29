"""CPU capability answers use fixed code data and the fake provider only."""

import pytest
from pydantic import TypeAdapter

from verity.contracts import ParseReply, to_wire
from verity.core.parse import build_system_prompt, final_verdict, parse_intent
from verity.cpu.kinds import CPU_KINDS
from verity.llm.fake import FakeProvider
from verity.research.serper import FakeSearchAdapter
from test_fact_check import Scripted, make


@pytest.mark.parametrize("text", ["Build a CPU", "I want a processor", "Which CPU can you build?", "a multicycle RISC-V core", "build a microcontroller"])
async def test_cpu_choices_from_code_survive_wire_round_trip(text, packs, settings):
    got = await parse_intent(text, packs, FakeProvider(settings, delay_s=0), settings)
    assert got.reply.verdict == "cpu_choice"
    wire = to_wire(got.reply)
    assert wire["cpuKinds"] == list(CPU_KINDS)
    assert [k["id"] for k in wire["cpuKinds"] if k["buildable"]] == ["rv32i_micro"]
    assert wire["closest"] is None and wire["research"] is None
    assert to_wire(TypeAdapter(ParseReply).validate_python(wire)) == wire
    assert final_verdict(got, "failed") == "cpu_choice"


async def test_model_cannot_add_a_kind_or_make_a_gpu_buildable(packs, settings):
    model = Scripted({"class": "cpu_choice", "verdict": "buildable_now", "cpuKinds": [{"id": "invented", "buildable": True}]})
    cpu = await parse_intent("build a processor", packs, model, settings)
    assert to_wire(cpu.reply)["cpuKinds"] == list(CPU_KINDS)
    gpu = await parse_intent("build a graphics processor", packs, model, settings)
    assert gpu.reply.verdict == "not_buildable_here"
    assert gpu.reply.reason == "Verity cannot build GPUs yet."
    assert gpu.reply.closest is None and "cpuKinds" not in to_wire(gpu.reply)


@pytest.mark.parametrize("text", ["What is a CPU?", "How does a processor work?", "Explain GPUs"])
async def test_general_questions_stay_questions(text, packs, settings):
    got = await parse_intent(text, packs, FakeProvider(settings, delay_s=0), settings)
    assert got.reply.intent_class == "question" and got.reply.verdict == "not_a_chip"


async def test_cpu_and_gpu_do_not_search_or_start_runs(packs, settings):
    adapter = FakeSearchAdapter()
    manager, store = await make(settings, packs, FakeProvider(settings, delay_s=0), adapter)
    try:
        cpu = await manager.parse("build a CPU", session_id="cpu-test")
        gpu = await manager.parse("build a GPU", session_id="cpu-test")
        assert cpu.verdict == "cpu_choice" and gpu.reason == "Verity cannot build GPUs yet."
        assert cpu.research is None and gpu.research is None and adapter.calls == []
        assert (await manager.list_runs()).runs == []
    finally:
        await manager.shutdown()
        await store.close()


def test_cpu_kinds_are_separate_from_chip_families(packs):
    prompt = build_system_prompt(packs)
    assert "processors, CPUs or GPUs" not in prompt
    assert '"cpu_choice"' in prompt and "never" in prompt.lower()
    assert "rv32i_micro:" not in prompt
