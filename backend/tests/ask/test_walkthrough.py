"""The walkthrough in POST /runs/{id}/ask ("walk me through it"): nine steps built by code from the run, no model call,
every number from run data. Uses the real run r_27fed084a749432ba42ca6981f3cc336 (cnt8, 10 bits, goal speed) plus the
spec rules and sign-off artifacts that the sandbox side emits (shapes from the shared contract)."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from test_ask import FIXTURE, OWNER, RUN_ID, Scripted, client_for, manager_for, seed
from verity.ask.intent import check_router_answer, prefilter
from verity.ask.tools import load_card
from verity.ask.walkthrough import build_walkthrough, lint_counts
from verity.contracts import WALK_STEP_ORDER, AskAnswer, parse_event
from verity.core.records import RunRecord
from verity.store import SqliteStore
from verity_test_support import make_settings

SHEET = {
    "candidateId": "c1",
    "tests": {
        "seedsRun": 9,
        "seedsPassed": 9,
        "perSeed": [{"seed": s, "passed": 10, "total": 10} for s in range(9)],
    },
    "lint": {"errors": 0, "warnings": 2, "byTag": {"WIDTHEXPAND": 1, "UNUSED": 1}, "latches": 0},
    "eqyVsV1": "v1_is_winner",
    "eqyVsHuman": "proven",
    "strength": {"planted": 20, "caught": 19, "harmless": 1, "undecided": 0, "status": "ok"},
    "durationMs": 61_000,
}
RULES = {"rules": ["q is a 10-bit register that wraps from 1023 to 0.", "The reset is synchronous and active low."]}


async def add_artifacts(store: SqliteStore, run_id: str, files: dict[str, object], first_seq: int = 107) -> None:
    for i, (path, body) in enumerate(files.items()):
        content = body if isinstance(body, str) else json.dumps(body)
        event = {"seq": first_seq + i, "tMs": 121_000 + i, "t": "artifact", "path": path, "content": content}
        await store.append_event(run_id, parse_event(event))


async def walk(tmp_path, packs, text="walk me through it", *, files=None, focus=None):
    provider = Scripted()  # any model call fails the test
    manager, store, run = await manager_for(tmp_path, packs, provider)
    try:
        if files:
            await add_artifacts(store, run.id, files)
        reply = await manager.ask(RUN_ID, session_id=OWNER, text=text, focus_version=focus)
    finally:
        await store.close()
    assert provider.requests == []
    return reply


# ---------- the nine steps ----------
async def test_walk_me_through_it_returns_nine_steps_from_the_run_without_a_model(tmp_path, packs) -> None:
    reply = await walk(tmp_path, packs)
    assert isinstance(reply, AskAnswer) and reply.templated and reply.bullets == []
    assert reply.text == "Here is how Verity built the 10-bit free-running counter, in 9 steps. The steps about one version are for v1, the kept version."
    assert "No model was called." in reply.basis
    assert reply.steps is not None and tuple(s.step_id for s in reply.steps) == WALK_STEP_ORDER
    by_id = {s.step_id: s for s in reply.steps}

    spec = by_id["spec"].text
    assert "A 10-bit free-running up counter." in spec and "q (output, 10 bits)" in spec and "10 locked tests" in spec
    plan = by_id["plan"].text
    assert "The plan has 2 blocks" in plan and "1. Next-state logic:" in plan and "2. Counter register:" in plan
    assert "not a wiring diagram" in plan and "latency of 1 cycle" in plan
    assert by_id["rtl"].text.startswith("Nemotron 3 Ultra wrote v1.") and "20 lines of Verilog" in by_id["rtl"].text
    assert by_id["lint"].text == "Verilator found 0 errors and 0 warnings in v1. No latches (no unintended memory)."
    assert by_id["simulate"].text.startswith("v1 passed 10 of 10 tests.")
    synth = by_id["synth"].text
    assert "area 72.62 um2" in synth and "35 cells" in synth and "clock period 0.389 ns" in synth and "Nangate45" in synth
    assert "The slowest path in v1" in synth
    improve = by_id["improve"].text
    assert "Round 1 made 3 versions from v1: v2 (simplify), v3 (optimize) and v4 (explore)." in improve
    assert "v1 was kept because no version beat it" in improve and "v4 (explore) had the same Verilog as v2" in improve
    equivalence = by_id["equivalence"].text
    assert "v1 is the reference" in equivalence and "against the human-written reference design: proven equal from reset" in equivalence
    assert "sign-off" in by_id["signoff"].text


async def test_node_ids_point_into_the_circuit_only_where_the_match_is_exact(tmp_path, packs) -> None:
    reply = await walk(tmp_path, packs)
    by_id = {s.step_id: s for s in reply.steps}  # type: ignore[union-attr]
    assert by_id["spec"].candidate_id == "c1" and by_id["spec"].node_ids == ["p-clk", "p-q", "p-rst_n"]  # the spec's ports, by name
    assert by_id["rtl"].candidate_id == "c1" and by_id["rtl"].node_ids == ["n2"]  # the circuit's own register node
    for step_id in ("plan", "lint", "simulate", "synth", "improve", "equivalence", "signoff"):
        assert by_id[step_id].node_ids is None  # plan lines are not wires, and checks are about the whole design
    assert by_id["plan"].candidate_id is None and by_id["improve"].candidate_id is None
    assert all(by_id[s].candidate_id == "c1" for s in ("lint", "simulate", "synth", "equivalence", "signoff"))


async def test_focus_version_picks_the_candidate_of_the_version_steps(tmp_path, packs) -> None:
    v2 = await walk(tmp_path / "a", packs, focus="v2")
    by_id = {s.step_id: s for s in v2.steps}  # type: ignore[union-attr]
    assert "for v2." in v2.text and "kept version" not in v2.text
    assert by_id["lint"].candidate_id == "c2" and by_id["lint"].text.startswith("Verilator found 0 errors and 0 warnings in v2.")
    assert by_id["rtl"].text.count("v2 was made from v1 in round 1 (simplify).") == 1 and by_id["rtl"].node_ids == ["n2"]
    assert by_id["spec"].candidate_id == "c2" and by_id["spec"].node_ids == ["p-clk", "p-q", "p-rst_n"]
    assert "area 72.62 um2 (+0.0% vs v1)" in by_id["synth"].text
    assert by_id["equivalence"].text.startswith("v2 against v1: not checked.")

    v4 = await walk(tmp_path / "b", packs, "explain the architecture of v4")  # the label in the text also picks it
    by_id = {s.step_id: s for s in v4.steps}  # type: ignore[union-attr]
    assert by_id["lint"].text.startswith("v4 was not linted. v4 (explore) had the same Verilog as v2")
    assert by_id["simulate"].text == "v4 was dropped before its tests, so no test ran on it."
    assert by_id["synth"].text == "v4 was not measured, so it has no area or clock numbers."
    assert by_id["spec"].node_ids is None and by_id["rtl"].node_ids is None  # v4 has no circuit
    assert "v4 (explore) had the same Verilog as v2" in by_id["improve"].text


@pytest.mark.parametrize(
    "text",
    [
        "walk me through it",
        "Walk me through this chip",
        "explain the architecture",
        "show the steps",
        "what are the steps?",
        "how was this built?",
        "tell me step by step how you built it",
        "give me a walkthrough",
    ],
)
def test_the_free_pre_filter_sends_walkthrough_requests_to_the_walkthrough(text: str) -> None:
    routed = prefilter(text, "cnt8")
    assert routed is not None and routed.intent == "walkthrough"


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("why was v4 dropped step by step", "run_question"),
        ("what limits the clock?", "run_question"),
        ("make it smaller", "change_request"),
        ("how much did this cost", "run_question"),
        ("what does the Verilog do", "rtl_question"),
    ],
)
def test_other_questions_keep_their_intent(text: str, intent: str) -> None:
    routed = prefilter(text, "cnt8")
    assert routed is not None and routed.intent == intent


async def test_the_router_may_also_choose_the_walkthrough_and_it_stays_free(tmp_path, packs) -> None:
    provider = Scripted({"ask_router": ['{"intent": "walkthrough", "topic": null, "version": "v3"}']})
    manager, store, _ = await manager_for(tmp_path, packs, provider)
    try:
        assert prefilter("can you take me through how this came together", "cnt8") is None
        reply = await manager.ask(RUN_ID, session_id=OWNER, text="can you take me through how this came together")
    finally:
        await store.close()
    assert [r.tags.get("purpose") for r in provider.requests] == ["ask_router"]  # only the router, no chat or other call
    assert isinstance(reply, AskAnswer) and reply.steps is not None and "for v3." in reply.text
    card_check = check_router_answer('{"intent": "walkthrough"}', await _card(tmp_path / "x"), [])
    assert card_check is not None and card_check.intent == "walkthrough"


async def _card(tmp_path):
    settings = make_settings(tmp_path / "data")
    store = SqliteStore(settings)
    await store.open()
    try:
        return await load_card(store, await seed(store))
    finally:
        await store.close()


async def test_a_version_the_run_does_not_have_is_said_plainly(tmp_path, packs) -> None:
    reply = await walk(tmp_path, packs, "walk me through v9")
    assert isinstance(reply, AskAnswer) and reply.steps is None
    assert reply.text == "This run has no version v9. It made v1, v2, v3 and v4."


# ---------- the spec rules and the sign-off sheet ----------
async def test_the_spec_rules_and_the_signoff_sheet_are_read_from_their_artifacts(tmp_path, packs) -> None:
    files = {"spec/rules.json": RULES, "signoff/c1.json": SHEET}
    reply = await walk(tmp_path, packs, files=files)
    by_id = {s.step_id: s for s in reply.steps}  # type: ignore[union-attr]
    assert "Rules it must follow: q is a 10-bit register that wraps from 1023 to 0. The reset is synchronous and active low." in by_id["spec"].text
    assert by_id["lint"].text == (
        "Verilator found 0 errors and 2 warnings in v1. Warnings by tag: UNUSED 1, WIDTHEXPAND 1. No latches (no unintended memory)."
    )
    assert "The sign-off ran the same tests on 9 seeds in total, and 9 passed every test." in by_id["simulate"].text
    assert by_id["equivalence"].text.startswith("v1 is the reference every other version is compared with")
    assert "against the human-written reference design: proven equal from reset" in by_id["equivalence"].text
    assert by_id["signoff"].candidate_id == "c1"
    assert by_id["signoff"].text == (
        "Tests: every test passed on 9 of 9 seeds. Lint: 0 errors, 2 warnings, no latches. Equal to v1: it is the reference. "
        "Equal to the human design: proven equal from reset. The tests caught 19 of 20 planted bugs; 1 proven harmless."
    )


async def test_signoff_wording_for_each_proof_and_strength_state(tmp_path, packs) -> None:
    sheet = {
        **SHEET,
        "eqyVsV1": "inconclusive",
        "eqyVsHuman": "not_equivalent",
        "lint": {"errors": 1, "warnings": 0, "byTag": {}, "latches": 2},
        "strength": {"planted": 20, "caught": 17, "harmless": 1, "undecided": 2, "status": "ok"},
    }
    reply = await walk(tmp_path / "a", packs, files={"signoff/c1.json": sheet})
    text = {s.step_id: s.text for s in reply.steps}["signoff"]  # type: ignore[union-attr]
    assert "Lint: 1 error, 0 warnings, 2 latches." in text
    assert "Equal to v1: inconclusive: the tool neither found a difference nor finished the proof." in text
    assert "Equal to the human design: not equal: the tool found a difference." in text
    assert "The tests caught 17 of 20 planted bugs; 1 proven harmless; 2 not decided." in text
    skipped = {**SHEET, "strength": {"planted": 0, "caught": 0, "harmless": 0, "undecided": 0, "status": "skipped", "note": "Out of time."}}
    failed = {**SHEET, "strength": {"planted": 0, "caught": 0, "harmless": 0, "undecided": 0, "status": "failed"}}
    first = await walk(tmp_path / "b", packs, files={"signoff/c1.json": skipped})
    second = await walk(tmp_path / "c", packs, files={"signoff/c1.json": failed})
    assert "The test-strength check was skipped. Out of time." in {s.step_id: s.text for s in first.steps}["signoff"]  # type: ignore[union-attr]
    assert "The test-strength check did not finish." in {s.step_id: s.text for s in second.steps}["signoff"]  # type: ignore[union-attr]


async def test_a_sheet_for_another_version_is_named_as_the_kept_versions(tmp_path, packs) -> None:
    reply = await walk(tmp_path, packs, focus="v2", files={"signoff/c1.json": SHEET})
    step = {s.step_id: s for s in reply.steps}["signoff"]  # type: ignore[union-attr]
    assert step.candidate_id == "c1" and step.text.startswith("Sign-off covers the kept version, v1, not v2. Tests: every test passed on 9 of 9 seeds.")


async def test_unreadable_artifacts_never_break_the_walkthrough(tmp_path, packs) -> None:
    junk = {
        "spec/rules.json": "not json",
        "signoff/c1.json": {"tests": "x", "lint": [], "eqyVsV1": [], "eqyVsHuman": {}, "strength": "no"},
        "circuit/c1.json": "{broken",
        "plan/plan.r9.json": "[]",
    }
    reply = await walk(tmp_path, packs, files=junk)
    by_id = {s.step_id: s for s in reply.steps}  # type: ignore[union-attr]
    assert tuple(by_id) == WALK_STEP_ORDER
    assert by_id["signoff"].text == "The sign-off sheet exists but holds no readable checks."
    assert "Rules it must follow" not in by_id["spec"].text
    assert by_id["spec"].node_ids is None  # the circuit would not parse, so no node is named


async def test_signoff_not_there_says_still_running_then_not_made(tmp_path) -> None:
    card = await _card(tmp_path)
    recent = card.run.model_copy(update={"ended_at": datetime.now(UTC) - timedelta(minutes=2)})
    card.run = recent
    text = {s.step_id: s.text for s in build_walkthrough(card, None).steps or []}["signoff"]
    assert text == "The sign-off for v1 is still running. It starts after the result shows, so ask again in a minute."
    card.run = recent.model_copy(update={"ended_at": datetime.now(UTC) - timedelta(days=2)})
    text = {s.step_id: s.text for s in build_walkthrough(card, None).steps or []}["signoff"]
    assert text == "There is no sign-off sheet for v1: this run was made before sign-off existed, or its checks did not finish."


# ---------- lint from the run's own log, when there is no sheet ----------
def test_lint_counts_read_a_verilator_log() -> None:
    log = (
        "Verilator: 2 warnings\n%Warning-WIDTHEXPAND: dut.v:7:12: Operator ASSIGN expects 8 bits\n"
        "%Warning-UNUSED: dut.v:3:1: Signal is not used: 'x'\n%Error: Exiting due to 0 error(s)\n"
    )
    assert lint_counts(log) == (0, 2, {"WIDTHEXPAND": 1, "UNUSED": 1})
    failed = "%Error-PROCASSWIRE: dut.v:9:9: Procedural assignment to wire\n%Error: Exiting due to 1 error(s)\n"
    assert lint_counts(failed) == (1, 0, {})
    assert lint_counts("Verilator: 3 warnings") == (0, 3, {})  # a capped log still gives the count
    assert lint_counts(None) == (0, 0, {})


async def test_the_lint_step_reads_the_version_log_without_a_sheet(tmp_path) -> None:
    card = await _card(tmp_path)
    v1 = card.versions[0]
    assert v1.verification is not None
    v1.verification = v1.verification.model_copy(
        update={"lint_log": "Verilator: 2 warnings\n%Warning-WIDTHEXPAND: a\n%Warning-LATCH: Latch inferred for signal 'x'\n"}
    )
    lint = {s.step_id: s.text for s in build_walkthrough(card, None).steps or []}["lint"]
    assert lint == (
        "Verilator found 0 errors and 2 warnings in v1. Warnings by tag: LATCH 1, WIDTHEXPAND 1. 1 latch found: a latch is memory the code did not ask for."
    )


# ---------- a run that ended early, and the wire ----------
async def test_a_run_with_no_events_still_answers_every_step_honestly(tmp_path) -> None:
    settings = make_settings(tmp_path / "data")
    store = SqliteStore(settings)
    await store.open()
    try:
        data = json.loads((FIXTURE / "run.json").read_text(encoding="utf-8"))
        data.update(session_id=OWNER, status="error", winner_id=None)
        run = RunRecord.model_validate(data)
        await store.create_run(run)
        reply = build_walkthrough(await load_card(store, run), None)
    finally:
        await store.close()
    by_id = {s.step_id: s for s in reply.steps or []}
    assert tuple(by_id) == WALK_STEP_ORDER and reply.text.endswith("This run made no version.")
    assert by_id["spec"].text == "This run ended before it read the spec."
    assert by_id["plan"].text == "This run ended before it made a plan."
    assert by_id["rtl"].text == "This run ended before it wrote any Verilog."
    assert by_id["lint"].text == "This run made no version to check."
    assert all(s.candidate_id is None and s.node_ids is None for s in by_id.values())


async def test_no_step_carries_a_dash_or_runs_long(tmp_path, packs) -> None:
    reply = await walk(tmp_path, packs, files={"spec/rules.json": RULES, "signoff/c1.json": SHEET})
    for step in reply.steps or []:  # type: ignore[union-attr]
        assert chr(0x2014) not in step.text and chr(0x2013) not in step.text and 0 < len(step.text) <= 900


def test_route_sends_the_steps_in_camel_case_and_older_answers_have_none(tmp_path, packs) -> None:
    with client_for(tmp_path, packs) as client:
        body = client.post(f"/runs/{RUN_ID}/ask", json={"text": "walk me through it", "focusVersion": "v1"}).json()
        assert body["kind"] == "answer" and body["templated"] is True and body["bullets"] == []
        assert [s["stepId"] for s in body["steps"]] == list(WALK_STEP_ORDER)
        spec = body["steps"][0]
        assert set(spec) == {"stepId", "title", "text", "candidateId", "nodeIds"}
        assert spec["candidateId"] == "c1" and spec["nodeIds"] == ["p-clk", "p-q", "p-rst_n"]
        assert set(body["steps"][1]) == {"stepId", "title", "text"}  # no candidate and no nodes: the keys are left out
        plain = client.post(f"/runs/{RUN_ID}/ask", json={"text": "why was this kept?"}).json()
        assert "steps" not in plain
