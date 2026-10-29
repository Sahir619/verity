"""What the thread agent remembers: the last 10 turns, the 20 to 40 word summary of older ones, and the facts record that
code (never the model) writes from the fact check, a proposal and POST /runs."""

import json


from test_agent import QUESTION, ME, TID, Harness, ScriptedLLM, answer, h, make_harness, tool  # noqa: F401 (h is a fixture)
from test_ask import paid_settings
from verity.ask import memory
from verity.contracts import AgentProposal, ParseClosest, ParseInRange, ParseOutOfRange, Research, ResearchSentence
from verity.core.errors import LLMError
from verity.core.parse import live_families
from verity.core.records import ThreadTurnIn
from verity.llm.fake import FakeProvider
from verity.runs.manager import RunManager
from verity.store import SqliteStore
from verity_test_support import FakeToolRunner, make_settings, tmp_cassettes


def summary(text: str) -> str:
    return json.dumps({"summary": text})


async def preload(h: Harness, turns: int, user_words: int = 1) -> None:
    """A checked thread that already has `turns` stored turns: 'marker-01' and its reply, 'marker-02' ... Each user message
    is `user_words` words long."""
    await h.checked()
    for n in range(1, turns + 1):
        await h.store.append_turns(
            ME,
            TID,
            [
                ThreadTurnIn(role="user", text=" ".join([f"marker-{n:02d}"] + ["word"] * (user_words - 1))),
                ThreadTurnIn(role="verity", text=f"reply-{n:02d}", steps=[{"tool": "list_blocks", "label": "Read the build list", "ok": True}]),
            ],
        )


# ---------- the recent turns and the summary ----------
async def test_older_turns_are_folded_four_at_a_time_when_fourteen_are_waiting(tmp_path, packs) -> None:
    note = "The user is exploring small adders and wants a fast one."
    provider = ScriptedLLM(agent=[answer("Plain words.")], memory=[summary(note)])
    h = await make_harness(tmp_path, packs, provider)
    try:
        await h.checked()
        for n in range(1, 14):
            await h.say(f"marker-{n:02d}")
        assert provider.calls("memory") == []  # thirteen turns fit: nothing is folded yet
        thirteen = await h.thread()
        assert thirteen is not None and thirteen.summary == "" and thirteen.summarized_through == 0 and len(thirteen.turns) == 26
        last = provider.calls("agent")[-1].user  # the 13th message saw the twelve before it, all verbatim
        assert [f"User: marker-{n:02d}" in last for n in range(1, 13)] == [True] * 12

        await h.say("marker-14")  # fourteen are waiting now: the oldest four are folded, ten stay
        (call,) = provider.calls("memory")
        assert call.role == "memory" and call.response_format == "json" and call.tags == {"purpose": "memory"}
        settings = h.service._settings
        assert call.model == "zai-org/GLM-5.3" == settings.model_for("memory")
        assert (call.temperature, call.reasoning_effort, call.max_output_tokens, call.timeout_s) == (0.0, "low", 800, 25.0)
        assert "20 to 40 words" in call.system and "<previous_summary>\nnone\n</previous_summary>" in call.user
        assert "User: marker-01" in call.user and "User: marker-04" in call.user and "Verity: Plain words." in call.user
        assert "marker-05" not in call.user
        fourteen = await h.thread()
        assert fourteen is not None and fourteen.summary == note
        assert fourteen.summarized_through == 8 and len(fourteen.turns) == 28  # four turns folded; every row is still stored
        assert len((await h.store.load(ME, TID, since_summary=True)).turns) == 20  # type: ignore[union-attr]

        await h.say("marker-15")  # the agent reads the summary and ten turns
        fifteenth = provider.calls("agent")[-1].user
        assert note in fifteenth.split("<earlier_turns>")[0]
        assert "marker-04" not in fifteenth  # turn 4 is only in the summary now
        assert [f"User: marker-{n:02d}" in fifteenth for n in range(5, 15)] == [True] * 10
        assert fifteenth.count("\nUser: marker-") == 10

        await h.say("marker-16")
        await h.say("marker-17")
        assert len(provider.calls("memory")) == 1  # no fold on the messages between: one every four turns
        await h.say("marker-18")  # the agent reads thirteen turns, the most it ever shows
        eighteenth = provider.calls("agent")[-1].user
        assert [f"User: marker-{n:02d}" in eighteenth for n in range(5, 18)] == [True] * 13
        assert eighteenth.count("\nUser: marker-") == 13
        assert len(provider.calls("memory")) == 2 and note in provider.calls("memory")[1].user  # the next four, with the summary so far
        assert (await h.thread()).summarized_through == 16  # type: ignore[union-attr]
    finally:
        await h.store.close()


