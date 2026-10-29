"""The engineer's walkthrough: "walk me through it", "explain the architecture", "show the steps" (POST /runs/{id}/ask).

Built by code from the run card, with no model call. Nine steps in a fixed order (verity.contracts.WALK_STEP_ORDER):

    spec         the locked spec: ports, tests, the pack's rules (artifact spec/rules.json)
    plan         the plan's blocks, as the ordered list they are (event plan, artifact plan/plan.rN.json)
    rtl          the first drafts of v1 and the one kept (file candidates/c1/samples.json), or how a later version was made
    lint         Verilator errors and warnings by tag
    simulate     the locked tests, and the extra seeds when a sign-off sheet exists
    synth        cells, area and clock against v1, and the slowest path
    improve      the versions tried, and why each was kept or dropped
    equivalence  the EQY proof against v1 and against the human reference
    signoff      the sign-off sheet (artifact signoff/<candidateId>.json), or that it is not ready

Every number is read from the run: events, artifacts and files. A step about one version uses the version in focus
(the label the user named or is looking at), else the kept version. `nodeIds` point into that version's circuit
artifact only where the match is exact: the spec's ports by name, and the registers the circuit itself lists.
"""

import json
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from ..contracts import WALK_STEP_ORDER, AskAnswer, AskStep, Candidate, CircuitGraph
from . import tools
from .tools import RunCard

MAX_STEP_CHARS = 900
_RECENT_S = 15 * 60  # a run that ended less than this long ago may still be running its sign-off checks

BASIS = (
    "From this run's saved events, files and artifacts: spec, plan, drafts, tests, reports, proofs and sign-off. "
    "No model was called."
)

TITLES: dict[str, str] = {
    "spec": "What the chip must do",
    "plan": "The plan",
    "rtl": "Writing the Verilog",
    "lint": "Lint check",
    "simulate": "Tests",
    "synth": "Size and speed",
    "improve": "Trying better versions",
    "equivalence": "Proof it matches",
    "signoff": "Sign-off",
}

_WARNING_TAG = re.compile(r"^%Warning-([A-Z0-9_]+):", re.MULTILINE)
_ERROR_LINE = re.compile(r"^%Error(?:-([A-Z0-9_]+))?:\s*(.*)$", re.MULTILINE)
_SUMMARY_WARNINGS = re.compile(r"Verilator:\s*(\d+)\s+warning")


# ======================================================================
# small readers (artifacts are untrusted JSON: every field is checked)
# ======================================================================
def _json(card: RunCard, path: str) -> dict[str, Any] | None:
    raw = card.artifacts.get(path)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _int(data: Any, key: str) -> int | None:
    value = data.get(key) if isinstance(data, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _circuit(card: RunCard, cid: str) -> CircuitGraph | None:
    raw = card.artifacts.get(f"circuit/{cid}.json")
    if not raw:
        return None
    try:
        return CircuitGraph.model_validate_json(raw)
    except ValueError:
        return None


def _latest_plan_doc(card: RunCard) -> dict[str, Any] | None:
    paths = [p for p in card.artifacts if re.fullmatch(r"plan/plan\.r\d+\.json", p)]
    if not paths:
        return None
    return _json(card, max(paths, key=lambda p: int(re.findall(r"\d+", p)[-1])))


def _signoff(card: RunCard, cid: str) -> dict[str, Any] | None:
    return _json(card, f"signoff/{cid}.json")


def _rules(card: RunCard) -> list[str]:
    data = _json(card, "spec/rules.json")
    rules = data.get("rules") if data else None
    return [r.strip() for r in rules if isinstance(r, str) and r.strip()] if isinstance(rules, list) else []


def _trim(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 3]
    cut = cut.rsplit(" ", 1)[0] if " " in cut else cut
    return cut.rstrip(" ,;:.") + "..."


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}{'es' if word.endswith(('x', 's', 'ch', 'sh')) else 's'}"


def _sentence(text: str) -> str:
    text = text.strip()
    return text if not text or text[-1] in ".!?" else text + "."


def _step(step_id: str, text: str, candidate: Candidate | None = None, node_ids: list[str] | None = None) -> AskStep:
    return AskStep(
        step_id=step_id,  # type: ignore[arg-type]
        title=TITLES[step_id],
        text=_trim(text, MAX_STEP_CHARS),
        candidate_id=candidate.id if candidate is not None else None,
        node_ids=node_ids or None,
    )


