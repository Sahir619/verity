"""Code tools behind POST /runs/{id}/ask (plan claim 3). The model calls no tool: code runs these and writes every number.

    load_card(store, run)          the run card: the stored events (latest version of each candidate, captions, rounds,
                                   winner, the spec, the plan, report, circuit and sign-off artifacts) plus the run row
                                   and the run's files
    explain_selection(card)        replays the ranking of verity.core.fitness (higher score, then smaller area, then the
                                   older version), the dedupe drops that exist only as captions, and whether round 2 ran
    version_detail(card, label)    one version: how it was made, tests, measurements vs v1, why it was dropped
    critical_path(card, label)     the slowest path from that version's OpenSTA report
    compare(card, a, b)            two versions side by side, deltas computed here
    cost_lines(card)               what the run spent, by model, from the ledger and the model call records
"""

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from ..contracts import (
    PPA,
    ArtifactEvent,
    Candidate,
    CandidateEvent,
    CaptionEvent,
    Equivalence,
    HumanRefEvent,
    PlanBlock,
    PlanEvent,
    RoundEvent,
    Spec,
    SpecEvent,
    WinnerEvent,
)
from ..core.fitness import goal_value, rankable, worse_on_goal
from ..core.protocols import Store
from ..core.records import RunRecord
from ..core.rtl import normalize_rtl_text

log = logging.getLogger("verity.ask")

_DUPLICATE = re.compile(r"^(v\d+) was identical to (v\d+) and was dropped\.$")
_CANDIDATE_ID = re.compile(r"^c\d{1,3}$")
_SKIPPED_ROUND2 = "Skipping round 2"
# Artifact folders the run card keeps: the reports, the walkthrough's sources (spec rules, plan, circuit) and the sign-off.
_KEPT_ARTIFACTS = ("reports/", "spec/", "plan/", "circuit/", "signoff/")

GOAL_WORDS = {"area": "smaller", "speed": "faster", "power": "lower power"}
STRATEGY_WORDS = {"INIT": "first version", "SIMPLIFY": "simplify", "OPTIMIZE": "optimize", "EXPLORE": "explore"}


# ======================================================================
# the run card
# ======================================================================
@dataclass(slots=True)
class RunCard:
    run: RunRecord
    versions: list[Candidate]  # the latest event of each candidate, oldest first
    duplicates: dict[str, str] = field(default_factory=dict)  # dropped label -> the label it repeated
    winner_id: str | None = None
    winner_by: str | None = None  # "ai" | "user"
    ai_pick_id: str | None = None
    rounds: dict[int, list[str]] = field(default_factory=dict)  # round -> parent ids
    skipped_round2: bool = False  # the "No version beat v1 this round. Skipping round 2." caption
    reports: dict[str, str] = field(default_factory=dict)  # candidate id -> OpenSTA report text
    synth: dict[str, str] = field(default_factory=dict)  # candidate id -> Yosys report text
    llm_calls: list[dict] = field(default_factory=list)  # final/llm-calls.json
    human_ref: PPA | None = None
    human_eqy: Equivalence | None = None  # the human reference against the winner
    spec: Spec | None = None
    plan_revision: int = 0
    plan_blocks: list[PlanBlock] = field(default_factory=list)  # the last plan
    artifacts: dict[str, str] = field(default_factory=dict)  # kept artifact events by path, newest content
    samples: dict | None = None  # candidates/c1/samples.json: the first drafts of v1

    @property
    def title(self) -> str:
        return self.run.title or self.run.preset_id

    @property
    def labels(self) -> list[str]:
        return [c.label for c in self.versions]

    def by_label(self, label: str | None) -> Candidate | None:
        if not label:
            return None
        return next((c for c in self.versions if c.label == label), None)

    def by_id(self, cid: str | None) -> Candidate | None:
        return next((c for c in self.versions if c.id == cid), None) if cid else None

    @property
    def baseline(self) -> Candidate | None:
        return next((c for c in self.versions if c.is_baseline), None) or self.by_id("c1")

    @property
    def winner(self) -> Candidate | None:
        return self.by_id(self.winner_id or self.run.winner_id)

    @property
    def round2_ran(self) -> bool:
        return any(r >= 2 for r in self.rounds) or any(c.round >= 2 for c in self.versions)

    @property
    def goal(self) -> str:
        return GOAL_WORDS.get(self.run.priority, self.run.priority)