async def test_earlier_replies_show_the_steps_they_took(tmp_path, packs) -> None:
    provider = ScriptedLLM(agent=[answer("Plain words.")])
    h = await make_harness(tmp_path, packs, provider)
    try:
        await preload(h, 2)
        await h.say("next")
        user = provider.calls("agent")[0].user
        assert "Verity: reply-01 [steps: Read the build list]" in user
    finally:
        await h.store.close()


async def test_the_summary_is_cut_to_40_words_and_stripped_of_verilog(tmp_path, packs) -> None:
    long = " ".join(f"word{n}" for n in range(1, 61))
    raw = summary(f"The user wants a fast adder. module top(input a); endmodule {long} ```verilog\nassign y = a;\n``` <b>done</b>")
    h = await make_harness(tmp_path, packs, ScriptedLLM(agent=[answer("Plain words.")], memory=[raw]))
    try:
        await preload(h, 13)
        await h.say("the 14th")
        stored = (await h.thread()).summary  # type: ignore[union-attr]
        words = stored.split()
        assert 0 < len(words) <= 40 and "module" not in stored and "assign" not in stored and "```" not in stored and "<" not in stored
        assert words[:5] == ["The", "user", "wants", "a", "fast"]
    finally:
        await h.store.close()


async def test_the_summary_has_no_em_dash_or_en_dash(tmp_path, packs) -> None:
    em, en = "\u2014", "\u2013"
    note = f"The user wants an adder, width 4{en}32 bits {em} fast, for a small GPU {em} one block first."
    h = await make_harness(tmp_path, packs, ScriptedLLM(agent=[answer("Plain words.")], memory=[summary(note)]))
    try:
        await preload(h, 13)
        await h.say("the 14th")
        stored = (await h.thread()).summary  # type: ignore[union-attr]
        assert em not in stored and en not in stored
        assert stored == "The user wants an adder, width 4 to 32 bits, fast, for a small GPU, one block first."
    finally:
        await h.store.close()


async def test_a_plain_text_summary_is_accepted_and_an_unusable_one_leaves_the_turns_for_next_time(tmp_path, packs) -> None:
    provider = ScriptedLLM(agent=[answer("Plain words.")], memory=["Talked about adders and speed."])
    h = await make_harness(tmp_path, packs, provider)
    try:
        await preload(h, 13)
        await h.say("the 14th")
        assert (await h.thread()).summary == "Talked about adders and speed."  # type: ignore[union-attr]
    finally:
        await h.store.close()

    for bad in (LLMError("down", retryable=False), summary("   "), "{not json"):
        provider = ScriptedLLM(agent=[answer("Plain words.")], memory=[bad])
        h = await make_harness(tmp_path / f"bad-{type(bad).__name__}-{len(str(bad))}", packs, provider)
        try:
            await preload(h, 13)
            await h.say("the 14th")
            kept = await h.thread()
            assert kept is not None and kept.summary == "" and kept.summarized_through == 0, bad
            assert (await h.say("the 15th")).reply is not None  # the reply never depends on the summary
            provider.answers["memory"] = [summary("Now it worked.")]
            await h.say("the 16th")
            folded = await h.thread()
            # sixteen turns waited: the next fold took everything above ten, six turns
            assert folded is not None and folded.summary == "Now it worked." and folded.summarized_through == 12
        finally:
            await h.store.close()


async def test_a_fold_after_a_long_failure_takes_at_most_eight_turns(tmp_path, packs) -> None:
    provider = ScriptedLLM(agent=[answer("Plain words.")], memory=[summary("Now it worked.")])
    h = await make_harness(tmp_path, packs, provider)
    try:
        await preload(h, 24)  # no fold ever ran
        await h.say("the 25th")
        assert (await h.thread()).summarized_through == 16  # type: ignore[union-attr]  # eight turns, not fifteen
        assert (await h.store.load(ME, TID, since_summary=True)).turns[0].text == "marker-09"  # type: ignore[union-attr]
        # the agent reads at most thirteen turns, even while more wait
        seen = provider.calls("agent")[0].user
        assert seen.count("\nUser: marker-") == 13 and "marker-11" not in seen and "marker-12" in seen
    finally:
        await h.store.close()


