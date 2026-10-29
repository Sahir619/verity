"""Caption templates (ARCHITECTURE 9.1: template strings from code, not LLM prose) and chapter labels.

Wording follows frontend/public/runs/golden.json. Only two templates embed model text, both truncated and
ASCII-cleaned: `diagnosis()` (the repair role's one-sentence reading) and nothing else. Numbers come from run data.
"""

import re

from ..contracts import PPA, Priority

# Chapter labels in timeline order. RunLog.meta.chapters uses (id, label, first tMs where a caption used it).
CHAPTERS: tuple[tuple[str, str], ...] = (
    ("spec", "Spec"),
    ("plan", "Plan"),
    ("lock", "Lock"),
    ("build", "Build and fix"),
    ("round1", "Round 1"),
    ("round2", "Round 2"),
    ("prove", "Prove"),
    ("done", "Done"),
)
CH_SPEC, CH_PLAN, CH_LOCK, CH_BUILD, CH_ROUND1, CH_ROUND2, CH_PROVE, CH_DONE = (label for _, label in CHAPTERS)
CHAPTER_ID_BY_LABEL: dict[str, str] = {label: cid for cid, label in CHAPTERS}


def round_chapter(round_n: int) -> str:
    return CH_ROUND1 if round_n <= 1 else CH_ROUND2

_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")
_GOALS: dict[Priority, str] = {
    "area": "smallest area",
    "speed": "shortest clock period",
    "power": "lowest power",
}


def words(n: int) -> str:
    return _WORDS[n] if 0 <= n < len(_WORDS) else str(n)


def clean(text: str, limit: int = 200) -> str:
    """Single line, ASCII, no dashes other than '-', truncated on a word boundary."""
    text = text.replace("\u2014", ", ").replace("\u2013", "-").replace("\u2212", "-")
    text = re.sub(r"\s+", " ", text).strip()
    text = text.encode("ascii", errors="ignore").decode("ascii")
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:")
    return cut + "..."


_DASH = r"[\u2012\u2013\u2014\u2015]"  # figure dash, en dash, em dash, horizontal bar
_DASH_RANGE = re.compile(rf"(?<=\d)[ \t]*{_DASH}[ \t]*(?=\d)")
_DASH_RUN = re.compile(rf"[ \t]*{_DASH}(?:[ \t]*{_DASH})*[ \t]*")
_MARK = "\x00"


def plain_dashes(text: str) -> str:
    """The project rule for words a model wrote: no em dash and no en dash. A number range (digits on both sides) becomes
    "4 to 32"; any other dash becomes a comma that folds into the punctuation beside it, and a dash at the start or the
    end of a line is dropped. A non-breaking hyphen (U+2011) becomes a plain hyphen. The numbers in the text do not change."""
    text = text.replace("\u2011", "-")
    if not re.search(_DASH, text):
        return text
    text = _DASH_RANGE.sub(" to ", text.replace(_MARK, ""))
    text = _DASH_RUN.sub(_MARK, text)
    text = re.sub(rf"(?m)^{_MARK}|{_MARK}$", "", text)
    text = re.sub(rf"([,;:.!?]){_MARK}", r"\1 ", text)
    text = re.sub(rf"\({_MARK}", "(", text)
    text = re.sub(rf"{_MARK}(?=[,;:.!?)])", "", text)
    return re.sub(r"[ \t]{2,}", " ", text.replace(_MARK, ", "))


def _num(value: float, decimals: int = 0) -> str:
    return f"{value:,.{decimals}f}"


def _pct(delta: float) -> str:
    return f"{'+' if delta >= 0 else '-'}{abs(delta):.1f}%"


# ---------- spec, plan, lock ----------
def spec_read(summary: str, priority: Priority) -> str:
    return f"Spec read: {summary}. Goal: {_GOALS[priority]}."


def drafting_plan() -> str:
    return "Drafting the plan."


def plan_drafted(blocks: int, auto_continue_ms: int) -> str:
    return f"Plan drafted: {blocks} blocks. Building in {round(auto_continue_ms / 1000)} s unless you steer."


def toggled(block_name: str, variant_label: str, value: str) -> str:
    return f"You set {variant_label.lower()} on {block_name} to {value}. Replanning."


def pinned(block_name: str, pin: str) -> str:
    return f"You pinned \"{pin}\" on {block_name}. Replanning."


def plan_updated(auto_continue_ms: int) -> str:
    return f"Plan updated. Building in {round(auto_continue_ms / 1000)} s."


def gate_held() -> str:
    return "Plan on hold. Building when you resume."


def building_now() -> str:
    return "Building now."


