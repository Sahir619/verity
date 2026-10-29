"""R4 through the real orchestrator with the fake provider: k first attempts in parallel, the fixer and the checks on
each, lint failures dropped, the best one continues, and a full pass needs no repair."""

import dataclasses
import json

import pytest

from verity.contracts import CandidateEvent, CaptionEvent
from verity.core.errors import RoleOutputError
from verity.core.llm_types import CallMeta, RtlOut
from verity.core.pack import TaskPack
from verity.core.results import ContractResult, LintResult, SimCase, SimResult, VerifyResult
from verity.core.rtl import Rtl
from verity_test_support import FakeToolRunner, execute_fake_run

pytestmark = pytest.mark.asyncio


class MarkerTools(FakeToolRunner):
    """A `// lintbad` marker fails lint, `// partial` passes all but one test, anything else is a FakeToolRunner."""

    async def verify(self, rtl: Rtl, pack: TaskPack) -> VerifyResult:
        n = pack.test_count
        if "// lintbad" in rtl.text:
            self.verify_calls += 1
            log = "%Error: dut.v:3:1: syntax error, unexpected foo\n%Error: Exiting due to 1 error(s)\n"
            return VerifyResult(
                status="lint_fail", contract=ContractResult(ok=True),
                lint=LintResult(ok=False, errors=1, log=log, tool="fake lint"), duration_ms=3,
            )
        if "// partial" in rtl.text:
            self.verify_calls += 1
            case = SimCase(name="acc_case", inputs="x", expected="1", got="0", pass_=False, cycle=3)
            return VerifyResult(
                status="sim_fail", contract=ContractResult(ok=True), lint=LintResult(ok=True, warnings=0, tool="fake lint"),
                sim=SimResult(ok=False, total=n, passed=n - 1, failure_count=1, failures=[case], tool="fake sim"),
                duration_ms=7,
            )
        return await super().verify(rtl, pack)


def _script(ctx, texts, seen: list[dict], counts: dict[str, int]) -> None:
    roles = ctx.roles
    queue = list(texts)

    async def generate_rtl(llm, pack, plan, priority, **kw):
        counts["rtl_gen"] = counts.get("rtl_gen", 0) + 1
        seen.append(kw)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        meta = CallMeta("rtl_gen", "m", "r", "rtl_gen.v2.md@x", 100, 900, 5, 0.01, finish_reason="stop", reasoning_tokens=700)
        return RtlOut(text=item, reason="sample", meta=meta, raw=item)

    async def repair(*a, **k):
        counts["repair"] = counts.get("repair", 0) + 1
        return await roles.repair(*a, **k)

    ctx.roles = dataclasses.replace(roles, generate_rtl=generate_rtl, repair=repair)


def _with_samples(settings, k: int = 3):
    return settings.model_copy(update={"baseline": settings.baseline.model_copy(update={"samples": k})})


def _c1(run):
    return [e.candidate for e in run.events if isinstance(e, CandidateEvent) and e.candidate.id == "c1"][-1]


def _ref(pack: TaskPack) -> str:
    return pack.reference_rtl().replace("\r\n", "\n").strip("\n")


async def test_a_full_pass_among_the_samples_needs_no_repair(settings, mac4: TaskPack) -> None:
    ref = _ref(mac4)
    seen: list[dict] = []
    counts: dict[str, int] = {}
    run = await execute_fake_run(
        _with_samples(settings), mac4, mode="fast", tools=MarkerTools(),
        on_ctx=lambda ctx: _script(ctx, [ref + "\n// partial", ref + "\n// lintbad", ref], seen, counts),
    )
    try:
        assert run.error is None and run.outcome is not None and run.outcome.status == "done"
        assert counts == {"rtl_gen": 3}, counts
        assert seen == [{"temperature": 1.0, "top_p": 0.95}] * 3
        c1 = _c1(run)
        assert len(c1.attempts) == 1 and c1.fix_count == 0
        assert c1.attempts[0].verification.passed == c1.attempts[0].verification.total
        info = json.loads(await run.store.read_run_file(run.run_id, "candidates/c1/samples.json"))
        assert info["requested"] == 3 and info["returned"] == 3 and info["chosen"] == 2
        assert [s["lintOk"] for s in info["samples"]] == [True, False, True]
        assert info["samples"][0]["passed"] == mac4.test_count - 1
        assert info["samples"][0]["reasoningTokens"] == 700 and info["samples"][0]["finishReason"] == "stop"
        assert any("3 first versions" in e.text for e in run.events if isinstance(e, CaptionEvent))
    finally:
        await run.store.close()


