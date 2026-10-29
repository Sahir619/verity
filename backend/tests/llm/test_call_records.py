"""R3: finish_reason and reasoning tokens are recorded per model call and reach the call metadata."""

import json
from types import SimpleNamespace

import pytest

from verity.config import Settings
from verity.core.llm_types import CallMeta, LLMResponse
from verity.llm.openai_compat import OpenAICompatProvider
from verity.roles.common import call_parsed, merge_meta
from verity.roles.parsing import ParseError
from verity.roles.prompts import PromptTemplate
from test_gateway_fake_cassette import ScriptedProvider, make_gateway

def reply(text: str, finish: str, reasoning: int, out: int = 500) -> LLMResponse:
    return LLMResponse(
        text=text, input_tokens=100, output_tokens=out, model="m", request_id="req", latency_ms=3,
        reasoning_tokens=reasoning, finish_reason=finish,
    )

async def test_the_gateway_keeps_one_record_per_finished_call(settings: Settings) -> None:
    provider = ScriptedProvider([reply("a", "length", 450), reply("b", "stop", 120, out=300)])
    gateway, _ = make_gateway(settings, provider)
    await gateway.call("rtl_gen", system="s", user="u", tags={"attempt": "1", "task_id": "mac4"})
    await gateway.call("repair", system="s", user="u", tags={"attempt": "2"}, temperature=1.0, top_p=0.95)
    first, second = gateway.calls
    assert (first["finishReason"], first["reasoningTokens"], first["outputTokens"]) == ("length", 450, 500)
    assert first["maxOutputTokens"] == settings.budget.call.rtl_max_output
    assert first["tags"] == {"attempt": "1"}
    assert (second["finishReason"], second["reasoningTokens"], second["role"]) == ("stop", 120, "repair")
    assert (second["temperature"], second["topP"]) == (1.0, 0.95)
    assert provider.requests[0].top_p is None and provider.requests[1].top_p == 0.95
    json.dumps(gateway.calls)  # must be serialisable for final/llm-calls.json

def test_merge_meta_sums_reasoning_and_lists_every_finish_reason() -> None:
    def meta(finish: str, reasoning: int) -> CallMeta:
        return CallMeta("rtl_gen", "m", "r", "p@1", 10, 20, 5, 0.01, finish_reason=finish, reasoning_tokens=reasoning)

    merged = merge_meta(meta("length", 15), meta("stop", 7))
    assert merged.finish_reason == "length,stop" and merged.reasoning_tokens == 22

async def test_call_parsed_puts_finish_reason_and_reasoning_on_the_meta() -> None:
    texts = [reply("bad", "length", 400), reply("good", "stop", 90)]

    class Gateway:
        async def call(self, role, *, system, user, response_format="text", prompt_version="", tags=None, **kw):
            return texts.pop(0), 0.002

    def parse(text: str) -> str:
        if text != "good":
            raise ParseError("no block")
        return text

    template = PromptTemplate(file="t", version="t@1", system="s", user="u")
    parsed = await call_parsed(
        Gateway(), "rtl_gen", template=template, system="s", user="u", parse=parse, tags={}, format_hint="h"  # type: ignore[arg-type]
    )
    assert parsed.meta.finish_reason == "length,stop" and parsed.meta.reasoning_tokens == 490

def test_usage_reads_reasoning_tokens_from_the_completion_details() -> None:
    usage = SimpleNamespace(
        prompt_tokens=50, completion_tokens=206, completion_tokens_details=SimpleNamespace(reasoning_tokens=150)
    )
    req = SimpleNamespace(system="", user="")
    assert OpenAICompatProvider._usage(SimpleNamespace(usage=usage), req, "x") == (50, 206, 150)  # type: ignore[arg-type]
    bare = SimpleNamespace(prompt_tokens=5, completion_tokens=9, completion_tokens_details=None)
    assert OpenAICompatProvider._usage(SimpleNamespace(usage=bare), req, "x") == (5, 9, 0)  # type: ignore[arg-type]