def plan_rejected(reason: str) -> str:
    return f"Plan rejected before any Verilog was written: {clean(reason, 160)}"


def locked(test_count: int) -> str:
    return f"Answer key locked: {test_count} tests from the golden model. The agent cannot edit them."


# ---------- build and fix ----------
def writing_v1() -> str:
    return "Writing v1 from the plan."


def auto_fixed(label: str, changes: str) -> str:
    return f"{label}: fixed declarations without asking the model ({clean(changes, 160)}). Re-checking."


def sampled(count: int, passed: int, total: int) -> str:
    return f"Wrote {count} first versions in parallel. Continuing with the one that passes {passed} of {total} tests."


def restarted(label: str) -> str:
    return f"{label} failed the same way twice. Rewriting it from the plan instead of patching."


def simulating(test_count: int) -> str:
    return f"Simulating {test_count} tests against the golden model."


def failed_tests(label: str, failed: int, total: int) -> str:
    return f"{label} failed {failed} of {total} tests. Reading the mismatches."


def failed_lint(label: str) -> str:
    return f"{label} did not pass lint. Reading the errors."


def rejected_rules(label: str, first_problem: str) -> str:
    return f"{label} broke the coding rules: {clean(first_problem, 120)}. Fixing it."


def diagnosis(text: str) -> str:
    return f"Diagnosis: {clean(text, 200)}"


def baseline_passes(label: str, total: int) -> str:
    return f"{label} passes {total} of {total}. This is the baseline."


def no_baseline(fixes: int) -> str:
    return f"No passing version after {fixes} fix{'es' if fixes != 1 else ''}. Stopping here."


def baseline_measured(label: str, ppa: PPA) -> str:
    parts = []
    parts.append(f"{_num(ppa.area_um2)} µm²" if ppa.area_um2 is not None else "area not measured")
    parts.append(f"{ppa.clock_period_ns:.2f} ns" if ppa.clock_period_ns is not None else "timing not measured")
    parts.append(f"{ppa.power_mw:.2f} mW" if ppa.power_mw is not None else "power not measured")
    return f"{label} measured: {', '.join(parts)}. Everything after is compared to this."


def baseline_synth_failed(label: str) -> str:
    return f"{label} passed the tests but did not synthesize. Stopping here."


# ---------- evolution ----------
def round_started(round_n: int, children: int, parent_labels: list[str], from_elites: bool) -> str:
    source = "the elites" if from_elites else " and ".join(parent_labels)
    return f"Round {round_n}: {words(children)} new version{'s' if children != 1 else ''} from {source}."


def child_failed(label: str, failed: int, total: int) -> str:
    return f"{label} failed {failed} of {total} tests. Fixing it before it can compete."


def child_rejected(label: str) -> str:
    return f"{label} failed again after a fix and was rejected."


def child_duplicate(label: str, same_as: str) -> str:
    return f"{label} was identical to {same_as} and was dropped."


def child_synth_failed(label: str) -> str:
    return f"{label} passed the tests but did not synthesize. Rejected."


def child_measured(label: str, baseline_label: str, ppa: PPA, baseline: PPA) -> str:
    def delta(b: float | None, c: float | None) -> float | None:
        return None if b is None or c is None or b == 0 else 100.0 * (c - b) / b

    parts = []
    d = delta(baseline.area_um2, ppa.area_um2)
    parts.append(f"area {_pct(d)} vs {baseline_label}" if d is not None else "area not measured")
    d = delta(baseline.clock_period_ns, ppa.clock_period_ns)
    parts.append(f"clock period {_pct(d)}" if d is not None else "timing not measured")
    d = delta(baseline.power_mw, ppa.power_mw)
    parts.append(f"power {_pct(d)}" if d is not None else "power not measured")
    return f"{label}: {', '.join(parts)}."


def elites_kept(labels: list[str]) -> str:
    if len(labels) == 1:
        return f"Kept the best: {labels[0]}."
    return f"Kept the {words(len(labels))} best: {' and '.join(labels)}."


def no_improvement(baseline_label: str = "v1") -> str:
    return f"No version beat {baseline_label} this round. Skipping round 2."


def winner(label: str) -> str:
    return f"Winner by fitness: {label}."


def baseline_wins(label: str = "v1") -> str:
    return f"No version beat {label}. {label} stands."


# ---------- prove ----------
def proving(label: str, baseline_label: str = "v1") -> str:
    return f"Checking {label} against {baseline_label} with Yosys EQY."


def proven(baseline_label: str = "v1") -> str:
    return f"Proven equal to {baseline_label}."


