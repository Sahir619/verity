"""POST /runs/{id}/ask against the real run r_27fed084a749432ba42ca6981f3cc336 (cnt8, 10 bits, goal speed), copied into
tests/fixtures: the code answers, the number validator, the Verilog strip, the router enum check, propose_run, money,
and the route's ownership, 409 and rate limit. No network: the fake provider or a scripted one."""

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from verity.api.app import create_app
from verity.api.auth import anonymous_sid
from verity.ask.intent import check_router_answer, prefilter
from verity.ask.propose import apply_change, priority_from_words
from verity.ask.reply import bad_numbers, build_facts, is_json_fragment, numbers_in, strip_verilog
from verity.ask.tools import explain_selection, load_card
from verity.contracts import AskAnswer, AskNewChip, AskProposal, AskRefused, parse_event
from verity.core.captions import plain_dashes
from verity.core.errors import AdmissionDenied, Conflict, Forbidden
from verity.core.family import Knob
from verity.core.fitness import pick_winner
from verity.core.llm_types import LLMRequest, LLMResponse
from verity.core.parse import live_families
from verity.core.records import RunRecord
from verity.llm.fake import FakeProvider
from verity.research import FakeSearchAdapter, ResearchService
from verity.runs.manager import RunManager
from verity.store import SqliteStore
from verity_test_support import FakeToolRunner, make_settings

RUN_ID = "r_27fed084a749432ba42ca6981f3cc336"
FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / RUN_ID
OWNER = "fixture-owner"


# ---------- helpers ----------
async def seed(store: SqliteStore, *, session_id: str = OWNER, status: str | None = None) -> RunRecord:
    """The real run: its row, its 106 stored events and the report files the ask tools read."""
    data = json.loads((FIXTURE / "run.json").read_text(encoding="utf-8"))
    data["session_id"] = session_id
    if status is not None:
        data["status"] = status
    run = RunRecord.model_validate(data)
    await store.create_run(run)
    for event in json.loads((FIXTURE / "runlog.json").read_text(encoding="utf-8"))["events"]:
        await store.append_event(run.id, parse_event(event))
    for path in sorted(FIXTURE.rglob("*")):
        rel = path.relative_to(FIXTURE).as_posix()
        if path.is_file() and rel.startswith(("candidates/", "final/")):
            await store.write_run_file(run.id, rel, path.read_bytes(), "text/plain")
    return run


class Scripted:
    """Answers by call purpose (tags["purpose"]), in order; remembers every request. Raises if a purpose has no answer."""

    name = "scripted"

    def __init__(self, answers: Mapping[str, list[str]] | None = None) -> None:
        self.answers = {k: list(v) for k, v in (answers or {}).items()}
        self.requests: list[LLMRequest] = []
        self.served: str | None = None  # the model that answers when it is not the asked one (a fallback chain)

    async def complete(self, req: LLMRequest) -> LLMResponse:
        self.requests.append(req)
        purpose = req.tags.get("purpose", "")
        queue = self.answers.get(purpose)
        if not queue:
            raise AssertionError(f"unexpected model call: {purpose}")
        text = queue.pop(0) if len(queue) > 1 else queue[0]
        return LLMResponse(
            text=text, input_tokens=1000, output_tokens=200, model=self.served or req.model, request_id="x", latency_ms=1
        )

    async def aclose(self) -> None:
        return None

    def calls(self, purpose: str) -> list[LLMRequest]:
        return [r for r in self.requests if r.tags.get("purpose") == purpose]


async def manager_for(tmp_path, packs, provider=None, *, settings=None, research: bool = False, **seed_kw):
    settings = settings or make_settings(tmp_path / "data")
    store = SqliteStore(settings)
    await store.open()
    run = await seed(store, **seed_kw)
    provider = provider or FakeProvider(settings, delay_s=0)
    service = ResearchService(settings, store, provider, FakeSearchAdapter(), live_families(packs)) if research else None
    manager = RunManager(settings, store, provider, FakeToolRunner(), packs, research=service)
    return manager, store, run


def paid_settings(tmp_path, **limits):
    """A real-provider configuration (no network: the provider object is Scripted) so spend is recorded."""
    base = make_settings(tmp_path / "data")
    llm = base.llm.model_copy(update={"api_key": SecretStr("test-key")})
    return base.model_copy(update={"provider": "openai_compat", "llm": llm, "limits": base.limits.model_copy(update=limits)})


