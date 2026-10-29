"""`packs` suite: self-test of every task pack (backend/tasks/self_test.md). $0, no LLM, needs the sandbox image.

Per pack: integrity, rules, reference passes, determinism, measurable, every mutant fails, equivalence sanity, the
sign-off and waveform tools (one more seed, waveforms of the reference and of a failing mutant), pack
unchanged. One result row per pack; the report line of self_test.md goes into `extra`.
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from ..core.errors import PackIntegrityError, RtlRejected, ToolInfraError
from ..core.pack import TaskPack, verify_manifest
from ..core.results import VerifyResult
from ..core.rtl import Rtl
from .harness import EvalEnv, pack_unchanged
from .record import EvalRecord

log = logging.getLogger("verity.evals")

SELF_TEST_NAME = "self_test.md"  # lives in the tasks directory
_ROW = re.compile(r"^\|\s*(\w+)\s*\|\s*(\w+)\s*\|\s*`(.*)`\s*\|\s*`(.*)`\s*\|\s*(\w+)\s*\|\s*$")


@dataclass(frozen=True, slots=True)
class Mutant:
    pack_id: str
    name: str
    find: str
    replace: str
    expected: str


def load_mutants(path: Path) -> list[Mutant]:
    """Rows of the mutant table in self_test.md: | pack | mutant | `find` | `replace` | expected |."""
    mutants: list[Mutant] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _ROW.match(line.strip())
        if match and match.group(1) not in ("Pack",):
            mutants.append(Mutant(*match.groups()))
    return mutants


def apply_mutant(reference: str, mutant: Mutant) -> str | None:
    """Exact single replacement. None when the find string does not occur exactly once."""
    text = reference.replace("\r\n", "\n")
    if text.count(mutant.find) != 1:
        return None
    return text.replace(mutant.find, mutant.replace, 1)


def _signature(result: VerifyResult) -> tuple:
    sim = result.sim
    return (
        result.status,
        sim.total if sim else None,
        sim.passed if sim else None,
        tuple(f.name for f in sim.failures) if sim else (),
    )


async def run(env: EvalEnv, packs: list[TaskPack], repeats: int) -> None:
    del repeats  # a self-test has no randomness: one row per pack
    mutants = load_mutants(env.settings.tasks_dir / SELF_TEST_NAME)
    for pack in packs:
        record = env.new_record("packs", pack.id)
        try:
            await _check_pack(env, pack, mutants, record)
        except (ToolInfraError, OSError) as exc:
            record.ok = False
            record.error = f"sandbox: {exc}"
        await env.write(record)


async def _check_pack(env: EvalEnv, pack: TaskPack, all_mutants: list[Mutant], record: EvalRecord) -> None:
    problems: list[str] = []
    extra: dict[str, object] = {"pack_sha256": pack.sha256, "tests": pack.test_count}
    record.extra = extra

    # 1. integrity
    try:
        verify_manifest(pack.root)
    except PackIntegrityError as exc:
        problems.append(f"integrity: {exc}")

    # 2. rules
    reference_text = pack.reference_rtl()
    reference: Rtl | None = None
    if reference_text is None:
        problems.append("no reference.v")
    else:
        try:
            reference = pack.validate_rtl(reference_text, max_bytes=env.settings.rtl.max_bytes)
        except RtlRejected as exc:
            problems.append(f"reference breaks the coding rules: {exc}")

    if reference is None or env.skip_sandbox:
        if env.skip_sandbox:
            record.note = "sandbox steps skipped (--skip-sandbox)"
        record.ok = not problems
        record.error = "; ".join(problems) or None
        extra["problems"] = problems
        return

    # 3. reference passes, 4. determinism
    with record.stage("sim"):
        first = await env.tools.verify(reference, pack)
        second = await env.tools.verify(reference, pack)
    ref_pass = first.passed_all(pack.test_count)
    record.final_pass = ref_pass
    extra["reference_pass"] = ref_pass
    if not ref_pass:
        problems.append(f"reference does not pass: status {first.status}, {first.message or 'no message'}")
    if _signature(first) != _signature(second):
        problems.append("testbench is not deterministic (two runs of the reference differ)")
    if first.lint is not None and not first.lint.ok:
        problems.append("lint errors on the reference")

    # 5. measurable
    with record.stage("synth"):
        measured = await env.tools.measure(reference, pack)
    ppa = measured.to_ppa()
    record.baseline_area_um2 = record.final_area_um2 = ppa.area_um2
    record.baseline_clock_ns = record.final_clock_ns = ppa.clock_period_ns
    if not measured.measured or not (ppa.area_um2 and ppa.area_um2 > 0):
        problems.append(f"reference does not synthesize: status {measured.status}")

    # 6. mutants fail
    mutants = [m for m in all_mutants if m.pack_id == pack.id]
    killed = 0
    first_sim_fail: Rtl | None = None
    first_fail_case: str | None = None
    mutant_rows: list[dict[str, object]] = []
    for mutant in mutants:
        text = apply_mutant(reference_text or "", mutant)
        if text is None:
            problems.append(f"mutant {mutant.name}: the find string does not occur exactly once")
            mutant_rows.append({"name": mutant.name, "status": "not_applicable"})
            continue
        try:
            rtl = pack.validate_rtl(text, max_bytes=env.settings.rtl.max_bytes)
        except RtlRejected as exc:
            problems.append(f"mutant {mutant.name}: rejected by the rules, so it cannot test the testbench ({exc})")
            mutant_rows.append({"name": mutant.name, "status": "rules_rejected"})
            continue
        with record.stage("mutants"):
            result = await env.tools.verify(rtl, pack)
        dead = not result.passed_all(pack.test_count)
        killed += int(dead)
        mutant_rows.append({"name": mutant.name, "status": result.status, "expected": mutant.expected, "killed": dead})
        if not dead:
            problems.append(f"mutant {mutant.name} PASSES: the testbench is too weak")
        if first_sim_fail is None and result.status == "sim_fail":
            first_sim_fail = rtl
            first_fail_case = result.sim.failures[0].name if result.sim and result.sim.failures else None
    extra["mutants_killed"] = killed
    extra["mutants_total"] = len(mutants)
    extra["mutants"] = mutant_rows
    if len(mutants) < 3:
        problems.append(f"only {len(mutants)} mutants in self_test.md, at least 3 are required")

    # 7. equivalence sanity
    if pack.eqy.enabled:
        with record.stage("prove"):
            same = await env.tools.check_equivalence(reference, reference, pack)
        extra["eqy_self"] = same.status
        record.equivalence = same.status
        if same.status == "not_equivalent":
            problems.append("EQY says the reference is not equivalent to itself")
        if first_sim_fail is not None:
            with record.stage("prove"):
                diff = await env.tools.check_equivalence(reference, first_sim_fail, pack)
            extra["eqy_mutant"] = diff.status
            if diff.status == "proven":
                problems.append("EQY proved a failing mutant equivalent: the proof path has a false positive")

    # 9. sign-off and waveform tools (CONTRACTS 3.9 to 3.11): one more seed passes, a waveform of the reference's first
    #    case, and a waveform of the first failing mutant that names a failing cycle.
    with record.stage("signoff"):
        problems += await _check_signoff_tools(env, pack, reference, first_sim_fail, first_fail_case, extra)

    # 8. pack unchanged
    if not pack_unchanged(pack):
        problems.append("the pack changed on disk during the self-test")
    record.pack_hash_ok = pack_unchanged(pack)

    extra["problems"] = problems
    record.ok = not problems
    record.error = "; ".join(problems) or None


async def _check_signoff_tools(
    env: EvalEnv, pack: TaskPack, reference: Rtl, failing: Rtl | None, fail_case: str | None, extra: dict[str, object]
) -> list[str]:
    """The reference passes one more seed; a waveform of its first case shows no failure; a waveform of the first
    failing mutant's first failing case names the failing cycle. `extra` records whether the golden replay was used."""
    problems: list[str] = []
    extra_seed = 2 if pack.sim.seed != 2 else 3
    seeds = await env.tools.verify_seeds(reference, pack, [extra_seed])
    extra["signoff_seed"] = seeds.status
    if seeds.status != "pass":
        problems.append(f"the reference fails the locked tests on seed {extra_seed}: {seeds.status} {seeds.message}")
    if seeds.lint is not None:
        extra["lint_by_tag"] = seeds.lint.by_tag

    passing = await env.tools.waveform(reference, pack, None)
    extra["wave_reference"] = passing.status
    if passing.status != "ok" or passing.wave is None or passing.wave.fail_cycle is not None:
        problems.append(f"no waveform of the reference's first case: {passing.status} {passing.message}")
    if failing is not None and fail_case is not None:
        failed = await env.tools.waveform(failing, pack, fail_case)
        wave = failed.wave
        extra["wave_mutant"] = failed.status
        extra["wave_mutant_expected"] = bool(wave and any(s.expected is not None for s in wave.signals))
        extra["wave_mutant_note"] = wave.note if wave else ""
        if failed.status != "ok" or wave is None or wave.fail_cycle is None:
            problems.append(f"no waveform of the failing mutant's first failing case: {failed.status} {failed.message}")
    return problems