async def test_a_summary_under_twenty_words_is_asked_for_once_more_when_the_turns_held_more(tmp_path, packs) -> None:
    fine = " ".join(["fine"] * 25)
    provider = ScriptedLLM(agent=[answer("Plain words.")], memory=[summary("Too short."), summary(fine)])
    h = await make_harness(tmp_path, packs, provider)
    try:
        await preload(h, 13, user_words=8)  # the four turns to fold hold 32 words of the user's text
        await h.say("the 14th")
        first, second = provider.calls("memory")
        assert "<note>" not in first.user and "Your last summary was too short" in second.user
        assert (await h.thread()).summary == fine  # type: ignore[union-attr]
    finally:
        await h.store.close()
    # a second short answer is accepted as it is
    provider = ScriptedLLM(agent=[answer("Plain words.")], memory=[summary("Too short."), summary("Still short.")])
    h = await make_harness(tmp_path / "again", packs, provider)
    try:
        await preload(h, 13, user_words=8)
        await h.say("the 14th")
        assert len(provider.calls("memory")) == 2 and (await h.thread()).summary == "Still short."  # type: ignore[union-attr]
    finally:
        await h.store.close()
    # a short summary of a few words of user text is fine: one call
    provider = ScriptedLLM(agent=[answer("Plain words.")], memory=[summary("Too short.")])
    h = await make_harness(tmp_path / "few", packs, provider)
    try:
        await preload(h, 13)  # one word per user message: four words to fold
        await h.say("the 14th")
        assert len(provider.calls("memory")) == 1 and (await h.thread()).summary == "Too short."  # type: ignore[union-attr]
    finally:
        await h.store.close()


async def test_the_summary_call_is_paid_for_like_the_chat_calls(tmp_path, packs) -> None:
    settings = paid_settings(tmp_path)
    h = await make_harness(tmp_path, packs, ScriptedLLM(agent=[answer("Plain words.")], memory=[summary("A short note.")]), settings=settings)
    try:
        await preload(h, 13)
        await h.say("the 14th")
        assert len(h.costs) == 2 and all(c > 0 and s == ME for c, s in h.costs)  # one agent call, one summary call
        (worst, _), = h.holds
        memory_call = h.service._call_usd("memory", 1)
        assert worst > memory_call  # the hold counted the summary call
        await h.say("the 15th")  # no fold starts on this message: its hold has no summary call in it
        assert h.holds[1][0] < worst
    finally:
        await h.store.close()


async def test_numbers_in_the_summary_are_not_facts(tmp_path, packs) -> None:
    provider = ScriptedLLM(agent=[answer("Plain words.")], memory=[summary("The user once asked for 4242 lanes.")])
    h = await make_harness(tmp_path, packs, provider)
    try:
        await preload(h, 13)
        await h.say("the 14th")
        provider.answers["agent"] = [answer("You wanted 4242 lanes."), answer("You asked about lanes.")]
        reply = (await h.say("what did I ask?")).reply
        assert reply.text == "You asked about lanes."  # type: ignore[union-attr]
        assert "4242" in provider.calls("agent")[-2].user  # the model reads the summary ...
    finally:
        await h.store.close()


# ---------- the facts record, written by code ----------
def research_with_id(research_id: str) -> Research:
    return Research(
        id=research_id, status="ok", reused=False, created_at="2026-10-23T00:00:00Z", queries=["q"],
        sentences=[ResearchSentence(text="Adders trade area for speed.", source_ids=[1])], sources=[], techniques=[],
        done_before=True, past_runs=0, usd=0.0, serper_credits=0,
    )


async def test_the_facts_record_comes_from_the_check(tmp_path, packs) -> None:
    check = ParseInRange(
        family="add8", params={"width": 16}, verdict="buildable_now", intent_class="in_family", stated_knobs=["width"], research=research_with_id("rs_abc")
    )
    h = await make_harness(tmp_path, packs, ScriptedLLM(agent=[answer("A note.")]), parse_reply=check)
    try:
        assert (await h.thread()) is None
        out = await h.say("a 16-bit adder")
        assert out.check == check
        facts = (await h.thread()).facts  # type: ignore[union-attr]
        assert facts == {
            "checked": True, "verdict": "buildable_now", "intent_class": "in_family", "family": "add8", "knobs": {"width": 16},
            "stated": ["width"], "research_id": "rs_abc", "run_ids": [],
        }
        system = h.provider.calls("agent")[0].system
        assert "family: add8" in system and "settings: width 16" in system and "you_stated: width" in system
        assert "research: saved for this block" in system
        await h.say("and then?")  # checked: the next message runs no fact check
        assert h.parsed == ["a 16-bit adder"]
        assert "<fact_check>" not in h.provider.calls("agent")[1].system and "<mode>discussion_no_run</mode>" in h.provider.calls("agent")[1].system
    finally:
        await h.store.close()