# ---------- the two expected answers ----------
async def test_why_was_this_kept_states_the_real_reasons_without_a_model_call(tmp_path, packs) -> None:
    provider = Scripted()  # any model call fails the test
    manager, store, run = await manager_for(tmp_path, packs, provider)
    try:
        reply = await manager.ask(RUN_ID, session_id=OWNER, text="why was this kept?")
    finally:
        await store.close()
    assert isinstance(reply, AskAnswer) and reply.templated and provider.requests == []
    assert reply.text == "v1 was kept because no version beat it, and a tie goes to the older version. Your goal: faster."
    assert reply.bullets[0] == (
        "v2 (simplify) and v3 (optimize) passed 10 of 10 tests and measured the same as v1: "
        "72.62 um2, 35 cells, 0.389 ns, so +0.0% vs v1."
    )
    assert "Their synthesis and timing reports match v1 exactly." in reply.bullets
    assert "v4 (explore) had the same Verilog as v2, so it was dropped before its tests." in reply.bullets
    assert reply.bullets[-1] == "Round 2 runs only when round 1 beats v1, so it did not run."
    assert reply.basis == "From this run: versions, tests, reports and the tie rule."


async def test_what_limits_the_clock_names_the_path_from_sta_v1(tmp_path, packs) -> None:
    provider = Scripted()
    manager, store, _ = await manager_for(tmp_path, packs, provider)
    try:
        reply = await manager.ask(RUN_ID, session_id=OWNER, text="what limits the clock?")
    finally:
        await store.close()
    assert isinstance(reply, AskAnswer) and reply.templated and provider.requests == []
    assert reply.text == (
        "The slowest path in v1 runs from flip-flop _61_ to flip-flop _69_ through 5 gates (AND4, AND4, AND3, XNOR2, NOR2): "
        "the signal arrives after 0.350 ns, plus 0.039 ns setup, which sets the 0.389 ns minimum clock period."
    )
    assert "The first flip-flop takes 0.096 ns to put out its value; the 5 gates take the other 0.254 ns." in reply.bullets
    assert "OpenSTA" in reply.basis and "v1" in reply.basis


async def test_selection_replays_core_fitness_and_names_the_tie_break(tmp_path, packs) -> None:
    settings = make_settings(tmp_path / "data")
    store = SqliteStore(settings)
    await store.open()
    try:
        run = await seed(store)
        card = await load_card(store, run)
    finally:
        await store.close()
    assert pick_winner(card.versions) == card.winner_id == "c1"
    assert card.duplicates == {"v4": "v2"} and not card.round2_ran and card.skipped_round2

    # Same score, v2 and v3 now larger: the tie-break that decides is the area, not the age.
    for c in card.versions[1:3]:
        c.ppa = c.ppa.model_copy(update={"area_um2": 80.0})  # type: ignore[union-attr]
    headline, _ = explain_selection(card)
    assert headline.startswith("v1 was kept because no version beat it, and a tie goes to the smaller area.")

    # A child that beats v1 is named with its own score and deltas.
    v3 = card.versions[2]
    v3.fitness = v3.fitness.model_copy(update={"score": 12.5})  # type: ignore[union-attr]
    v3.ppa = v3.ppa.model_copy(update={"area_um2": 72.62, "clock_period_ns": 0.35})  # type: ignore[union-attr]
    card.winner_id = "c3"
    headline, _ = explain_selection(card)
    assert headline.startswith("v3 was kept: it had the best score, +12.50 vs v1 (area +0.0%, clock period -10.0%")


async def test_selection_says_when_a_higher_scoring_version_was_worse_on_the_goal(tmp_path, packs) -> None:
    settings = make_settings(tmp_path / "data")
    store = SqliteStore(settings)
    await store.open()
    try:
        run = await seed(store)
        card = await load_card(store, run)
    finally:
        await store.close()
    v1, v2 = card.versions[0], card.versions[1]
    assert v1.ppa is not None and v2.ppa is not None and v2.fitness is not None and v2.fitness.priority == "speed"
    slower = round(v1.ppa.clock_period_ns + 0.01, 3)  # type: ignore[operator]
    v2.ppa = v2.ppa.model_copy(update={"clock_period_ns": slower})
    v2.fitness = v2.fitness.model_copy(update={"score": 3.0})  # the blended score still prefers v2
    card.winner_id = "c1"
    headline, bullets = explain_selection(card)
    assert headline.startswith("v1 was kept because v2 scored higher but was worse than v1 on your goal.")
    assert "A version can only be kept if it is no worse than v1 on the goal." in headline
    assert f"was worse than v1 on your goal: clock period {slower:.3f} ns vs {v1.ppa.clock_period_ns:.3f} ns, so it could not be kept." in bullets[0]
    assert bullets[0].startswith("v2 (") and "scored higher (+3.00)" in bullets[0]