# ======================================================================
# the nine steps
# ======================================================================
def _spec_step(card: RunCard, focus: Candidate | None) -> AskStep:
    spec = card.spec
    if spec is None:
        return _step("spec", "This run ended before it read the spec.")
    ports = ", ".join(
        f"{p.name} ({'input' if p.dir == 'in' else 'output'}, {_plural(p.width, 'bit')})" for p in spec.ports
    )
    parts = [
        _sentence(spec.text),
        f"Its ports are {ports}." if ports else "",
        f"{_plural(spec.test_count, 'locked test')} from the golden model check every version, and the agent cannot edit them.",
    ]
    rules = _rules(card)
    if rules:
        shown = [_trim(_sentence(r), 140) for r in rules[:4]]
        more = f" and {len(rules) - 4} more" if len(rules) > 4 else ""
        parts.append(f"Rules it must follow: {' '.join(shown)}{more}")
    node_ids: list[str] = []
    if focus is not None:
        graph = _circuit(card, focus.id)
        if graph is not None:
            names = {p.name for p in spec.ports}
            node_ids = [n.id for n in graph.nodes if n.kind == "port" and n.label in names]  # exact port names only
    return _step("spec", " ".join(p for p in parts if p), focus if node_ids else None, node_ids)


def _plan_step(card: RunCard) -> AskStep:
    blocks = card.plan_blocks
    if not blocks:
        return _step("plan", "This run ended before it made a plan.")
    listed = " ".join(_sentence(f"{i}. {b.name}: {_trim(b.reason, 120)}") for i, b in enumerate(blocks, start=1))
    parts = [
        f"The plan has {_plural(len(blocks), 'block')}, in order. {listed}",
        "The blocks are sections of one Verilog module, in the order the plan lists them. The order is not a wiring "
        "diagram.",
    ]
    doc = _latest_plan_doc(card)
    latency = _int(doc, "latencyCycles")
    if latency is not None:
        parts.append(f"The plan targets a latency of {_plural(latency, 'cycle')}.")
    if card.plan_revision > 1:
        parts.append(f"It was revised {_plural(card.plan_revision - 1, 'time')} before the build.")
    return _step("plan", " ".join(parts))


def _model_name(card: RunCard, role: str) -> str:
    model = card.run.models.get(role)
    return tools.model_name(model) if model else "The model"


def _rtl_step(card: RunCard, focus: Candidate | None) -> AskStep:
    base = card.baseline
    if base is None or not base.rtl:
        return _step("rtl", "This run ended before it wrote any Verilog.")
    writer = _model_name(card, "rtl_gen")
    samples = card.samples.get("samples") if card.samples else None
    chosen = card.samples.get("chosen") if card.samples else None
    parts: list[str] = []
    if isinstance(samples, list) and samples and isinstance(chosen, int) and 0 <= chosen < len(samples):
        n = len(samples)
        lint_ok = sum(1 for s in samples if isinstance(s, dict) and s.get("lintOk") is True)
        pick = samples[chosen] if isinstance(samples[chosen], dict) else {}
        passed, total = _int(pick, "passed"), _int(pick, "total")
        parts.append(f"{writer} wrote {_plural(n, 'first draft')} of {base.label} at the same time, and {lint_ok} of {n} passed lint.")
        if passed is not None and total is not None:
            parts.append(f"The draft that passed the most tests ({passed} of {total}) became {base.label}.")
    else:
        fixes = f", after {_plural(base.fix_count, 'fix')}" if base.fix_count else ""
        parts.append(f"{writer} wrote {base.label}{fixes}.")
    shown = focus or base
    if shown.id != base.id:
        parent = card.by_id(shown.parent_id)
        origin = f" from {parent.label}" if parent is not None else ""
        parts.append(f"{shown.label} was made{origin} in round {shown.round} ({tools.strategy(shown)}).")
        if shown.reason:
            parts.append(f"Why it was written: {_trim(_sentence(shown.reason), 260)}")
    if shown.rtl:
        parts.append(f"{shown.label} is {_plural(len(shown.rtl.strip().splitlines()), 'line')} of Verilog.")
    node_ids: list[str] = []
    graph = _circuit(card, shown.id)
    if graph is not None:
        node_ids = [n.id for n in graph.nodes if n.kind == "reg"]  # the registers its circuit lists
        if node_ids:
            parts.append(f"Its circuit keeps state in {_plural(graph.stats.regs, 'register')}.")
    return _step("rtl", " ".join(parts), shown if node_ids else None, node_ids)


