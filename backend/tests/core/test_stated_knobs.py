"""statedKnobs on POST /parse: code decides which knobs the sentence itself stated, from the model's answer checked against
the text. The model's word alone is never enough. Fake and scripted providers, $0."""

import asyncio
import json

from fastapi.testclient import TestClient

from verity.api.app import create_app
from verity.contracts import ParseInRange, ParseOutOfRange, to_wire
from verity.core.family import Knob
from verity.core.llm_types import LLMRequest, LLMResponse
from verity.core.parse import parse_request, stated_knobs
from verity.llm.fake import FakeProvider
from verity.runs.manager import RunManager
from verity.store import SqliteStore
from verity_test_support import FakeToolRunner, make_settings

MAC4 = [
    Knob(id="lanes", kind="int", default=4, min=1, max=8),
    Knob(id="width", kind="int", default=8, min=4, max=8),
    Knob(id="cycles", kind="int", default=16, min=4, max=32),
]
CNT8 = [Knob(id="width", kind="int", default=8, min=4, max=16), Knob(id="enable", kind="bool", default=True)]
FSM = [Knob(id="pattern", kind="bits", default="1011", min_len=3, max_len=8), Knob(id="overlap", kind="bool", default=True)]


def stated(knobs, values: dict, text: str) -> list[str]:
    return stated_knobs(knobs, values, values, text)


# ---------- integers: a number next to the knob's own word ----------
def test_an_int_knob_is_stated_by_a_number_next_to_its_word() -> None:
    values = {"lanes": 4, "width": 8, "cycles": 16}
    assert stated(MAC4, values, "an 8-bit multiply accumulate unit with 4 lanes and 16 cycles") == ["lanes", "width", "cycles"]
    assert stated(MAC4, values, "a multiply accumulate unit with sixteen cycles") == ["cycles"]
    assert stated(MAC4, values, "an eight bit mac with four parallel multipliers") == ["lanes", "width"]


def test_a_number_without_the_knobs_word_is_not_a_statement() -> None:
    values = {"lanes": 4, "width": 8, "cycles": 16}
    # "16" and "8" appear, but nothing says they are cycles or bits: the model filled these in.
    assert stated(MAC4, values, "a matrix multiply for 16 8 and 4 values") == []
    assert stated(MAC4, values, "a multiply accumulate unit") == []


def test_a_value_the_model_changed_is_not_what_the_sentence_said() -> None:
    # The sentence says 64-bit; the model's 64 was clamped to 32, so the 32 on the bench is an assumption.
    add8 = [Knob(id="width", kind="int", default=8, min=4, max=32)]
    assert stated_knobs(add8, {"width": 64}, {"width": 32}, "a 64-bit adder") == []
    assert stated_knobs(add8, {"width": 32}, {"width": 32}, "a 32-bit adder") == ["width"]


def test_a_knob_the_model_did_not_name_is_never_stated() -> None:
    assert stated_knobs(CNT8, {}, {"width": 8, "enable": True}, "an 8-bit counter with enable") == []


# ---------- booleans: the word, with its meaning ----------
def test_a_bool_knob_needs_its_word_and_the_right_meaning() -> None:
    assert stated(FSM, {"pattern": "1011", "overlap": True}, "detect 1011 with overlapping matches") == ["pattern", "overlap"]
    assert stated(FSM, {"pattern": "1011", "overlap": False}, "detect 1011, non-overlapping") == ["pattern", "overlap"]
    assert stated(FSM, {"pattern": "1011", "overlap": False}, "detect 1011 without overlap") == ["pattern", "overlap"]
    # The model said overlap on, but the sentence says "non-overlapping": the sentence did not state "on".
    assert stated(FSM, {"pattern": "1011", "overlap": True}, "detect 1011, non-overlapping") == ["pattern"]
    # No word, no statement.
    assert stated(FSM, {"pattern": "1011", "overlap": True}, "detect the pattern 1011") == ["pattern"]
    assert stated(CNT8, {"width": 8, "enable": True}, "an 8 bit counter with enable") == ["width", "enable"]
    assert stated(CNT8, {"width": 8, "enable": False}, "an 8 bit counter, enable disabled") == ["width", "enable"]
    assert stated(CNT8, {"width": 8, "enable": True}, "an 8 bit counter") == ["width"]