async def test_drop_and_unknown_version_answers(tmp_path, packs) -> None:
    manager, store, _ = await manager_for(tmp_path, packs, Scripted())
    try:
        drop = await manager.ask(RUN_ID, session_id=OWNER, text="why was v4 dropped?")
        missing = await manager.ask(RUN_ID, session_id=OWNER, text="why was v9 dropped?")
        cost = await manager.ask(RUN_ID, session_id=OWNER, text="how much did this run cost?")
    finally:
        await store.close()
    assert drop.text == "v4 (explore) had the same Verilog as v2, so it was dropped before its tests."  # type: ignore[union-attr]
    assert missing.text == "This run has no version v9. It made v1, v2, v3 and v4."
    assert isinstance(cost, AskAnswer) and cost.text == "This run spent $0.018."
    assert "Nemotron 3 Ultra wrote the plan and first versions: 4 calls, $0.012." in cost.bullets


# ---------- chat answers: numbers and Verilog ----------
def test_numbers_are_compared_after_normalizing_their_format() -> None:
    from verity.ask.reply import numbers_in

    allowed = set(numbers_in("8,799 tokens; 0.389 ns (+0.0% vs v1); 72.62 um2; 10 of 10; 3 calls; $0.05 spent"))
    assert bad_numbers(["8,799 tokens, 0.3890 ns, +0.0% vs v1, 10 of 10 tests, v2 and S3, spent $0.050 (0.05)"], allowed) == []
    assert bad_numbers(["about 72.6 um2, 3.1% faster, 3% smaller, $3"], allowed) == ["72.6", "3.1%", "3%", "$3"]


FACT_NUMBERS = "v2 power 1.8620 mW (-4.3% vs v1), v1 power 1.9453 mW; period 2099.80 ps; 0.0084 mW idle; 35.0 cells; 0.5 ns"


def test_numerically_equal_values_pass_and_a_different_value_never_does() -> None:
    allowed = set(numbers_in(FACT_NUMBERS))
    equal = [
        "1.862 mW", "1.8620 mW", "1.86200 mW", "01.862 mW", "1.862mW",  # trailing zeros and a leading zero
        "2099.8 ps", "2099.800 ps", "2,099.8 ps",
        ".5 ns", "0.50 ns", ".0084 mW", "0.00840 mW",  # a leading zero dropped
        "35 cells", "35.00 cells", "4.30%", "4.3 %",
    ]
    for text in equal:
        assert bad_numbers([text], allowed) == [], text
    different = ["1.87 mW", "1.86 mW", "1.9 mW", "1.945 mW", "18.62 mW", "2099.9 ps", "209.98 ps", ".6 ns", ".0085 mW", "36 cells", "4.2%"]
    for text in different:
        assert len(bad_numbers([text], allowed)) == 1, text
    assert bad_numbers(["1.87 mW"], allowed) == ["1.87"]


def test_a_possessive_is_not_a_sized_literal_but_real_literals_are_still_stripped() -> None:
    kept = "v2's power is 1.8620 mW, v1's is 1.9453 mW. A 2's complement adder is fine. That is an estimate."
    assert strip_verilog(kept) == kept
    assert strip_verilog("It is 8'b1010. Then 4'hF and 16'd255, 8'sd5 and 1'b1. Plain words stay.") == "Plain words stay."


def test_a_piece_of_the_models_json_is_not_a_bullet_or_a_chip() -> None:
    for fragment in ("chips:[", "chips:", "bullets:", "bullets: [", '"text": "x"', "{", "}", "]", "[", "'a'", 'say it": yes', 'text":"x', "", "  ", ",", "..."):
        assert is_json_fragment(fragment), fragment
    for words in ("It is fast.", "Counter", "Adder with carry", "Note: it is a 12-bit counter", "Text: a point", "4 to 32 bits"):
        assert not is_json_fragment(words), words


def test_verilog_and_module_text_are_stripped() -> None:
    text = (
        "v2 folds the next value into the register. It is written as:\n```verilog\nmodule cnt8(input logic clk);\nendmodule\n```\n"
        "The module cnt8 has one port. It uses q <= q + 1'b1 each edge. Both pass 10 of 10 tests."
    )
    assert strip_verilog(text) == "v2 folds the next value into the register. It is written as: Both pass 10 of 10 tests."