def measuring_human_reference() -> str:
    return "Measuring the human-written design with the same tools."


def not_proven() -> str:
    return "Tested, not proven: EQY could not decide in time."


def not_equivalent(label: str, next_label: str) -> str:
    return f"{label} is not equal to v1 and was rejected. Next best: {next_label}."


# ---------- end ----------
def budget_reached(limit: str) -> str:
    return f"Budget reached ({limit.replace('_', ' ')}). Finishing with what is measured."


def done(versions: int, fixes: int, rejected: int, human_inputs: int) -> str:
    return (
        f"Done. {versions} version{'s' if versions != 1 else ''}, {fixes} fix{'es' if fixes != 1 else ''}, "
        f"{rejected} rejected. Human inputs: {human_inputs}."
    )


def signoff(
    label: str,
    test_count: int,
    seeds_passed: int,
    seeds_run: int,
    lint_errors: int,
    lint_warnings: int,
    caught: int | None,
    planted: int | None,
) -> str:
    """After `Done`: the sign-off of the winner. Every number is from the sign-off artifact of this run."""
    if seeds_passed == seeds_run:
        tests = f"all {test_count} tests passed on {seeds_run} seed{'s' if seeds_run != 1 else ''}"
    else:
        tests = f"the {test_count} tests passed on {seeds_passed} of {seeds_run} seeds"
    lint = (
        f"{lint_errors} lint error{'s' if lint_errors != 1 else ''} and "
        f"{lint_warnings} warning{'s' if lint_warnings != 1 else ''}"
    )
    if planted:
        strength = f"{caught} of {planted} planted bugs caught"
    else:
        strength = "test strength not measured"
    return f"Sign-off for {label}: {tests}, {lint}, {strength}."


def stopped() -> str:
    return "Run stopped by you."


def failed(reason: str) -> str:
    return f"Run ended: {clean(reason, 160)}"


# ---------- infrastructure failures: one plain sentence the user can act on ----------
# Used for the terminal `run error` message, its caption and the POST /runs 503. The raw detail stays in the server
# log only (verity.api.redact scrubs tokens there).
DOCKER_DOWN = "The test machine (Docker) is not running. Start Docker and try again."
IMAGE_MISSING = "The test machine is missing its tool image. Build the sandbox image and try again."
SANDBOX_TIMEOUT = "The test machine stopped answering. Restart Docker and try again."
SANDBOX_BROKEN = "The test machine failed. Try again, and restart Docker if it keeps happening."
MODEL_TIMEOUT = "The model service took too long to answer. Try again in a minute."
MODEL_UNREACHABLE = "The model service could not be reached. Check the internet connection and try again."
MODEL_RATE_LIMITED = "The model service is rate limiting this key. Wait a minute and try again."
MODEL_KEY = "The model service did not accept the API key. Check LLM_API_KEY and try again."
MODEL_DOWN = "The model service had an error. Try again in a minute."
RESTARTED = "The server restarted during this run. Try again."

_DOCKER_DOWN_MARKS = (
    "failed to connect to the docker api",
    "cannot connect to the docker daemon",
    "is the docker daemon running",
    "error during connect",
    "dockerdesktoplinuxengine",
    "cannot start docker",
)
_IMAGE_MISSING_MARKS = ("no such image", "unable to find image", "pull access denied", "manifest unknown")
_TIMEOUT_MARKS = ("timed out", "timeout")


def sandbox_failed(detail: str) -> str:
    """Plain sentence for a sandbox infrastructure failure (ToolInfraError text plus its stderr tail)."""
    text = detail.lower()
    if any(mark in text for mark in _DOCKER_DOWN_MARKS):
        return DOCKER_DOWN
    if any(mark in text for mark in _IMAGE_MISSING_MARKS):
        return IMAGE_MISSING
    if any(mark in text for mark in _TIMEOUT_MARKS):
        return SANDBOX_TIMEOUT
    return SANDBOX_BROKEN


def model_failed(detail: str, status: int | None, retryable: bool) -> str | None:
    """Plain sentence for a model service failure (LLMError), or None when it is not an infrastructure problem
    (a prompt over the per-call limit, a fake provider error): the caller keeps its own wording then."""
    text = detail.lower()
    if status == 429:
        return MODEL_RATE_LIMITED
    if status in (401, 403) or "llm_api_key is not set" in text:
        return MODEL_KEY
    if "timed out" in text:
        return MODEL_TIMEOUT
    if "could not reach" in text:
        return MODEL_UNREACHABLE
    if retryable or (status is not None and status >= 500):
        return MODEL_DOWN
    return None