async def test_a_check_with_only_a_closest_chip_records_that_chip(tmp_path, packs) -> None:
    check = ParseOutOfRange(
        reason="A GPU is thousands of cores.", closest=ParseClosest(family="mac4", params={"lanes": 8, "width": 8, "cycles": 32}),
        verdict="not_buildable_here", intent_class="too_big", stated_knobs=[],
    )
    h = await make_harness(tmp_path, packs, ScriptedLLM(agent=[answer("A note.")]), parse_reply=check)
    try:
        await h.say("I need a GPU")
        facts = (await h.thread()).facts  # type: ignore[union-attr]
        assert facts["checked"] is True and facts["verdict"] == "not_buildable_here" and facts["family"] == "mac4"
        assert facts["knobs"] == {"lanes": 8, "width": 8, "cycles": 32} and facts["research_id"] is None
        assert "reason: A GPU is thousands of cores." in h.provider.calls("agent")[0].system
    finally:
        await h.store.close()


async def test_a_question_or_a_not_a_chip_leaves_the_thread_unchecked(tmp_path, packs) -> None:
    h = await make_harness(tmp_path, packs, ScriptedLLM(agent=[answer("A plain reply.")]), parse_reply=QUESTION)
    try:
        out = await h.say("what can you build?")
        assert out.check is None and out.reply is not None  # a question: no check on the wire, the agent answers
        facts = (await h.thread()).facts  # type: ignore[union-attr]
        assert not facts.get("checked") and facts.get("verdict") is None
        await h.say("and what is a counter?")
        assert len(h.parsed) == 2  # still no fact check done: every message asks the parser again
    finally:
        await h.store.close()
    food = ParseOutOfRange(reason="Food.", closest=None, verdict="not_a_chip", intent_class="not_chip")
    h = await make_harness(tmp_path / "food", packs, ScriptedLLM(agent=[answer("Verity only designs chips.")]), parse_reply=food)
    try:
        out = await h.say("I want ice cream")
        assert out.check is None and out.reply is not None and len(h.provider.calls("agent")) == 1  # the agent answers, no dead end
        facts = (await h.thread()).facts  # type: ignore[union-attr]
        assert not facts.get("checked") and facts.get("verdict") is None
    finally:
        await h.store.close()


async def test_a_proposal_is_written_into_the_facts_and_read_back_next_message(h) -> None:
    await h.checked(family="add8", knobs={"width": 16}, verdict="buildable_now")
    h.provider.answers["agent"] = [tool("propose_run", family="add8", knobs={"width": 20}, priority="speed"), answer("Faster, then.")]
    reply = (await h.say("make it 20-bit and faster")).reply
    assert isinstance(reply, AgentProposal)
    facts = (await h.thread()).facts  # type: ignore[union-attr]
    assert facts["proposal"]["knobs"] == {"width": 20} and facts["knobs"] == {"width": 16}  # the check's own settings stay
    h.provider.answers["agent"] = [answer("Waiting for your Run.")]
    await h.say("ok")
    assert "pending_proposal: add8 width 20, goal faster (waits for Run)" in h.provider.calls("agent")[-1].system


def test_the_block_list_names_each_block_without_a_size(packs) -> None:
    lines = memory.buildable_lines(packs, live_families(packs))
    assert [ln for ln in lines if ln.startswith("- ")] == [
        "- adder with carry (id for propose_run: add8)",
        "- counter with enable (id for propose_run: cnt8)",
        "- sequence detector (id for propose_run: fsm_seq)",
        "- MAC array (id for propose_run: mac4)",
    ]
    # the settings, ranges and defaults follow each name: the sizes are there
    assert lines[:2] == ["- adder with carry (id for propose_run: add8)", "    width: whole number 4 to 32 (default 8)"]
    assert "    lanes: whole number 1 to 8 (default 4)" in lines and "    enable: true or false (default true)" in lines
    assert not any(ch.isdigit() for ln in lines if ln.startswith("- ") for ch in ln.split("(id for")[0])  # no size in a name