async def test_an_invented_number_is_retried_once_then_replaced_by_the_code_answer(tmp_path, packs) -> None:
    bad = json.dumps({"text": "v3 is about 72.6 um2, roughly 3% smaller than v1.", "bullets": []})
    provider = Scripted({"ask_chat": [bad, bad]})
    manager, store, _ = await manager_for(tmp_path, packs, provider)
    try:
        reply = await manager.ask(RUN_ID, session_id=OWNER, text="how big is v3?")
    finally:
        await store.close()
    chats = provider.calls("ask_chat")
    assert len(chats) == 2 and "72.6, 3%" in chats[1].user and "not in the facts" in chats[1].user
    assert isinstance(reply, AskAnswer) and reply.templated
    assert reply.text.startswith("v3 (optimize of v1, round 1); passed 10 of 10 tests; area 72.62 um2 (+0.0% vs v1)")


async def test_a_retry_with_good_numbers_is_kept_and_verilog_never_reaches_the_user(tmp_path, packs) -> None:
    bad = json.dumps({"text": "v3 is 70 um2.", "bullets": []})
    good = json.dumps(
        {
            "text": "v3 measured 72.62 um2, the same as v1 (+0.0% vs v1). module cnt8 is the top.",
            "bullets": ["```verilog\nmodule cnt8; endmodule\n```", "It passed 10 of 10 tests."],
        }
    )
    provider = Scripted({"ask_chat": [bad, good]})
    manager, store, _ = await manager_for(tmp_path, packs, provider)
    try:
        reply = await manager.ask(RUN_ID, session_id=OWNER, text="how big is v3?", focus_version=None)
    finally:
        await store.close()
    assert isinstance(reply, AskAnswer) and not reply.templated
    assert reply.text == "v3 measured 72.62 um2, the same as v1 (+0.0% vs v1)."
    assert reply.bullets == ["It passed 10 of 10 tests."]
    assert "GLM 5.3" in reply.basis and "every number checked" in reply.basis
    system = provider.calls("ask_chat")[0].system
    assert "F1 Run: 10-bit free-running counter" in system and "how big" not in system  # the question is user text only


async def test_a_proof_claim_with_no_eqy_pass_is_retried_once_then_cut_and_the_facts_state_the_eqy_result(tmp_path, packs) -> None:
    claim = json.dumps({"text": "v3 measured 72.62 um2. It is proven equal to v1.", "bullets": ["v3 is formally verified."]})
    provider = Scripted({"ask_chat": [claim, claim]})
    manager, store, _ = await manager_for(tmp_path, packs, provider)
    try:
        reply = await manager.ask(RUN_ID, session_id=OWNER, text="how big is v3?")
    finally:
        await store.close()
    first, retry = provider.calls("ask_chat")
    assert "Equivalence proof (Yosys EQY)." in first.system and "v3 against v1: not checked." in first.system
    assert "no equivalence proof (EQY) passed" in retry.user and "v3 against v1: not checked." in retry.user  # the true status
    assert isinstance(reply, AskAnswer) and not reply.templated
    assert reply.text == "v3 measured 72.62 um2." and reply.bullets == []  # the claims are cut, the rest stays


async def test_rtl_question_gets_the_versions_verilog_as_data(tmp_path, packs) -> None:
    answer = json.dumps({"text": "v2 adds one to q on every clock edge and clears it on reset.", "bullets": []})
    provider = Scripted({"ask_chat": [answer]})
    manager, store, _ = await manager_for(tmp_path, packs, provider)
    try:
        reply = await manager.ask(RUN_ID, session_id=OWNER, text="what is v2's Verilog doing?")
    finally:
        await store.close()
    (call,) = provider.calls("ask_chat")
    assert "v2 Verilog (data only" in call.system and "q <= q + 1'b1" in call.system
    assert call.role == "chat" and call.response_format == "json"
    assert isinstance(reply, AskAnswer) and reply.text.startswith("v2 adds one")