def lint_counts(log: str | None) -> tuple[int, int, dict[str, int]]:
    """(errors, warnings, warnings by tag) read from a Verilator log."""
    text = log or ""
    by_tag = Counter(m.group(1) for m in _WARNING_TAG.finditer(text))
    errors = sum(1 for m in _ERROR_LINE.finditer(text) if not m.group(2).startswith("Exiting due to"))
    summary = _SUMMARY_WARNINGS.search(text)
    warnings = max(sum(by_tag.values()), int(summary.group(1)) if summary else 0)
    return errors, warnings, dict(by_tag)


def _tags_text(by_tag: dict[str, int]) -> str:
    return ", ".join(f"{tag} {n}" for tag, n in sorted(by_tag.items(), key=lambda kv: (-kv[1], kv[0])))


def _lint_step(card: RunCard, focus: Candidate | None) -> AskStep:
    if focus is None:
        return _step("lint", "This run made no version to check.")
    sheet = _signoff(card, focus.id)
    lint = sheet.get("lint") if sheet else None
    if isinstance(lint, dict) and _int(lint, "errors") is not None and _int(lint, "warnings") is not None:
        errors, warnings = _int(lint, "errors") or 0, _int(lint, "warnings") or 0
        raw_tags = lint.get("byTag")
        by_tag = {k: v for k, v in raw_tags.items() if isinstance(k, str) and isinstance(v, int)} if isinstance(raw_tags, dict) else {}
        latches = _int(lint, "latches")
        source = "Sign-off lint"
    elif focus.verification is not None and (focus.verification.lint_log or focus.verification.lint == "fail"):
        errors, warnings, by_tag = lint_counts(focus.verification.lint_log)
        if focus.verification.lint == "fail":
            errors = max(errors, 1)
        latches = by_tag.get("LATCH", 0)
        source = "Lint"
    elif focus.verification is not None:
        errors, warnings, by_tag, latches, source = 0, 0, {}, 0, "Lint"
    else:
        reason = tools.drop_reason(card, focus)
        return _step("lint", f"{focus.label} was not linted. {reason or ''}".strip(), focus)
    text = f"Verilator found {_plural(errors, 'error')} and {_plural(warnings, 'warning')} in {focus.label}."
    if by_tag:
        text += f" Warnings by tag: {_tags_text(by_tag)}."
    if latches is not None:
        text += " No latches (no unintended memory)." if latches == 0 else f" {_plural(latches, 'latch')} found: a latch is memory the code did not ask for."
    if focus.fix_count:
        text += f" It took {_plural(focus.fix_count, 'fix')} to get here."
    return _step("lint", text, focus)


def _simulate_step(card: RunCard, focus: Candidate | None) -> AskStep:
    if focus is None:
        return _step("simulate", "This run made no version to test.")
    tests = tools.tests_text(focus)
    if tests is None:
        return _step("simulate", f"{focus.label} was dropped before its tests, so no test ran on it.", focus)
    parts = [f"{focus.label} {tests}. The tests are locked: they come from the golden model and the agent cannot edit them."]
    v = focus.verification
    if v is not None and v.failures:
        first = v.failures[0]
        where = f" at cycle {first.cycle}" if first.cycle is not None else ""
        parts.append(_sentence(f"The first failure was {first.name}{where}: expected {_trim(first.expected, 60)}, got {_trim(first.got, 60)}"))
    sheet = _signoff(card, focus.id)
    seeds = sheet.get("tests") if sheet else None
    run_n, ok_n = _int(seeds, "seedsRun"), _int(seeds, "seedsPassed")
    if run_n is not None and ok_n is not None:
        parts.append(f"The sign-off ran the same tests on {_plural(run_n, 'seed')} in total, and {ok_n} passed every test.")
    return _step("simulate", " ".join(parts), focus)


def _synth_step(card: RunCard, focus: Candidate | None) -> AskStep:
    if focus is None:
        return _step("synth", "This run made no version to measure.")
    if focus.ppa is None:
        return _step("synth", f"{focus.label} was not measured, so it has no area or clock numbers.", focus)
    base = card.baseline
    parts = [f"{focus.label} measured {tools.measures_text(focus, base)}."]
    parts.append(f"Measured with {focus.ppa.pdk} (a post-synthesis estimate, no wires). Every change is against {base.label if base else 'v1'}.")
    path = tools.critical_path(card, focus)
    if path is not None:
        parts.append(tools.critical_path_text(path)[0])
    return _step("synth", " ".join(parts), focus)