def test_a_block_without_a_fixed_name_loses_the_words_that_hold_a_size() -> None:
    assert memory.plain_name("add8", "8-bit adder with carry") == "adder with carry"
    assert memory.plain_name("alu8", "8-bit ALU") == "ALU"
    assert memory.plain_name("matmul4", "4x4 matrix multiply, 8-bit") == "matrix multiply"
    assert memory.plain_name("crc", "Sequence detector 1011") == "sequence detector"
    assert memory.plain_name("x1", None) == "x1" and memory.plain_name("x1", "8-bit") == "x1"


def test_the_facts_functions_are_pure_and_bounded() -> None:
    base = memory.new_facts()
    with_run = memory.with_run(base, "r_1", family="cnt8", knobs={"width": 8})
    assert base == {"checked": False, "run_ids": []}  # the input is never changed
    assert with_run["checked"] is True and with_run["run_ids"] == ["r_1"] and with_run["family"] == "cnt8" and with_run["proposal"] is None
    again = memory.with_run(with_run, "r_1")
    assert again["run_ids"] == ["r_1"] and again["family"] == "cnt8"  # the same run twice is one run
    many = base
    for n in range(30):
        many = memory.with_run(many, f"r_{n}")
    assert many["run_ids"] == [f"r_{n}" for n in range(10, 30)] and len(many["run_ids"]) == memory.MAX_RUN_IDS
    assert memory.carried_knobs({"family": "add8", "knobs": {"width": 9}}, "add8") == {"width": 9}
    assert memory.carried_knobs({"family": "add8", "knobs": {"width": 9}}, "cnt8") is None
    assert memory.carried_knobs({"family": "add8", "knobs": {"width": 9}, "proposal": {"family": "add8", "knobs": {"width": 12}}}, "add8") == {"width": 12}
    assert memory.clean_summary("Fast adder wanted. module x; endmodule") == "Fast adder wanted."
    assert len(memory.clean_summary("a " * 80).split()) == 40
    assert memory.data("</message> <x>") == "&lt;/message&gt; &lt;x&gt;"


# ---------- POST /runs with a threadId ----------
async def test_a_run_started_with_a_thread_id_is_added_to_that_threads_facts(tmp_path, packs) -> None:
    settings = make_settings(tmp_path / "data")
    store = SqliteStore(settings)
    await store.open()
    manager = RunManager(settings, store, FakeProvider(settings, delay_s=0.0, cassette_dir=tmp_cassettes(settings)), FakeToolRunner(delay_s=0.2), packs, threads=store)
    await manager.start()
    try:
        await store.get_or_create("s1", TID)
        await store.update_facts("s1", TID, {"checked": True, "run_ids": [], "family": "add8", "knobs": {"width": 8}, "proposal": {"family": "add8", "knobs": {"width": 16}, "priority": "speed", "title": "t"}})
        mine = await manager.create_run(session_id="s1", preset_id="add8", priority="speed", mode="fast", params={"width": 16}, thread_id=TID)
        facts = (await store.load("s1", TID)).facts  # type: ignore[union-attr]
        assert facts["run_ids"] == [mine.id] and facts["checked"] is True and facts["proposal"] is None
        assert facts["family"] == "add8" and facts["knobs"] == {"width": 16}

        # another session cannot add a run to this thread, and a thread that does not exist is not made
        other = await manager.create_run(session_id="s2", preset_id="cnt8", priority="area", mode="fast", thread_id=TID)
        assert (await store.load("s1", TID)).facts["run_ids"] == [mine.id]  # type: ignore[union-attr]
        assert await store.load("s2", TID) is None and other.id != mine.id
        await manager.stop(other.id, "s2")
        none_yet = await store.load("s3", TID)
        assert none_yet is None
    finally:
        await manager.shutdown()
        await store.close()


async def test_a_thread_with_a_run_describes_it_and_lets_the_agent_talk_about_it(h) -> None:
    from test_ask import RUN_ID, seed

    run = await seed(h.store, session_id=ME)
    await h.service.attach_run(TID, ME, run)  # no such thread yet: nothing happens
    assert await h.thread() is None
    out = await h.service.message(TID, session_id=ME, text="how did it go?", run_id=RUN_ID)
    assert out.check is None and h.parsed == []  # a run means a fact check already happened
    facts = (await h.thread()).facts  # type: ignore[union-attr]
    assert facts["checked"] is True and facts["run_ids"] == [RUN_ID] and facts["family"] == "cnt8"
    system = h.provider.calls("agent")[0].system
    assert "<mode>discussion_after_run</mode>" in system and f"latest_run: {RUN_ID}" in system
    assert f"- {RUN_ID}: cnt8 " in system and "status done" in system and "<run>\nF1 Run:" in system