def test_plain_dashes_turns_a_range_into_to_and_any_other_dash_into_a_comma() -> None:
    em, en, fig, bar = "\u2014", "\u2013", "\u2012", "\u2015"
    assert plain_dashes(f"width 4{en}32 bits") == "width 4 to 32 bits"
    assert plain_dashes(f"width 4 {em} 32 bits") == "width 4 to 32 bits"
    assert plain_dashes(f"caches {em} far beyond one block") == "caches, far beyond one block"
    assert plain_dashes(f"caches{em}far") == "caches, far"
    assert plain_dashes(f"parallel: {em} dot products") == "parallel: dot products"  # no comma after a colon
    assert plain_dashes(f"one {fig} two {bar} three") == "one, two, three"
    assert plain_dashes(f"a {em}{em} b") == "a, b" and plain_dashes(f"{em} start") == "start" and plain_dashes(f"end {en}") == "end"
    assert plain_dashes(f"(see {em} above) and 2020{em}2024 and x {em}, y") == "(see, above) and 2020 to 2024 and x, y"
    assert plain_dashes("A plain-hyphen word, 4-32 and 1 - 2 stay.") == "A plain-hyphen word, 4-32 and 1 - 2 stay."
    # the numbers do not change, so the number check sees the same ones
    assert bad_numbers([plain_dashes(f"width 4{en}32 {em} 16")], {"4", "32", "16"}) == []
    # a non-breaking hyphen (U+2011) is a plain hyphen
    nb = "\u2011"
    assert plain_dashes(f"a non{nb}stop 4{nb}32 chip") == "a non-stop 4-32 chip" and nb not in plain_dashes(f"{nb}x{nb}")
    assert plain_dashes(f"{nb}4{en}32{nb}") == "-4 to 32-"
    assert bad_numbers([plain_dashes(f"width 4{nb}32")], {"4", "32"}) == []


# ---------- intent ----------
def test_prefilter_decides_the_clear_cases_for_free() -> None:
    assert prefilter("why was this kept?", "cnt8").topic == "selection"  # type: ignore[union-attr]
    assert prefilter("what limits the clock?", "cnt8").topic == "timing"  # type: ignore[union-attr]
    assert prefilter("make it faster", "cnt8").intent == "change_request"  # type: ignore[union-attr]
    assert prefilter("ok try something that is actually faster", "cnt8").intent == "change_request"  # type: ignore[union-attr]
    assert prefilter("what is v2's Verilog doing?", "cnt8").intent == "rtl_question"  # type: ignore[union-attr]
    assert prefilter("design a 16-bit adder", "cnt8").intent == "new_chip"  # type: ignore[union-attr]
    # Plainly off topic is decided here, so a real router cannot answer a joke as small talk.
    for text in ("tell me a joke", "write me a poem about adders", "what is the weather today?", "give me a recipe for pasta", "tell me a story"):
        assert prefilter(text, "cnt8").intent == "off_topic", text  # type: ignore[union-attr]
    story = prefilter("what is the story behind v2's fail?", "cnt8")  # a run question that merely says "story"
    assert story is None or story.intent != "off_topic"


async def test_router_answer_is_checked_against_the_enums_and_the_real_versions(tmp_path, packs) -> None:
    settings = make_settings(tmp_path / "data")
    store = SqliteStore(settings)
    await store.open()
    try:
        card = await load_card(store, await seed(store))
    finally:
        await store.close()
    assert check_router_answer('{"intent": "delete_everything", "topic": "selection"}', card, []) is None
    routed = check_router_answer('{"intent": "run_question", "topic": "gossip", "version": "v9"}', card, [])
    assert routed is not None and routed.topic is None and routed.versions == ()
    routed = check_router_answer('{"intent": "small_talk", "topic": "selection", "version": "V2"}', card, [])
    assert routed is not None and routed.intent == "small_talk" and routed.topic is None and routed.versions == ("v2",)
    assert check_router_answer("not json at all", card, []) is None


async def test_router_off_topic_is_refused_and_junk_falls_back_to_a_checked_answer(tmp_path, packs) -> None:
    provider = Scripted(
        {
            "ask_router": ['{"intent": "off_topic", "topic": null, "version": null}', '{"intent": "hack", "topic": "x"}'],
            "ask_chat": [json.dumps({"text": "v1 was kept.", "bullets": []})],
        }
    )
    manager, store, _ = await manager_for(tmp_path, packs, provider)
    try:
        game = await manager.ask(RUN_ID, session_id=OWNER, text="who won the football game last night")
        other = await manager.ask(RUN_ID, session_id=OWNER, text="tell me about this design")
        joke = await manager.ask(RUN_ID, session_id=OWNER, text="tell me a joke")  # decided by keywords: no router call
    finally:
        await store.close()
    assert isinstance(game, AskRefused) and isinstance(joke, AskRefused)
    routers = provider.calls("ask_router")
    assert len(routers) == 2  # the game question and the design question; the joke never reached the router
    assert routers[0].role == "router" and routers[0].thinking is False and routers[0].response_format == "json"
    assert routers[0].model == "nvidia/Nemotron-3_5-Lightning" and "football" not in routers[0].system
    assert isinstance(other, AskAnswer) and other.text == "v1 was kept."  # unknown intent: a general run question


