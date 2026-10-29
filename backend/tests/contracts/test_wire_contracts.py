"""The pydantic contracts must produce exactly the JSON the web zod schemas accept (CONTRACTS rule 4).

The strongest check available without node is the recorded fixture: frontend/public/runs/golden.json parses into the Python
models and serializes back to the same JSON. Optional TS fields (`x?: T`) must be omitted when None, nullable ones
(`x: T | null`) must be sent as null.
"""

import json

import pytest

from verity.contracts import (
    PPA,
    CaptionEvent,
    Candidate,
    CircuitGraph,
    CreditsEvent,
    Equivalence,
    ErrorResponse,
    HealthResponse,
    OpEvent,
    Port,
    RunLog,
    RunPhaseEvent,
    StageEvent,
    TestCase,
    Verification,
    camel,
    event_to_json,
    event_to_wire,
    parse_event_json,
    to_json,
    to_wire,
)
from verity.contracts.events import AddNodeOp, UpdateStatusOp
from verity_test_support import GOLDEN_JSON


def test_camel_drops_the_keyword_escape() -> None:
    assert camel("pass_") == "pass"
    assert camel("t_ms") == "tMs"
    assert camel("area_um2") == "areaUm2"
    assert camel("auto_continue_ms") == "autoContinueMs"


def test_testcase_uses_the_pass_key() -> None:
    wire = to_wire(TestCase(name="n", inputs="i", expected="e", got="g", pass_=False))
    assert wire == {"name": "n", "inputs": "i", "expected": "e", "got": "g", "pass": False}  # cycle omitted


def test_optional_fields_are_omitted_nullable_fields_are_null() -> None:
    assert "count" not in to_wire(Port(name="clk", dir="in", width=1))
    assert to_wire(Port(name="a", dir="in", width=8, count=4))["count"] == 4

    verification = Verification(lint="pass", passed=1, total=1, tool="t", duration_ms=3)
    wire = to_wire(verification)
    assert "lintLog" not in wire and "diagnosis" not in wire
    assert wire["durationMs"] == 3

    ppa = to_wire(PPA(pdk="Nangate45"))
    assert ppa["areaUm2"] is None and ppa["powerMw"] is None  # nullable in zod: present as null

    caption = event_to_wire(CaptionEvent(text="hi"))
    assert "chapter" not in caption
    credits = event_to_wire(CreditsEvent(used_usd=0.1))
    assert credits["capUsd"] is None  # nullable in zod


def test_candidate_nullable_and_optional_fields() -> None:
    candidate = Candidate(id="c1", label="v1", round=0, created_by="INIT", reason="r", rtl="", status="drafting")
    wire = to_wire(candidate)
    for nullable in ("parentId", "verification", "ppa", "fitness"):
        assert wire[nullable] is None
    assert "eqy" not in wire  # optional in zod
    with_eqy = to_wire(candidate.model_copy(update={"eqy": Equivalence(status="proven", against_id="c1")}))
    assert with_eqy["eqy"] == {"status": "proven", "againstId": "c1", "tool": "yosys-eqy"}


def test_events_are_single_line_json_and_round_trip() -> None:
    events = [
        RunPhaseEvent(phase="running", seq=1, t_ms=0),
        StageEvent(stage="evolve", status="active", detail="R1 of 2", round=1, seq=2, t_ms=5),
        OpEvent(by="ai", op=AddNodeOp(id="spec", kind="spec", zone="plan", label="Spec", ref_id="spec"), seq=3, t_ms=6),
        OpEvent(by="system", op=UpdateStatusOp(id="golden", status="locked"), seq=4, t_ms=7),
        CaptionEvent(text="Done.", chapter="Done", seq=5, t_ms=8),
    ]
    for event in events:
        raw = event_to_json(event)
        assert "\n" not in raw
        parsed = parse_event_json(raw)
        assert type(parsed) is type(event)
        assert event_to_wire(parsed) == event_to_wire(event)


def test_api_bodies_use_camel_case() -> None:
    health = to_wire(HealthResponse(live_enabled=True, cap_usd=0.5, provider="fake", models={"planner": "m"}))
    assert set(health) == {"liveEnabled", "capUsd", "provider", "models", "authRequired", "features"}  # reason omitted when None
    assert to_wire(ErrorResponse(error="x")) == {"error": "x"}
    assert to_wire(ErrorResponse(error="x", reason="busy")) == {"error": "x", "reason": "busy"}


def test_circuit_graph_defaults_serialize() -> None:
    wire = to_wire(CircuitGraph(top="mac4"))
    assert wire["format"] == "verity.circuit/1"
    assert wire["candidateId"] == ""
    assert wire["truncated"] is False


@pytest.mark.skipif(not GOLDEN_JSON.is_file(), reason="frontend/public/runs/golden.json not found")
def test_golden_fixture_round_trips_through_the_python_models() -> None:
    """golden.json -> RunLog -> wire dict must equal the original (key names, omitted optionals, nulls)."""
    original = json.loads(GOLDEN_JSON.read_text(encoding="utf-8"))
    log = RunLog.model_validate(original)
    assert to_wire(log) == original
    assert json.loads(to_json(log)) == original