async def test_sending_the_run_id_with_every_message_keeps_a_pending_proposal(h) -> None:
    from test_ask import RUN_ID, seed

    await seed(h.store, session_id=ME)
    h.provider.answers["agent"] = [tool("propose_run", family="cnt8", knobs={"width": 12}, priority="speed"), answer("A faster counter.")]
    await h.say("how did it go?", run_id=RUN_ID)
    h.provider.answers["agent"] = [tool("propose_run", family="cnt8", knobs={"width": 12}, priority="speed"), answer("A faster counter.")]
    await h.say("make it faster", run_id=RUN_ID)
    assert (await h.thread()).facts["proposal"]["knobs"] == {"width": 12, "enable": False}  # type: ignore[union-attr]
    h.provider.answers["agent"] = [answer("Still waiting for Run.")]
    await h.say("ok", run_id=RUN_ID)  # the same run id again: it changes nothing
    facts = (await h.thread()).facts  # type: ignore[union-attr]
    assert facts["run_ids"] == [RUN_ID] and facts["proposal"]["knobs"] == {"width": 12, "enable": False}
    assert "pending_proposal: cnt8 enable false, width 12, goal faster (waits for Run)" in h.provider.calls("agent")[-1].system


# ---------- a failed CPU job: the next step is named by code, and the prompt forbids inventing a control ----------
def _cpu_job_view(*, status="failed", block_status="passed", upgrade_id=None, parent=None, retry_of=None):
    from verity.contracts.cpu import CpuBlockView, CpuJobView
    from verity.runs.cpu_job import BLOCKS

    blocks = [CpuBlockView(block_id=p, pack_id=p, title=t, status=block_status) for p, t in BLOCKS]
    return CpuJobView(
        id="cpu_a", goal="speed", status=status, created_at="2026-10-28T00:00:00+00:00", blocks=blocks,
        upgrade_id=upgrade_id, parent_job_id=parent, retry_of=retry_of,
    )


RETRY_NEXT = (
    "- next: retry only its core with propose_cpu_retry, job_id cpu_a (reuses the 7 passed blocks); the user presses Confirm on the card"
)
AGAIN_NEXT = "- next: try again as a new CPU job with propose_cpu_job; the user presses Confirm on the card"
UPGRADE_NEXT = (
    "- next: try this upgrade again with propose_cpu_upgrade, job_id cpu_p, upgrade_id pipeline_2; the user presses Confirm on the card"
)


def test_a_failed_job_with_seven_passed_blocks_names_the_core_retry_tool():
    text = memory.cpu_jobs_block([_cpu_job_view()])
    assert RETRY_NEXT in text and AGAIN_NEXT not in text


def test_any_other_failed_job_names_a_new_cpu_job():
    one_failed = _cpu_job_view()
    one_failed.blocks[3].status = "failed"
    for job in (one_failed, _cpu_job_view(block_status="failed")):
        text = memory.cpu_jobs_block([job])
        assert AGAIN_NEXT in text and RETRY_NEXT not in text and UPGRADE_NEXT not in text
    # A failed upgrade is tried again from its parent's board, which still offers the upgrade.
    text = memory.cpu_jobs_block([_cpu_job_view(upgrade_id="pipeline_2", parent="cpu_p")])
    assert UPGRADE_NEXT in text and AGAIN_NEXT not in text and RETRY_NEXT not in text
    # A job that has not failed gets neither line.
    running = memory.cpu_jobs_block([_cpu_job_view(status="running")])
    assert "propose_cpu_retry" not in running and "propose_cpu_job" not in running


def test_a_retry_job_says_it_is_a_core_retry_of_the_failed_job():
    text = memory.cpu_jobs_block([_cpu_job_view(status="running", retry_of="cpu_failed")])
    assert "CPU job cpu_a: " in text and ", core retry of the failed CPU job cpu_failed;" in text


def test_the_prompt_forbids_naming_a_control_the_facts_do_not_name():
    from verity.ask import prompt

    # The persona's own controls (Run, Approve, New chip, the CPU card) stay nameable: the rule counts these instructions too.
    rule = "12. Name only the buttons, cards and controls named in these instructions, in <verity_facts> or in a tool result. Never invent a button"
    assert rule in prompt.PERSONA
    assert rule in prompt.system_prompt("discussion_no_run", "<verity_facts>x</verity_facts>")