async def test_the_fake_provider_routes_a_build_request_for_another_chip_and_does_not_claim_glm(tmp_path, packs) -> None:
    manager, store, _ = await manager_for(tmp_path, packs)  # FakeProvider; the run is a counter
    try:
        uart = await manager.ask(RUN_ID, session_id=OWNER, text="build me a UART")
        adder = await manager.ask(RUN_ID, session_id=OWNER, text="design an adder")
        own = await manager.ask(RUN_ID, session_id=OWNER, text="why does v2 need so many cells?")  # a counter-run question
        same = await manager.ask(RUN_ID, session_id=OWNER, text="make a faster counter")  # the run's own chip: a change
    finally:
        await store.close()
    assert isinstance(uart, AskNewChip) and isinstance(adder, AskNewChip)
    assert not isinstance(own, AskNewChip) and isinstance(same, AskProposal)
    assert isinstance(own, AskAnswer) and "GLM" not in own.basis and "no model was called" in own.basis


# ---------- propose_run ----------
def test_apply_change_clamps_whole_numbers_and_refuses_other_bad_values() -> None:
    knobs = [Knob(id="width", kind="int", default=8, min=4, max=16), Knob(id="enable", kind="bool", default=True)]
    current = {"width": 10, "enable": False}
    assert apply_change(knobs, current, {"width": 12}, "12 bits") == ({"width": 12, "enable": False}, [])
    values, notes = apply_change(knobs, current, {"width": 40}, "make it 40 bits wide")  # the user said 40: the note names it
    assert values == {"width": 16, "enable": False} and notes == ["width 40 is not allowed (4 to 16), so it is 16."]
    values, notes = apply_change(knobs, current, {"width": 40}, "make it much wider")  # only the model said 40: the note does not
    assert values == {"width": 16, "enable": False} and notes == ["That width is not allowed (4 to 16), so it is 16."]
    values, notes = apply_change(knobs, current, {"enable": "maybe", "depth": 3}, "turn it on")
    assert values == current and "Ignored unknown settings: depth." in notes
    assert "Kept the current settings: enable must be true or false." in notes
    assert priority_from_words("make it smaller") == "area" and priority_from_words("lower power please") == "power"
    assert priority_from_words("again") is None


async def test_make_it_faster_proposes_a_validated_run_and_a_different_chip_is_new_chip(tmp_path, packs) -> None:
    provider = Scripted(
        {"ask_propose": ['{"family": "cnt8", "params": {}}', '{"family": "cnt8", "params": {"width": 99}}', '{"family": "add8", "params": {}}']}
    )
    manager, store, _ = await manager_for(tmp_path, packs, provider)
    try:
        faster = await manager.ask(RUN_ID, session_id=OWNER, text="make it faster")
        smaller = await manager.ask(RUN_ID, session_id=OWNER, text="make it smaller and 99 bits wide")
        adder = await manager.ask(RUN_ID, session_id=OWNER, text="try it as a different chip")
    finally:
        await store.close()
    assert isinstance(faster, AskProposal)
    p = faster.proposal
    assert (p.preset_id, p.params, p.priority, p.mode) == ("cnt8", {"width": 10, "enable": False}, "speed", "full")
    assert p.title == "10-bit free-running counter" and p.last_run_usd == 0.018 and p.cap_usd == 0.5 and p.idea is None
    assert faster.text.startswith("New run: 10-bit free-running counter, goal: faster.")
    (call,) = provider.calls("ask_propose")[:1]
    assert call.role == "parser" and "Current settings: width = 10, enable = false" in call.system and call.user == "make it faster"
    assert isinstance(smaller, AskProposal) and smaller.proposal.params == {"width": 16, "enable": False}
    assert smaller.proposal.priority == "area" and "width 99 is not allowed (4 to 16), so it is 16." in smaller.text
    assert smaller.proposal.title == "16-bit free-running counter"
    assert isinstance(adder, AskNewChip)


async def test_the_proposal_idea_is_the_technique_the_new_run_will_read(tmp_path, packs) -> None:
    manager, store, _ = await manager_for(tmp_path, packs, research=True)
    try:
        research = await manager.ask(RUN_ID, session_id=OWNER, text="what is known about fast counters? any papers?")
        proposal = await manager.ask(RUN_ID, session_id=OWNER, text="make it faster")
    finally:
        await store.close()
    assert isinstance(research, AskAnswer) and research.basis.startswith("From web research:")
    assert any(b.startswith("S1 ") for b in research.bullets)
    assert isinstance(proposal, AskProposal)
    # The research found both incrementers; for the goal "faster" the run reads the lookahead one first.
    assert "Known approaches: Ripple incrementer, Lookahead incrementer." in research.bullets
    assert proposal.proposal.idea == "Lookahead incrementer"
    assert proposal.text.startswith("New run: 10-bit free-running counter, goal: faster, idea: lookahead incrementer.")