# ---------- bit strings ----------
def test_a_bit_string_must_be_written_in_the_sentence() -> None:
    values = {"pattern": "1101", "overlap": True}
    assert stated(FSM, values, "detect 1101") == ["pattern"]
    assert stated(FSM, values, "detect 1 1 0 1 in the stream") == ["pattern"]
    assert stated(FSM, values, "detect 1-1-0-1") == ["pattern"]
    assert stated(FSM, values, "detect a four bit pattern") == []  # the model invented 1101
    assert stated(FSM, values, "detect 11010") == []  # a longer run is a different pattern


def test_unknown_knob_ids_are_matched_by_their_own_name() -> None:
    depth = [Knob(id="depth", kind="int", default=8, min=2, max=64)]
    assert stated(depth, {"depth": 16}, "a fifo with depth 16") == ["depth"]
    assert stated(depth, {"depth": 16}, "a 16-bit fifo") == []


# ---------- through the parser ----------
class Scripted:
    name = "scripted"

    def __init__(self, text: str) -> None:
        self.text = text

    async def complete(self, req: LLMRequest) -> LLMResponse:
        return LLMResponse(text=self.text, input_tokens=1, output_tokens=1, model=req.model, request_id="x", latency_ms=1)


def parse(text: str, packs, settings, provider):
    return asyncio.run(parse_request(text, packs, provider, settings))


def test_the_parser_lists_the_stated_knobs_in_range(packs, settings) -> None:
    fake = FakeProvider(settings, delay_s=0)
    sized = parse("a 12 bit counter", packs, settings, fake)
    assert isinstance(sized, ParseInRange) and sized.params == {"width": 12, "enable": True}
    assert sized.stated_knobs == ["width"]  # enable took its default
    plain = parse("a counter", packs, settings, fake)
    assert isinstance(plain, ParseInRange) and plain.stated_knobs == []
    pattern = parse("detect the pattern 1101", packs, settings, fake)
    assert isinstance(pattern, ParseInRange) and pattern.stated_knobs == ["pattern"]
    assert to_wire(sized)["statedKnobs"] == ["width"] and to_wire(plain)["statedKnobs"] == []


def test_a_model_that_fills_every_knob_does_not_make_them_stated(packs, settings) -> None:
    filled = json.dumps(
        {"class": "in_family", "label": "mac", "family": "mac4", "params": {"lanes": 4, "width": 8, "cycles": 16}}
    )
    reply = parse("an 8-bit multiply accumulate unit", packs, settings, Scripted(filled))
    assert isinstance(reply, ParseInRange) and reply.params == {"lanes": 4, "width": 8, "cycles": 16}
    assert reply.stated_knobs == ["width"]  # only the width is in the sentence


def test_out_of_range_replies_check_the_closest_chips_knobs(packs, settings) -> None:
    clamped = json.dumps({"class": "in_family", "label": "adder", "family": "add8", "params": {"width": 64}})
    reply = parse("a 64-bit adder", packs, settings, Scripted(clamped))
    assert isinstance(reply, ParseOutOfRange) and reply.closest is not None and reply.closest.params == {"width": 32}
    assert reply.stated_knobs == []  # 32 is the bench's limit, not what the sentence said
    gpu = parse("an RTX 6090", packs, settings, FakeProvider(settings, delay_s=0))
    assert isinstance(gpu, ParseOutOfRange) and gpu.stated_knobs == []
    no_chip = parse("a pancake", packs, settings, FakeProvider(settings, delay_s=0))
    assert isinstance(no_chip, ParseOutOfRange) and no_chip.closest is None and no_chip.stated_knobs is None
    assert "statedKnobs" not in to_wire(no_chip)  # older clients see the same body as before


# ---------- the route ----------
def test_post_parse_sends_stated_knobs_in_camel_case(tmp_path, packs) -> None:
    settings = make_settings(tmp_path / "data")
    store = SqliteStore(settings)
    manager = RunManager(settings, store, FakeProvider(settings, delay_s=0), FakeToolRunner(), packs)
    app = create_app(settings, manager, startup=[store.open], shutdown=[store.close])
    with TestClient(app) as client:
        body = client.post("/parse", json={"text": "a 12 bit counter"}).json()
        assert body["result"] == "in_range" and body["statedKnobs"] == ["width"]
        assert body["params"] == {"width": 12, "enable": True}
        bare = client.post("/parse", json={"text": "a counter"}).json()
        assert bare["statedKnobs"] == []