def _improve_step(card: RunCard, focus: Candidate | None) -> AskStep:
    versions = card.versions
    base = card.baseline
    if len(versions) <= 1:
        return _step("improve", "This run made only one version, so there was nothing to compare.")
    children = [c for c in versions if base is None or c.id != base.id]
    parts: list[str] = []
    for round_n in sorted({c.round for c in children if c.round > 0}):
        made = [tools.named(c) for c in children if c.round == round_n]
        parents = [p.label for p in (card.by_id(pid) for pid in card.rounds.get(round_n, [])) if p is not None]
        origin = f" from {tools.join_words(parents)}" if parents else ""
        parts.append(f"Round {round_n} made {_plural(len(made), 'version')}{origin}: {tools.join_words(made)}.")
    headline, _ = tools.explain_selection(card)
    parts.append(headline)
    if focus is not None and (base is None or focus.id != base.id):
        reason = tools.drop_reason(card, focus)
        if reason:
            parts.append(reason)
    else:
        dropped = [r for r in (tools.drop_reason(card, c) for c in versions) if r]
        parts += dropped[:3]
    parts.append(tools.round2_text(card))
    return _step("improve", " ".join(parts))


_EQY_WORDS = {
    "proven": "proven equal from reset",
    "inconclusive": "inconclusive: the tool neither found a difference nor finished the proof",
    "not_equivalent": "not equal: the tool found a difference",
    "not_run": "not checked",
}


def _eqy(value: Any) -> str | None:
    """A proof status from an artifact: one of the known words, else None (the artifact is untrusted JSON)."""
    return value if isinstance(value, str) and value in _EQY_WORDS else None


_EQY_EXPLAINED = "The proof (EQY) starts both designs from reset and checks they give the same outputs."


def _eqy_status(card: RunCard, c: Candidate) -> tuple[str | None, str | None]:
    """(against v1, against the human reference) of one version, each a key of _EQY_WORDS or None when it does not apply:
    v1 is not compared with itself, and only the kept version (or a version with a sign-off sheet) has a human result. The
    sign-off sheet's word comes first, then the version's own EQY record, then "not run"."""
    base = card.baseline
    sheet = _signoff(card, c.id)
    vs_v1 = None
    if base is None or c.id != base.id:
        vs_v1 = (_eqy(sheet.get("eqyVsV1")) if sheet else None) or (c.eqy.status if c.eqy is not None else "not_run")
    human = _eqy(sheet.get("eqyVsHuman")) if sheet else None
    if human is None and card.human_eqy is not None and card.winner is not None and c.id == card.winner.id:
        human = card.human_eqy.status
    return vs_v1, human


def _eqy_sentences(card: RunCard, focus: Candidate, *, explained: bool) -> list[str]:
    """What the equivalence proof found for one version, in the words of _EQY_WORDS."""
    base = card.baseline
    ref = base.label if base is not None else "v1"
    vs_v1, human = _eqy_status(card, focus)
    parts: list[str] = []
    if vs_v1 is None:
        parts.append(f"{focus.label} is the reference every other version is compared with, so it is not compared with itself.")
    else:
        parts.append(f"{focus.label} against {ref}: {_EQY_WORDS[vs_v1]}." + (f" {_EQY_EXPLAINED}" if explained else ""))
    if human is not None:
        parts.append(f"{focus.label} against the human-written reference design: {_EQY_WORDS[human]}.")
    return parts


def eqy_facts(card: RunCard) -> str:
    """The run fact for the equivalence proof of every version, in the walkthrough's words ("" when the run made no
    version). Passing tests are simulation, not this proof."""
    if not card.versions:
        return ""
    parts = [s for c in card.versions for s in _eqy_sentences(card, c, explained=False)]
    return f"Equivalence proof (Yosys EQY). {_EQY_EXPLAINED} Passing tests are simulation, not this proof. " + " ".join(parts)


def eqy_passes(card: RunCard) -> frozenset[str]:
    """What the run has proven: "v1" when some version is proven equal to v1, "human" when a version is proven equal to the
    human-written reference design. Only a pass makes "proven" true in an answer, and a human pass backs only a sentence
    about the human design."""
    found: set[str] = set()
    for c in card.versions:
        vs_v1, human = _eqy_status(card, c)
        if vs_v1 == "proven":
            found.add("v1")
        if human == "proven":
            found.add("human")
    return frozenset(found)


def _equivalence_step(card: RunCard, focus: Candidate | None) -> AskStep:
    if focus is None:
        return _step("equivalence", "This run made no version to compare.")
    return _step("equivalence", " ".join(_eqy_sentences(card, focus, explained=True)), focus)