def test_research_techniques_are_ranked_by_past_wins_then_the_run_goal() -> None:
    from verity.core.records import StrategyOutcomeRow
    from verity.research.service import rank_techniques

    ids = ["ripple_incrementer", "lookahead_incrementer"]
    assert rank_techniques(ids, [], "speed") == ["lookahead_incrementer", "ripple_incrementer"]
    assert rank_techniques(ids, [], "area") == ids
    won = StrategyOutcomeRow.model_construct(technique="ripple_incrementer", result="improved")
    assert rank_techniques(ids, [won], "speed") == ids  # our own measured win comes first


# ---------- thread state, money ----------
async def test_owner_only_and_never_while_running(tmp_path, packs) -> None:
    manager, store, _ = await manager_for(tmp_path, packs, Scripted())
    try:
        with pytest.raises(Forbidden):
            await manager.ask(RUN_ID, session_id="someone-else", text="why was this kept?")
    finally:
        await store.close()
    manager, store, _ = await manager_for(tmp_path / "b", packs, Scripted(), status="running")
    try:
        with pytest.raises(Conflict):
            await manager.ask(RUN_ID, session_id=OWNER, text="why was this kept?")
    finally:
        await store.close()


async def test_model_turns_reserve_and_record_spend_and_templates_stay_free(tmp_path, packs) -> None:
    settings = paid_settings(tmp_path)
    provider = Scripted({"ask_chat": [json.dumps({"text": "v3 measured 72.62 um2.", "bullets": []})]})
    manager, store, _ = await manager_for(tmp_path, packs, provider, settings=settings)
    try:
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        await manager.ask(RUN_ID, session_id=OWNER, text="why was this kept?")
        assert await store.daily_spend(day) == 0.0
        await manager.ask(RUN_ID, session_id=OWNER, text="how big is v3?")
        expected = (1000 * 1.40 + 200 * 4.40) / 1e6  # GLM 5.3 price, one call of 1000 in and 200 out
        assert abs(await store.daily_spend(day) - expected) < 1e-9
        assert manager._parse_holds == {}  # the hold was released
        worst = manager._ask._worst_usd(await load_card(store, await store.get_run(RUN_ID)), None, "how big is v3?")  # type: ignore[arg-type]
        assert 0.005 < worst < 0.05

        await store.add_daily_spend(day, settings.limits.daily_usd)  # the day is used up
        with pytest.raises(AdmissionDenied) as denied:
            await manager.ask(RUN_ID, session_id=OWNER, text="how big is v3?")
        assert denied.value.reason == "budget_exhausted"
        still = await manager.ask(RUN_ID, session_id=OWNER, text="why was this kept?")
        assert isinstance(still, AskAnswer) and still.templated
    finally:
        await store.close()


async def test_a_chat_answer_served_by_a_fallback_model_is_priced_and_named_as_that_model(tmp_path, packs) -> None:
    settings = paid_settings(tmp_path)
    provider = Scripted({"ask_chat": [json.dumps({"text": "v3 measured 72.62 um2.", "bullets": []})]})
    provider.served = "nvidia/Nemotron-3-Ultra-550b-a55b"
    manager, store, _ = await manager_for(tmp_path, packs, provider, settings=settings)
    try:
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        reply = await manager.ask(RUN_ID, session_id=OWNER, text="how big is v3?")
        assert abs(await store.daily_spend(day) - (1000 * 1.00 + 200 * 3.00) / 1e6) < 1e-9  # Ultra's price, not GLM 5.3's
    finally:
        await store.close()
    assert isinstance(reply, AskAnswer) and not reply.templated and "Worded by Nemotron 3 Ultra;" in reply.basis


async def test_a_model_turn_is_charged_to_its_session_and_refused_when_that_session_is_used_up(tmp_path, packs) -> None:
    settings = paid_settings(tmp_path, session_usd=1.0)
    provider = Scripted({"ask_chat": [json.dumps({"text": "v3 measured 72.62 um2.", "bullets": []})]})
    manager, store, _ = await manager_for(tmp_path, packs, provider, settings=settings)
    try:
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        await manager.ask(RUN_ID, session_id=OWNER, text="how big is v3?")
        expected = (1000 * 1.40 + 200 * 4.40) / 1e6  # one chat call of 1000 in and 200 out, at the GLM 5.3 price
        assert abs(await store.session_spend(OWNER, day) - expected) < 1e-9
        assert abs(await store.daily_spend(day) - expected) < 1e-9
        assert manager._parse_holds == {}  # the hold was released

        await store.add_daily_spend(day, 1.0, OWNER)  # this session has used its day; the global day has room
        with pytest.raises(AdmissionDenied) as denied:
            await manager.ask(RUN_ID, session_id=OWNER, text="how big is v3?")
        assert denied.value.reason == "session_budget_exhausted"
        still = await manager.ask(RUN_ID, session_id=OWNER, text="why was this kept?")  # a template turn costs nothing
        assert isinstance(still, AskAnswer) and still.templated
    finally:
        await store.close()