async def test_the_highest_fraction_passed_continues_and_only_that_one_is_repaired(settings, mac4: TaskPack) -> None:
    ref = _ref(mac4)
    counts: dict[str, int] = {}
    run = await execute_fake_run(
        _with_samples(settings), mac4, mode="fast", tools=MarkerTools(),
        on_ctx=lambda ctx: _script(ctx, [ref + "\n// lintbad", ref + "\n// partial", ref + "\n// lintbad 2"], [], counts),
    )
    try:
        assert run.error is None and run.outcome is not None and run.outcome.status == "done"
        c1 = _c1(run)
        assert counts == {"rtl_gen": 3, "repair": 1}, counts
        assert "// partial" in c1.attempts[0].rtl and c1.attempts[0].by == "INIT"
        assert c1.attempts[1].by == "FIX" and c1.fix_count == 1
        assert c1.attempts[-1].verification.passed == c1.attempts[-1].verification.total
    finally:
        await run.store.close()


async def test_when_every_sample_fails_lint_the_repair_loop_still_runs(settings, mac4: TaskPack) -> None:
    ref = _ref(mac4)
    counts: dict[str, int] = {}
    run = await execute_fake_run(
        _with_samples(settings), mac4, mode="fast", tools=MarkerTools(),
        on_ctx=lambda ctx: _script(ctx, [ref + "\n// lintbad"] * 3, [], counts),
    )
    try:
        assert run.error is None and run.outcome is not None and run.outcome.status == "done"
        assert counts == {"rtl_gen": 3, "repair": 1}, counts
        info = json.loads(await run.store.read_run_file(run.run_id, "candidates/c1/samples.json"))
        assert not any(s["lintOk"] for s in info["samples"])
    finally:
        await run.store.close()


async def test_a_sample_that_fails_to_parse_does_not_sink_the_others(settings, mac4: TaskPack) -> None:
    ref = _ref(mac4)
    err = RoleOutputError("rtl_gen", "no block")
    run = await execute_fake_run(
        _with_samples(settings), mac4, mode="fast", tools=MarkerTools(),
        on_ctx=lambda ctx: _script(ctx, [err, ref, err], [], {}),
    )
    try:
        assert run.error is None and run.outcome is not None and run.outcome.status == "done"
        info = json.loads(await run.store.read_run_file(run.run_id, "candidates/c1/samples.json"))
        assert info["requested"] == 3 and info["returned"] == 1
        assert len(_c1(run).attempts) == 1
    finally:
        await run.store.close()


async def test_all_samples_failing_to_parse_ends_the_run_with_an_error(settings, mac4: TaskPack) -> None:
    err = RoleOutputError("rtl_gen", "no block")
    run = await execute_fake_run(
        _with_samples(settings), mac4, mode="fast", tools=MarkerTools(),
        on_ctx=lambda ctx: _script(ctx, [err, err, err], [], {}),
    )
    try:
        assert run.outcome is not None and run.outcome.status == "error"
    finally:
        await run.store.close()


async def test_one_sample_keeps_the_old_single_call_path(settings, mac4: TaskPack) -> None:
    seen: list[dict] = []
    counts: dict[str, int] = {}
    run = await execute_fake_run(
        _with_samples(settings, 1), mac4, mode="fast", tools=MarkerTools(),
        on_ctx=lambda ctx: _script(ctx, [_ref(mac4)], seen, counts),
    )
    try:
        assert run.outcome is not None and run.outcome.status == "done"
        assert counts == {"rtl_gen": 1} and seen == [{}]
    finally:
        await run.store.close()
