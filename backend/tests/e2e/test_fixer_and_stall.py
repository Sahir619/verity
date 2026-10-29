"""R2 and R5 through the real orchestrator with the fake provider: the declaration fixer needs no LLM call, and a
repeated failure restarts the candidate from the plan instead of patching it again."""

import dataclasses
import json
from pathlib import Path

import pytest

from verity.contracts import CandidateEvent, CaptionEvent
from verity.core.pack import TaskPack
from verity.core.results import ContractResult, LintResult, VerifyResult
from verity.core.rtl import Rtl, normalize_rtl_text
from verity.core.rtl_fixer import fix_declarations
from verity.llm.fake import _answer, _inject_bug
from verity_test_support import FakeToolRunner, execute_fake_run

pytestmark = pytest.mark.asyncio


class StrayGenerateLint(FakeToolRunner):
    """Lint fails on a stray `generate` line, the way Verilator does; otherwise the usual fake behaviour."""

    async def verify(self, rtl: Rtl, pack: TaskPack) -> VerifyResult:
        if "\n    generate\n" in rtl.text:
            self.verify_calls += 1
            log = "%Error: dut.v:5:5: syntax error, unexpected generate\n%Error: Exiting due to 1 error(s)\n"
            return VerifyResult(
                status="lint_fail",
                contract=ContractResult(ok=True),
                lint=LintResult(ok=False, errors=1, log=log, tool="fake lint"),
                duration_ms=3,
            )
        # reference.v still uses wire/reg, which the fixer also converts, so accept the converted reference too.
        reference = normalize_rtl_text(pack.reference_rtl() or "")
        if rtl.text == normalize_rtl_text(fix_declarations(reference).text):
            return await super().verify(Rtl.validate(reference, top=pack.top, max_lines=10_000), pack)
        return await super().verify(rtl, pack)


def _count_roles(ctx, counts: dict[str, int], *, restart_with: str | None = None) -> None:
    roles = ctx.roles

    async def repair(*a, **k):
        counts["repair"] = counts.get("repair", 0) + 1
        return await roles.repair(*a, **k)

    async def generate_rtl(*a, **k):
        counts["rtl_gen"] = counts.get("rtl_gen", 0) + 1
        out = await roles.generate_rtl(*a, **k)
        if restart_with is not None and counts["rtl_gen"] >= 2:
            return dataclasses.replace(out, text=restart_with)
        return out

    ctx.roles = dataclasses.replace(roles, repair=repair, generate_rtl=generate_rtl)


async def test_fixer_repairs_lint_failure_without_an_llm_call(settings, mac4: TaskPack, tmp_path: Path) -> None:
    reference = mac4.reference_rtl().replace("\r\n", "\n").strip("\n")
    lines = reference.split("\n")
    start = next(i + 1 for i, line in enumerate(lines) if ");" in line.split("//")[0])
    broken = "\n".join(lines[:start] + ["    generate", "    endgenerate"] + lines[start:])
    cassettes = tmp_path / "cassettes" / "mac4"
    cassettes.mkdir(parents=True)
    (cassettes / "rtl_gen.txt").write_text(_answer("First version.", broken), encoding="utf-8")

    counts: dict[str, int] = {}
    run = await execute_fake_run(
        settings, mac4, mode="fast", tools=StrayGenerateLint(), cassette_dir=tmp_path / "cassettes",
        on_ctx=lambda ctx: _count_roles(ctx, counts),
    )
    try:
        assert run.error is None and run.outcome is not None and run.outcome.status == "done"
        c1 = [e.candidate for e in run.events if isinstance(e, CandidateEvent) and e.candidate.id == "c1"][-1]
        assert [a.by for a in c1.attempts] == ["INIT", "FIX"]
        assert c1.attempts[0].verification.lint == "fail" and c1.attempts[1].verification.lint == "pass"
        assert c1.fix_count == 0, "a deterministic fix spends no repair"
        assert "repair" not in counts, "the repair LLM role must not be called"
        assert "without a model call" in (c1.attempts[0].verification.diagnosis or "")
        captions = [e.text for e in run.events if isinstance(e, CaptionEvent)]
        assert any("without asking the model" in t for t in captions)
        record = json.loads(await run.store.read_run_file(run.run_id, "candidates/c1/attempt-1.autofix.json"))
        assert record["lintIssues"] == ["GENERATE"] and record["fromAttempt"] == 1
    finally:
        await run.store.close()


async def test_same_failure_twice_restarts_from_the_plan(settings, mac4: TaskPack, tmp_path: Path) -> None:
    reference = mac4.reference_rtl().replace("\r\n", "\n").strip("\n")
    buggy = _inject_bug(reference)
    assert buggy != reference
    cassettes = tmp_path / "cassettes" / "mac4"
    cassettes.mkdir(parents=True)
    # The scripted repair returns the same failing RTL it was given.
    (cassettes / "repair.txt").write_text(_answer("Looked again.", buggy, diagnosis="Not sure."), encoding="utf-8")

    counts: dict[str, int] = {}
    run = await execute_fake_run(
        settings, mac4, mode="fast", cassette_dir=tmp_path / "cassettes",
        on_ctx=lambda ctx: _count_roles(ctx, counts, restart_with=reference),
    )
    try:
        assert run.error is None and run.outcome is not None and run.outcome.status == "done"
        c1 = [e.candidate for e in run.events if isinstance(e, CandidateEvent) and e.candidate.id == "c1"][-1]
        assert counts == {"rtl_gen": 2, "repair": 1}, counts
        assert len(c1.attempts) == 3
        assert c1.attempts[2].reason == "Restarted from the plan after the same failure twice"
        assert c1.attempts[2].verification.passed == c1.attempts[2].verification.total
        assert c1.fix_count == 2, "the repair and the restart both count against the repair budget"
        captions = [e.text for e in run.events if isinstance(e, CaptionEvent)]
        assert any("failed the same way twice" in t for t in captions)
        assert json.loads(await run.store.read_run_file(run.run_id, "candidates/c1/stall-2.json"))["signature"] == "test:acc_case"
    finally:
        await run.store.close()