# ---------- the route ----------
def client_for(tmp_path, packs, provider=None, *, session_id: str | None = None, status: str | None = None):
    import asyncio

    settings = make_settings(tmp_path / "data")
    sid = session_id or anonymous_sid("testclient")

    async def prepare() -> None:
        store = SqliteStore(settings)
        await store.open()
        await seed(store, session_id=sid, status=status)
        await store.close()

    asyncio.run(prepare())
    store = SqliteStore(settings)
    manager = RunManager(settings, store, provider or Scripted(), FakeToolRunner(), packs)
    if status is not None:
        async def keep() -> None:  # startup recovery would end a seeded "running" run as "server restarted"
            return None

        manager._recover_orphans = keep  # type: ignore[method-assign]
    return TestClient(create_app(settings, manager, startup=[store.open], shutdown=[store.close]))


def test_route_answers_the_owner_and_rate_limits(tmp_path, packs) -> None:
    with client_for(tmp_path, packs) as client:
        assert "ask" in client.get("/health").json()["features"]
        first = client.post(f"/runs/{RUN_ID}/ask", json={"text": "why was this kept?", "focusVersion": "v2"})
        assert first.status_code == 200 and first.headers["cache-control"] == "no-store"
        body = first.json()
        assert body["kind"] == "answer" and body["templated"] is True and "basis" in body
        assert client.post(f"/runs/{RUN_ID}/ask", json={"text": "x" * 301}).status_code == 422
        assert client.post("/runs/r_nope/ask", json={"text": "why?"}).status_code == 404
        codes = [client.post(f"/runs/{RUN_ID}/ask", json={"text": "what limits the clock?"}).status_code for _ in range(6)]
        assert codes == [200] * 5 + [429]
        limited = client.post(f"/runs/{RUN_ID}/ask", json={"text": "what limits the clock?"})
        assert limited.json()["reason"] == "rate_limited" and int(limited.headers["retry-after"]) >= 1


def test_route_refuses_another_session_and_a_running_run(tmp_path, packs) -> None:
    with client_for(tmp_path / "a", packs, session_id="someone-else") as client:
        res = client.post(f"/runs/{RUN_ID}/ask", json={"text": "why was this kept?"})
        assert res.status_code == 403
    with client_for(tmp_path / "b", packs, status="running") as client:
        res = client.post(f"/runs/{RUN_ID}/ask", json={"text": "why was this kept?"})
        assert res.status_code == 409 and "still going" in res.json()["error"]


def test_route_proposal_and_new_chip_wire_shapes(tmp_path, packs) -> None:
    provider = Scripted({"ask_propose": ['{"family": "cnt8", "params": {"width": 12}}']})
    with client_for(tmp_path, packs, provider) as client:
        body = client.post(f"/runs/{RUN_ID}/ask", json={"text": "make it 12 bits and smaller"}).json()
        assert body["kind"] == "proposal" and set(body["proposal"]) == {
            "presetId", "params", "priority", "mode", "title", "idea", "lastRunUsd", "capUsd"
        }
        assert body["proposal"]["params"] == {"width": 12, "enable": False} and body["proposal"]["idea"] is None
        new_chip = client.post(f"/runs/{RUN_ID}/ask", json={"text": "design a 32-bit adder"}).json()
        assert new_chip == {"kind": "new_chip", "text": new_chip["text"]} and new_chip["text"]


def test_facts_carry_units_and_vs_v1(tmp_path, packs) -> None:
    import asyncio

    async def go():  # noqa: ANN202
        settings = make_settings(tmp_path / "data")
        store = SqliteStore(settings)
        await store.open()
        try:
            return build_facts(await load_card(store, await seed(store)), ["v2"], include_rtl=False)
        finally:
            await store.close()

    facts = asyncio.run(go())
    v2 = next(line for line in facts.lines if " v2 (simplify of v1" in line)
    assert "area 72.62 um2 (+0.0% vs v1)" in v2 and "clock period 0.389 ns (+0.0% vs v1)" in v2
    assert {"72.62", "0.389", "0.35", "0.039", "35", "10"} <= facts.allowed