def _number(c: Candidate) -> int:
    digits = "".join(ch for ch in c.id if ch.isdigit())
    return int(digits) if digits else 0


async def _read_text(store: Store, run_id: str, relpath: str) -> str | None:
    try:
        data = await store.read_run_file(run_id, relpath)
    except (OSError, ValueError):
        return None
    return data.decode("utf-8", errors="replace") if data is not None else None


async def load_card(store: Store, run: RunRecord) -> RunCard:
    """Everything the ask tools read, from the store only (no model, no sandbox)."""
    latest: dict[str, Candidate] = {}
    card = RunCard(run=run, versions=[])
    artifacts: dict[str, str] = {}
    for event in await store.events_after(run.id, 0):
        if isinstance(event, CandidateEvent):
            latest[event.candidate.id] = event.candidate
        elif isinstance(event, CaptionEvent):
            found = _DUPLICATE.match(event.text.strip())
            if found:
                card.duplicates[found.group(1)] = found.group(2)
            elif _SKIPPED_ROUND2 in event.text:
                card.skipped_round2 = True
        elif isinstance(event, RoundEvent):
            card.rounds[event.round] = list(event.parents)
        elif isinstance(event, WinnerEvent):
            card.winner_id, card.winner_by, card.ai_pick_id = event.candidate_id, event.by, event.ai_pick_id
        elif isinstance(event, ArtifactEvent) and event.path.startswith(_KEPT_ARTIFACTS):
            artifacts[event.path] = event.content
        elif isinstance(event, HumanRefEvent):
            card.human_ref, card.human_eqy = event.ppa, event.eqy
        elif isinstance(event, SpecEvent):
            card.spec = event.spec
        elif isinstance(event, PlanEvent):
            card.plan_revision, card.plan_blocks = event.revision, list(event.blocks)
    card.artifacts = artifacts
    card.versions = sorted(latest.values(), key=_number)

    # A rejected version with no attempts whose Verilog equals another version's (older runs have no caption).
    texts = {c.label: normalize_rtl_text(c.rtl) for c in card.versions if c.rtl}
    for c in card.versions:
        if c.status == "rejected" and not c.attempts and c.label not in card.duplicates and c.label in texts:
            same = next((lbl for lbl, t in texts.items() if lbl != c.label and t == texts[c.label]), None)
            if same is not None:
                card.duplicates[c.label] = same

    for c in card.versions:
        if c.ppa is None or not _CANDIDATE_ID.fullmatch(c.id):
            continue
        sta = await _read_text(store, run.id, f"candidates/{c.id}/sta.log")
        card.reports[c.id] = sta if sta is not None else artifacts.get(f"reports/sta_{c.label}.txt", "")
        synth = await _read_text(store, run.id, f"candidates/{c.id}/synth.log")
        card.synth[c.id] = synth if synth is not None else artifacts.get(f"reports/synth_{c.label}.txt", "")

    samples = await _read_text(store, run.id, "candidates/c1/samples.json")
    if samples:
        try:
            parsed_samples = json.loads(samples)
            card.samples = parsed_samples if isinstance(parsed_samples, dict) else None
        except ValueError:
            log.warning("run %s: unreadable samples.json", run.id)

    calls = await _read_text(store, run.id, "final/llm-calls.json")
    if calls:
        try:
            parsed = json.loads(calls)
            card.llm_calls = [c for c in parsed if isinstance(c, dict)] if isinstance(parsed, list) else []
        except ValueError:
            log.warning("run %s: unreadable llm-calls.json", run.id)
    return card


# ======================================================================
# formatting (every number in a code answer is written here)
# ======================================================================
def fmt_area(x: float) -> str:
    return f"{x:.2f} um2"


def fmt_clock(x: float) -> str:
    return f"{x:.3f} ns"


def fmt_power(x: float) -> str:
    return f"{x:.4f} mW"