def _strength_text(strength: dict[str, Any]) -> str | None:
    status = strength.get("status")
    note = strength.get("note")
    tail = f" {_sentence(_trim(note, 120))}" if isinstance(note, str) and note.strip() else ""
    if status == "skipped":
        return "The test-strength check was skipped." + tail
    if status == "failed":
        return "The test-strength check did not finish." + tail
    planted, caught = _int(strength, "planted"), _int(strength, "caught")
    if status != "ok" or planted is None or caught is None:
        return None
    text = f"The tests caught {caught} of {_plural(planted, 'planted bug')}"
    harmless, undecided = _int(strength, "harmless"), _int(strength, "undecided")
    if harmless:
        text += f"; {harmless} proven harmless"
    if undecided:
        text += f"; {undecided} not decided"
    return text + "."


def _signoff_step(card: RunCard, focus: Candidate | None) -> AskStep:
    winner = card.winner
    shown = focus if focus is not None and _signoff(card, focus.id) else None
    note = ""
    if shown is None and winner is not None and _signoff(card, winner.id):
        shown = winner
        if focus is not None and focus.id != winner.id:
            note = f"Sign-off covers the kept version, {winner.label}, not {focus.label}. "
    sheet = _signoff(card, shown.id) if shown is not None else None
    if shown is None or sheet is None:
        target = winner or focus
        who = target.label if target is not None else "this run"
        ended = card.run.ended_at
        recent = ended is None or (datetime.now(UTC) - (ended if ended.tzinfo else ended.replace(tzinfo=UTC))).total_seconds() < _RECENT_S
        if recent:
            text = f"The sign-off for {who} is still running. It starts after the result shows, so ask again in a minute."
        else:
            text = f"There is no sign-off sheet for {who}: this run was made before sign-off existed, or its checks did not finish."
        return _step("signoff", text, target)
    parts: list[str] = []
    tests = sheet.get("tests")
    run_n, ok_n = _int(tests, "seedsRun"), _int(tests, "seedsPassed")
    if run_n is not None and ok_n is not None:
        parts.append(f"Tests: every test passed on {ok_n} of {_plural(run_n, 'seed')}.")
    lint = sheet.get("lint")
    if isinstance(lint, dict) and _int(lint, "errors") is not None and _int(lint, "warnings") is not None:
        latches = _int(lint, "latches")
        latch_text = "" if latches is None else (", no latches" if latches == 0 else f", {_plural(latches, 'latch')}")
        parts.append(f"Lint: {_plural(_int(lint, 'errors') or 0, 'error')}, {_plural(_int(lint, 'warnings') or 0, 'warning')}{latch_text}.")
    base = card.baseline
    vs_v1, vs_human = sheet.get("eqyVsV1"), _eqy(sheet.get("eqyVsHuman"))
    if vs_v1 == "v1_is_winner":
        parts.append(f"Equal to {base.label if base else 'v1'}: it is the reference.")
    elif _eqy(vs_v1) is not None:
        parts.append(f"Equal to {base.label if base else 'v1'}: {_EQY_WORDS[vs_v1]}.")
    if vs_human is not None:
        parts.append(f"Equal to the human design: {_EQY_WORDS[vs_human]}.")
    strength = sheet.get("strength")
    if isinstance(strength, dict):
        text = _strength_text(strength)
        if text:
            parts.append(text)
    if not parts:
        parts.append("The sign-off sheet exists but holds no readable checks.")
    return _step("signoff", note + " ".join(parts), shown)


# ======================================================================
# the walkthrough
# ======================================================================
def build_walkthrough(card: RunCard, focus_label: str | None) -> AskAnswer:
    """Nine steps built from the run. `focus_label` (like "v4") picks the version the version steps talk about."""
    focus = card.by_label(focus_label) or card.winner or card.baseline
    steps = [
        _spec_step(card, focus),
        _plan_step(card),
        _rtl_step(card, focus),
        _lint_step(card, focus),
        _simulate_step(card, focus),
        _synth_step(card, focus),
        _improve_step(card, focus),
        _equivalence_step(card, focus),
        _signoff_step(card, focus),
    ]
    assert tuple(s.step_id for s in steps) == WALK_STEP_ORDER
    who = (
        f"The steps about one version are for {focus.label}" + (", the kept version" if card.winner is not None and focus.id == card.winner.id else "")
        if focus is not None
        else "This run made no version"
    )
    return AskAnswer(
        text=f"Here is how Verity built the {card.title}, in {len(steps)} steps. {who}.",
        bullets=[],
        basis=BASIS,
        templated=True,
        steps=steps,
    )


__all__ = ["BASIS", "TITLES", "build_walkthrough", "eqy_facts", "eqy_passes", "lint_counts"]