def fmt_pct(delta: float) -> str:
    return f"{delta + 0.0:+.1f}%"


def fmt_usd(x: float) -> str:
    return f"${x:.3f}" if x < 0.1 else f"${x:.2f}"


def delta_pct(base: float | None, value: float | None) -> float | None:
    """(value - base) / base in percent, as the captions write it: negative is smaller or faster."""
    if base is None or value is None or base == 0:
        return None
    return round(100.0 * (value - base) / base, 1) + 0.0


def strategy(c: Candidate) -> str:
    return STRATEGY_WORDS.get(c.created_by, c.created_by.lower())


def named(c: Candidate) -> str:
    return f"{c.label} ({strategy(c)})"


def join_words(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def tests_text(c: Candidate) -> str | None:
    v = c.verification
    if v is None:
        return None
    if v.lint == "fail" and v.passed == 0:
        if (v.lint_log or "").startswith("Rejected before simulation"):
            return "was rejected by the RTL rules before simulation"
        return "failed the lint check"
    return f"passed {v.passed} of {v.total} tests"


def same_ppa(a: PPA | None, b: PPA | None) -> bool:
    if a is None or b is None:
        return False
    return (a.area_um2, a.cell_count, a.clock_period_ns, a.power_mw) == (b.area_um2, b.cell_count, b.clock_period_ns, b.power_mw)


def measures_text(c: Candidate, base: Candidate | None, *, with_power: bool = True) -> str:
    """'area 72.62 um2 (+0.0% vs v1), 35 cells, clock period 0.389 ns (+0.0% vs v1), power 0.0084 mW (+0.0% vs v1)'."""
    ppa = c.ppa
    if ppa is None:
        return "not measured"
    b = base.ppa if base is not None and base.id != c.id else None
    ref = base.label if base is not None else "v1"
    parts: list[str] = []

    def add(name: str, value: float | None, fmt: Callable[[float], str], base_value: float | None) -> None:
        if value is None:
            return
        d = delta_pct(base_value, value) if b is not None else None
        parts.append(f"{name} {fmt(value)}" + (f" ({fmt_pct(d)} vs {ref})" if d is not None else ""))

    add("area", ppa.area_um2, fmt_area, b.area_um2 if b else None)
    if ppa.cell_count is not None:
        parts.append(f"{ppa.cell_count} cells")
    add("clock period", ppa.clock_period_ns, fmt_clock, b.clock_period_ns if b else None)
    if with_power:
        add("power", ppa.power_mw, fmt_power, b.power_mw if b else None)
    if base is not None and base.id == c.id and parts:
        parts.append(f"{c.label} is the reference for every other version")
    return ", ".join(parts) if parts else "not measured"


# ======================================================================
# tools
# ======================================================================
def drop_reason(card: RunCard, c: Candidate) -> str | None:
    """Why a version left the race, in one sentence, or None when it was not dropped."""
    if c.status not in ("rejected", "fail"):
        if c.eqy is not None and c.eqy.status == "not_equivalent":
            return f"{named(c)} was not proven equal to v1, so it was not kept."
        return None
    if c.label in card.duplicates:
        return f"{named(c)} had the same Verilog as {card.duplicates[c.label]}, so it was dropped before its tests."
    v = c.verification
    fixes = f" after {c.fix_count} fix{'es' if c.fix_count != 1 else ''}" if c.fix_count else ""
    if c.eqy is not None and c.eqy.status == "not_equivalent":
        return f"{named(c)} was not proven equal to v1, so it was dropped."
    if v is not None and v.lint == "fail":
        return f"{named(c)} {tests_text(c)}{fixes}, so it was dropped."
    if v is not None and v.passed < v.total:
        return f"{named(c)} passed {v.passed} of {v.total} tests{fixes}, so it was dropped."
    if v is not None and c.ppa is None:
        return f"{named(c)} passed {v.passed} of {v.total} tests but did not synthesize, so it was dropped."
    if v is None:
        return f"{named(c)} was dropped before its tests."
    return f"{named(c)} was dropped."


def _tie_rule(winner: Candidate, tied: list[Candidate]) -> str:
    """Which tie-break of core.fitness._rank_key decided between the winner and versions with the same score."""
    w_area = winner.ppa.area_um2 if winner.ppa else None
    same_area = [c for c in tied if c.ppa is not None and c.ppa.area_um2 == w_area]
    if len(same_area) == len(tied):
        return "a tie goes to the older version"
    if not same_area:
        return "a tie goes to the smaller area"
    return "a tie goes to the smaller area, then the older version"


def _goal_detail(c: Candidate, base: Candidate) -> str:
    """'clock period 0.350 ns vs 0.344 ns': the goal metric of a version next to v1's, both from the measurements."""
    assert c.fitness is not None
    priority = c.fitness.priority
    name, fmt = {
        "area": ("area", fmt_area),
        "speed": ("clock period", fmt_clock),
        "power": ("power", fmt_power),
    }[priority]
    value, base_value = goal_value(c.ppa, priority), goal_value(base.ppa, priority)
    assert value is not None and base_value is not None
    return f"{name} {fmt(value)} vs {fmt(base_value)}"


def _reports_match(card: RunCard, a: Candidate, b: Candidate) -> bool:
    sta_a, sta_b = card.reports.get(a.id), card.reports.get(b.id)
    syn_a, syn_b = card.synth.get(a.id), card.synth.get(b.id)
    return bool(sta_a and syn_a) and sta_a == sta_b and syn_a == syn_b


def explain_selection(card: RunCard) -> tuple[str, list[str]]:
    """(headline, bullets) for "why was this kept?", from the replayed ranking. No model output is used."""
    run = card.run
    winner = card.winner
    base = card.baseline
    if winner is None:
        ended = f" ({run.error_reason})" if run.error_reason else ""
        return f"No version was kept: the run ended as {run.status}{ended} before a winner was picked.", [
            r for r in (drop_reason(card, c) for c in card.versions) if r
        ]

    ranked = rankable(card.versions)
    score = winner.fitness.score if winner.fitness else 0.0
    others = [c for c in ranked if c.id != winner.id]
    tied = [c for c in others if c.fitness is not None and c.fitness.score == score]
    better = [c for c in others if c.fitness is not None and c.fitness.score > score]
    demoted = [c for c in better if c.eqy is not None and c.eqy.status == "not_equivalent"]
    passed_over = [c for c in better if c not in demoted and worse_on_goal(c, base)]
    goal = f"Your goal: {card.goal}."
    if card.winner_by == "user":
        ai = card.by_id(card.ai_pick_id)
        headline = f"You picked {winner.label}" + (f"; the AI's pick by score was {ai.label}." if ai and ai.id != winner.id else ".")
    elif better:
        verb = "was" if len(better) == 1 else "were"
        base_label = base.label if base is not None else "v1"
        if len(demoted) == len(better):
            why = f"not proven equal to {base_label}"
        elif len(passed_over) == len(better):
            why = f"worse than {base_label} on your goal"
        else:
            why = "not kept"
        headline = f"{winner.label} was kept because {join_words([c.label for c in better])} scored higher but {verb} {why}."
        if len(passed_over) == len(better):
            headline += f" A version can only be kept if it is no worse than {base_label} on the goal."
    elif base is not None and winner.id == base.id:
        headline = f"{winner.label} was kept because no version beat it"
        headline += f", and {_tie_rule(winner, tied)}." if tied else "."
    else:
        gains = []
        if winner.ppa and base and base.ppa:
            for name, value, base_value in (
                ("area", winner.ppa.area_um2, base.ppa.area_um2),
                ("clock period", winner.ppa.clock_period_ns, base.ppa.clock_period_ns),
                ("power", winner.ppa.power_mw, base.ppa.power_mw),
            ):
                d = delta_pct(base_value, value)
                if d is not None:
                    gains.append(f"{name} {fmt_pct(d)}")
        headline = f"{winner.label} was kept: it had the best score, {score:+.2f} vs {base.label if base else 'v1'}"
        headline += f" ({', '.join(gains)})" if gains else ""
        headline += f", and {_tie_rule(winner, tied)}." if tied else "."
    headline = f"{headline} {goal}"

    bullets: list[str] = []
    for c in passed_over:
        assert c.fitness is not None and base is not None
        bullets.append(
            f"{named(c)} scored higher ({c.fitness.score:+.2f}) but was worse than {base.label} on your goal: "
            f"{_goal_detail(c, base)}, so it could not be kept."
        )
    equal = [c for c in others if same_ppa(c.ppa, winner.ppa)]
    if equal and winner.ppa is not None:
        tests = {tests_text(c) for c in equal}
        test_part = f" {next(iter(tests))}" if len(tests) == 1 and None not in tests else ""
        ppa = winner.ppa
        nums = [fmt_area(ppa.area_um2)] if ppa.area_um2 is not None else []
        if ppa.cell_count is not None:
            nums.append(f"{ppa.cell_count} cells")
        if ppa.clock_period_ns is not None:
            nums.append(fmt_clock(ppa.clock_period_ns))
        tail = f": {', '.join(nums)}, so {fmt_pct(0.0)} vs {winner.label}" if nums else ""
        bullets.append(f"{join_words([named(c) for c in equal])}{test_part} and measured the same as {winner.label}{tail}.")
        if all(_reports_match(card, c, winner) for c in equal):
            subject = "Its" if len(equal) == 1 else "Their"
            bullets.append(f"{subject} synthesis and timing reports match {winner.label} exactly.")
    for c in others:
        if c in equal:
            continue
        t = tests_text(c)
        line = f"{named(c)}{' ' + t if t else ''} and measured {measures_text(c, base, with_power=False)}"
        if c.fitness is not None:
            line += f": score {c.fitness.score:+.2f}"
        bullets.append(line + ".")
    for c in card.versions:
        reason = drop_reason(card, c)
        if reason:
            bullets.append(reason)
    bullets.append(round2_text(card))
    return headline, bullets


def round2_text(card: RunCard) -> str:
    if card.round2_ran:
        parents = [card.by_id(p) for p in card.rounds.get(2, [])]
        names = [p.label for p in parents if p is not None]
        return f"Round 2 ran from {join_words(names)}." if names else "Round 2 ran."
    if card.run.budget.max_rounds < 2:
        return "This run allowed one round only, so round 2 did not run."
    if card.skipped_round2:
        return "Round 2 runs only when round 1 beats v1, so it did not run."
    if card.run.status != "done":
        return f"Round 2 did not run: the run ended as {card.run.status}."
    return "Round 2 did not run."


def version_detail(card: RunCard, c: Candidate) -> str:
    """One line about one version, for the facts block and the code fallback."""
    base = card.baseline
    origin = strategy(c)
    if c.parent_id:
        parent = card.by_id(c.parent_id)
        origin += f" of {parent.label}" if parent else ""
    parts = [f"{c.label} ({origin}, round {c.round})"]
    t = tests_text(c)
    if t:
        parts.append(t)
    if c.ppa is not None:
        parts.append(measures_text(c, base))
    if c.fitness is not None and base is not None and c.id != base.id:
        parts.append(f"score {c.fitness.score:+.2f} vs {base.label}")
    if c.fix_count:
        parts.append(f"{c.fix_count} fix{'es' if c.fix_count != 1 else ''}")
    reason = drop_reason(card, c)
    if reason:
        parts.append(reason.rstrip("."))
    elif card.winner is not None and c.id == card.winner.id:
        parts.append("kept (the winner)")
    if c.reason:
        parts.append(f"why it was written: {c.reason.strip()[:200]}")
    return "; ".join(parts)


# ---------- timing ----------
_STEP = re.compile(r"^\s*(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+([\^v])\s+(\S+)\s+\(([^)]+)\)\s*$")
_ARRIVAL = re.compile(r"^\s*(-?\d+\.\d+)\s+data arrival time\s*$")
_SETUP = re.compile(r"^\s*(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+library setup time\s*$")
_POINT = re.compile(r"^(Startpoint|Endpoint):\s+(\S+)\s+\((.*)\)\s*$")
_MIN_PERIOD = re.compile(r"Minimum clock period:\s+(\d+(?:\.\d+)?) ns")
_TARGET = re.compile(r"Target period (\d+(?:\.\d+)?) ns, worst setup slack (-?\d+(?:\.\d+)?) ns")


@dataclass(slots=True)
class CriticalPath:
    label: str
    startpoint: str
    start_kind: str
    endpoint: str
    end_kind: str
    arrival_ns: float
    launch_ns: float  # inside the start cell (a flip-flop's clock to output), 0 for an input port
    gates: list[str]  # cell types between the start cell and the end cell
    setup_ns: float | None
    period_ns: float | None
    target_ns: float | None
    slack_ns: float | None


def parse_sta(label: str, text: str) -> CriticalPath | None:
    """The first max path of an OpenSTA report_checks section (verity/orchestrator/build.py _sta_report)."""
    start = end = start_kind = end_kind = ""
    steps: list[tuple[float, str, str]] = []
    arrival: float | None = None
    setup: float | None = None
    for line in text.splitlines():
        point = _POINT.match(line)
        if point:
            if point.group(1) == "Startpoint" and not start:
                start, start_kind = point.group(2), point.group(3)
            elif point.group(1) == "Endpoint" and not end:
                end, end_kind = point.group(2), point.group(3)
            continue
        if arrival is None:
            found = _ARRIVAL.match(line)
            if found:
                arrival = float(found.group(1))
                continue
            step = _STEP.match(line)
            if step and start:
                steps.append((float(step.group(1)), step.group(4), step.group(5)))
        elif setup is None:
            found = _SETUP.match(line)
            if found:
                setup = abs(float(found.group(1)))
    if arrival is None or not start:
        return None
    period = _MIN_PERIOD.search(text)
    target = _TARGET.search(text)

    def instance(pin: str) -> str:
        return pin.rsplit("/", 1)[0]

    launch = sum(d for d, pin, _ in steps if instance(pin) == start)
    gates = [
        cell.split("_X")[0]
        for _, pin, cell in steps
        if instance(pin) not in (start, end) and cell not in ("in", "out") and "/" in pin
    ]
    return CriticalPath(
        label=label,
        startpoint=start,
        start_kind=start_kind,
        endpoint=end,
        end_kind=end_kind,
        arrival_ns=arrival,
        launch_ns=launch,
        gates=gates,
        setup_ns=setup,
        period_ns=float(period.group(1)) if period else None,
        target_ns=float(target.group(1)) if target else None,
        slack_ns=float(target.group(2)) if target else None,
    )


def critical_path(card: RunCard, c: Candidate) -> CriticalPath | None:
    text = card.reports.get(c.id) or ""
    return parse_sta(c.label, text) if text else None


def _kind(kind: str) -> str:
    low = kind.lower()
    if "flip-flop" in low:
        return "flip-flop"
    if "input port" in low:
        return "input"
    if "output port" in low:
        return "output"
    return "point"


def critical_path_text(path: CriticalPath) -> tuple[str, list[str]]:
    """(headline, bullets) naming the slowest path, all numbers from the report."""
    n = len(path.gates)
    gates = f"{n} gate{'s' if n != 1 else ''}"
    src, dst = _kind(path.start_kind), _kind(path.end_kind)
    head = f"The slowest path in {path.label} runs from {src} {path.startpoint} to {dst} {path.endpoint} through {gates}"
    if path.gates:
        head += f" ({', '.join(path.gates)})"
    head += f": the signal arrives after {fmt_clock(path.arrival_ns)}"
    if path.setup_ns is not None:
        head += f", plus {fmt_clock(path.setup_ns)} setup"
    if path.period_ns is not None:
        head += f", which sets the {fmt_clock(path.period_ns)} minimum clock period"
    head += "."
    bullets: list[str] = []
    if path.launch_ns > 0:
        logic = round(path.arrival_ns - path.launch_ns, 3) + 0.0
        bullets.append(
            f"The first {src} takes {fmt_clock(path.launch_ns)} to put out its value; the {gates} take the other {fmt_clock(logic)}."
        )
    if path.setup_ns is not None:
        bullets.append(f"The capturing {dst} needs its input {fmt_clock(path.setup_ns)} before the clock edge (setup time).")
    if path.target_ns is not None and path.slack_ns is not None:
        bullets.append(f"At the {fmt_clock(path.target_ns)} test clock there is {fmt_clock(path.slack_ns)} of slack.")
    return head, bullets


# ---------- compare and cost ----------
def compare(card: RunCard, a: Candidate, b: Candidate) -> str:
    """One line: b against a, every delta computed here."""
    parts = [f"{b.label} vs {a.label}:"]
    if a.ppa is not None and b.ppa is not None:
        rows = (
            ("area", a.ppa.area_um2, b.ppa.area_um2, fmt_area),
            ("clock period", a.ppa.clock_period_ns, b.ppa.clock_period_ns, fmt_clock),
            ("power", a.ppa.power_mw, b.ppa.power_mw, fmt_power),
        )
        cells = []
        for name, x, y, fmt in rows:
            if x is None or y is None:
                continue
            d = delta_pct(x, y)
            cells.append(f"{name} {fmt(y)} vs {fmt(x)}" + (f" ({fmt_pct(d)})" if d is not None else ""))
        if a.ppa.cell_count is not None and b.ppa.cell_count is not None:
            cells.append(f"cells {b.ppa.cell_count} vs {a.ppa.cell_count}")
        parts.append(", ".join(cells) + ";")
    else:
        parts.append("not both measured;")
    ta, tb = tests_text(a), tests_text(b)
    if ta and tb:
        parts.append(f"{b.label} {tb}, {a.label} {ta};")
    same = bool(a.rtl and b.rtl) and normalize_rtl_text(a.rtl) == normalize_rtl_text(b.rtl)
    parts.append("same Verilog." if same else "different Verilog.")
    return " ".join(parts)


def model_name(model_id: str) -> str:
    """'nvidia/Nemotron-3-Ultra-550b-a55b' -> 'Nemotron 3 Ultra', 'zai-org/GLM-5.3' -> 'GLM 5.3'."""
    words = [w for w in model_id.split("/")[-1].split("-") if not re.fullmatch(r"a?\d+b", w.lower())]
    return " ".join(w.replace("_", ".") if any(ch.isupper() for ch in w) else w.capitalize().replace("_", ".") for w in words)


_ROLE_WORDS = {"planner": "the plan", "rtl_gen": "first versions", "repair": "fixes", "optimizer": "new versions"}


def cost_lines(card: RunCard) -> tuple[str, list[str]]:
    run = card.run
    spent = run.spent
    head = f"This run spent {fmt_usd(spent.usd)}."
    if run.status == "cap_reached":
        head += " It stopped at the spend limit."
    bullets = [f"{spent.llm_calls} model calls: {spent.input_tokens:,} input and {spent.output_tokens:,} output tokens."]
    by_model: dict[str, dict] = {}
    for call in card.llm_calls:
        model = str(call.get("model") or "")
        usd = call.get("usd")
        if not model or not isinstance(usd, (int, float)):
            continue
        row = by_model.setdefault(model, {"usd": 0.0, "calls": 0, "roles": []})
        row["usd"] += float(usd)
        row["calls"] += 1
        role = _ROLE_WORDS.get(str(call.get("role")), str(call.get("role") or ""))
        if role and role not in row["roles"]:
            row["roles"].append(role)
    for model, row in by_model.items():
        n = row["calls"]
        bullets.append(
            f"{model_name(model)} wrote {join_words(row['roles'])}: {n} call{'s' if n != 1 else ''}, {fmt_usd(row['usd'])}."
        )
    fixes = spent.repairs
    bullets.append(
        f"It ran for {spent.wall_s:.1f} s and made {len(card.versions)} version{'s' if len(card.versions) != 1 else ''}"
        f" with {fixes} fix{'es' if fixes != 1 else ''}."
    )
    return head, bullets


__all__ = [
    "CriticalPath",
    "RunCard",
    "compare",
    "cost_lines",
    "critical_path",
    "critical_path_text",
    "drop_reason",
    "explain_selection",
    "load_card",
    "model_name",
    "parse_sta",
    "version_detail",
]
