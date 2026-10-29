"""W14: profiles in CPU jobs, upgrade jobs (pipeline and cache), the upgrade route. Fakes only, no network, $0."""

import asyncio

import pytest
from fastapi.testclient import TestClient
from test_cpu_jobs import finish, setup

from verity.api.app import create_app
from verity.api.auth import Principal
from verity.api.routes import session_guard
from verity.config import CpuJobConfig
from verity.contracts import to_wire
from verity.contracts.cpu import CreateCpuJobRequest, UpgradeRequest
from verity.contracts.cpu_profile import BenchRow, CyclesByStall
from verity.core.errors import Conflict, Forbidden
from verity.cpu.upgrades import UPGRADES
from verity.runs.cpu_job import BLOCKS, UPGRADE_JOBS
from verity.store import SqliteStore
from verity.tools.bench import BenchResult

BLOCK_IDS = [p for p, _ in BLOCKS]
SIX = list(UPGRADE_JOBS["pipeline_2"].reused)


def req(goal="area"):
    return CreateCpuJobRequest(specId="rv32i_micro", goal=goal)


def suite(cycles, wait=0, fetch=0, data=0):
    """A passing three-benchmark suite of 100 instructions each."""
    rows = [
        BenchRow(name=n, instructions=100, cycles=cycles, cpi=cycles / 100, passed=True, self_check=1,
                 cycles_by_stall=CyclesByStall(busy=cycles - fetch - data, fetch=fetch, data=data))
        for n in ("crc32", "matmult-int", "edn")
    ]
    return BenchResult(status="pass", wait_cycles=wait, bench=rows)


async def passed_parent(tmp_path, cpu_packs, **updates):
    """A passed rv32i_micro job whose block winners are tagged with their run and whose blocks all report area and clock."""
    updates.setdefault("cpu_job", CpuJobConfig(improve_rounds=0))
    settings, store, runs = setup(tmp_path, cpu_packs, **updates)
    await store.open()
    runs.tag_winners = True
    for i, block in enumerate(BLOCK_IDS):
        runs.ppas[block] = [(100.0 + i, 1.0 + i / 10, 0.5)]
    # The integrated cores' own measures: their clock sets the benchmark time, not the slowest block's.
    runs.ppas["rv32i_core"] = [(900.0, 8.0, 1.0)]
    runs.ppas["rv32i_pipe2_core"] = [(880.0, 8.2, 1.0)]
    runs.ppas["rv32i_cache_core"] = [(1300.0, 7.9, 1.0)]
    view = await runs.cpu_jobs.create(req("speed"), session_id="owner", role="team")
    view = await finish(runs, view.id)
    assert view.status == "passed"
    return settings, store, runs, view


async def upgrade(runs, parent_id, upgrade_id, **kw):
    view = await runs.cpu_jobs.upgrade(
        parent_id, UpgradeRequest(upgradeId=upgrade_id), session_id=kw.pop("session_id", "owner"), role=kw.pop("role", "team"),
    )
    return view, await finish(runs, view.id)


# ---------------------------------------------------------------------------
# profiles in a plain CPU job
# ---------------------------------------------------------------------------
async def test_core_run_is_benched_at_both_memories_and_riscv_formal_is_stored(tmp_path, cpu_packs):
    _, store, runs, view = await passed_parent(tmp_path, cpu_packs)
    try:
        tools = runs._tools
        assert tools.bench_calls == [("rv32i_core", 0), ("rv32i_core", 3)] and tools.rvfi_calls == ["rv32i_core"]
        (v1,) = view.versions
        # Fast memory: 3 x 400 cycles over 3 x 100 instructions. Flash-like memory: 500 cycles each.
        assert v1.profile.cpi == pytest.approx(4.0) and v1.profile.wait_cycles == 0
        assert v1.flash_profile.cpi == pytest.approx(5.0) and v1.flash_profile.wait_cycles == 3
        assert v1.profile.cycles_by_stall.model_dump() == {"busy": 900, "fetch": 150, "data": 150}
        assert [r.name for r in v1.profile.bench] == ["crc32", "matmult-int", "edn"] and v1.profile.bench[0].cpi == 4.0
        # Area by block from the block winners' measures; the clock is the core run's measured clock; time at it.
        assert v1.profile.area_by_block_um2 == {b: 100.0 + i for i, b in enumerate(BLOCK_IDS)}
        assert v1.profile.clock_ns == pytest.approx(8.0)
        assert v1.profile.bench_time_us == pytest.approx(1200 * 8.0 / 1000)
        assert v1.profile_note is None
        for target in (v1, v1.core_evidence, view.core.evidence):
            assert (target.rvfi_passed, target.rvfi_total, target.rvfi_timeouts, target.rvfi_failed) == (3, 3, 0, [])
        wire = to_wire(view)
        assert wire["versions"][0]["profile"]["cyclesByStall"]["busy"] == 900
        assert wire["versions"][0]["flashProfile"]["waitCycles"] == 3 and wire["versions"][0]["rvfiPassed"] == 3
        assert wire["core"]["evidence"]["rvfiTotal"] == 3
        # Any later read keeps the numbers.
        again = await runs.cpu_jobs.get(view.id, session_id="owner", role="team")
        assert again.versions[0].profile == v1.profile and again.core.evidence.rvfi_passed == 3
    finally:
        await store.close()


async def test_a_bench_or_rvfi_that_cannot_run_is_not_measured_never_guessed(tmp_path, cpu_packs):
    settings, store, runs = setup(tmp_path, cpu_packs, cpu_job=CpuJobConfig(improve_rounds=0))
    await store.open()
    try:
        runs._tools.bench_results[0] = RuntimeError("docker fell over")
        runs._tools.bench_results[3] = BenchResult(status="timeout", wait_cycles=3, message="deadline")
        runs._tools.rvfi_error = RuntimeError("docker fell over")
        view = await runs.cpu_jobs.create(req(), session_id="owner", role="team")
        view = await finish(runs, view.id)
        # The core run passed, so the job passed. What was not measured is null with a fixed note.
        assert view.status == "passed"
        (v1,) = view.versions
        assert v1.profile is None and v1.flash_profile is None
        assert (v1.rvfi_passed, v1.rvfi_total, v1.rvfi_timeouts, v1.rvfi_failed) == (None, None, None, None)
        assert view.core.evidence.rvfi_total is None
        assert "benchmarks did not finish" in v1.profile_note and "riscv-formal could not run" in v1.profile_note
    finally:
        await store.close()


async def test_a_riscv_formal_failure_is_reported_but_never_gates_the_plain_job(tmp_path, cpu_packs):
    _, store, runs = setup(tmp_path, cpu_packs, cpu_job=CpuJobConfig(improve_rounds=0))
    await store.open()
    try:
        runs._tools.rvfi_checks = [("insn_add", "fail"), ("reg", "pass"), ("pc_fwd", "timeout")]
        view = await runs.cpu_jobs.create(req(), session_id="owner", role="team")
        view = await finish(runs, view.id)
        (v1,) = view.versions
        assert view.status == "passed"
        assert (v1.rvfi_passed, v1.rvfi_total, v1.rvfi_timeouts, v1.rvfi_failed) == (1, 3, 1, ["insn_add"])
    finally:
        await store.close()


async def test_flash_like_memory_is_optional_when_only_the_wait_bench_fails(tmp_path, cpu_packs):
    _, store, runs = setup(tmp_path, cpu_packs, cpu_job=CpuJobConfig(improve_rounds=0))
    await store.open()
    try:
        runs._tools.bench_results[3] = BenchResult(status="fail", wait_cycles=3, message="self-check")
        view = await finish(runs, (await runs.cpu_jobs.create(req(), session_id="owner", role="team")).id)
        (v1,) = view.versions
        assert v1.profile is not None and v1.flash_profile is None
        assert "flash-like memory did not finish" in v1.profile_note
    finally:
        await store.close()


async def test_improved_versions_are_profiled_too(tmp_path, cpu_packs):
    _, store, runs = setup(tmp_path, cpu_packs)
    for i, block in enumerate(BLOCK_IDS):
        runs.ppas[block] = [(100.0 + i, 1.0, 0.5)]
    runs.ppas["rv32i_ctl"] = [(500.0, 1.0, 0.5), (400.0, 1.0, 0.5)]
    await store.open()
    try:
        view = await finish(runs, (await runs.cpu_jobs.create(req(), session_id="owner", role="team")).id)
        assert [v.version for v in view.versions] == [1, 2]
        assert all(v.profile is not None and v.flash_profile is not None and v.rvfi_total == 3 for v in view.versions)
        # v2 carries the improved block's smaller area.
        assert view.versions[1].profile.area_by_block_um2["rv32i_ctl"] == 400.0
        assert view.versions[0].profile.area_by_block_um2["rv32i_ctl"] == 500.0
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# the pipeline upgrade job
# ---------------------------------------------------------------------------
async def test_pipeline_upgrade_reuses_the_six_winners_and_runs_only_pctl_and_the_core(tmp_path, cpu_packs):
    settings, store, runs, parent = await passed_parent(tmp_path, cpu_packs)
    try:
        saved = await store.get_cpu_job(parent.id)
        before = len(runs.calls)
        runs._tools.bench_calls.clear()
        # The upgraded core runs in 1.5 cycles per instruction, against 4.0 for the parent.
        runs._tools.bench_results[0] = suite(150)
        runs.ppas["rv32i_pctl"] = [(50.0, 0.8, 0.2)]
        started, child = await upgrade(runs, parent.id, "pipeline_2")
        assert started.status == "running" and started.spec_id == "rv32i_pipe2"
        assert started.parent_job_id == parent.id and started.upgrade_id == "pipeline_2"

        assert child.status == "passed" and child.spec_id == "rv32i_pipe2" and child.goal == "speed"
        calls = runs.calls[before:]
        assert [c["preset_id"] for c in calls][0] == "rv32i_pctl" and len(calls) == 2
        assert calls[0]["mode"] == "full" and calls[1]["mode"] == "fast"
        # Seven rows: the six reused ones (no run, the parent's run id and evidence) and the new one.
        assert [b.block_id for b in child.blocks] == [*SIX, "rv32i_pctl"]
        reused = child.blocks[:6]
        parent_rows = {b.block_id: b for b in parent.blocks}
        assert all(b.reused and b.status == "passed" and b.attempts == 0 for b in reused)
        assert all(b.run_id == parent_rows[b.block_id].run_id and b.evidence == parent_rows[b.block_id].evidence for b in reused)
        assert all(b.summary.id == b.run_id for b in reused)
        pctl = child.blocks[6]
        assert not pctl.reused and pctl.status == "passed" and pctl.attempts == 1 and pctl.research == "skipped"
        assert child.spent_usd == pytest.approx(0.2)  # the parent's runs are not counted again

        # The derived pack: blocks.v is the parent's six saved winners plus the pctl winner.
        pack = runs._packs[child.core.pack_id]
        pack.reverify()
        assert pack.top == "rv32i_pipe2_core" and pack.root.parent == settings.data_dir / "cpu_packs" / child.id
        pctl_text = (await store.read_run_file(pctl.run_id, "final/winner.v")).decode()
        expected = "\n\n".join([*(saved.winners[b].rstrip() for b in SIX), pctl_text.rstrip()]) + "\n"
        assert (pack.root / "blocks.v").read_text() == expected
        assert saved.winners["rv32i_ctl"] not in expected

        # Wait 0 only (the pipelined core cannot run with waits), then riscv-formal.
        assert runs._tools.bench_calls == [("rv32i_pipe2_core", 0)] and runs._tools.rvfi_calls[-1] == "rv32i_pipe2_core"
        (v1,) = child.versions
        assert v1.profile.cpi == pytest.approx(1.5) and v1.flash_profile is None
        assert "memory wait cycles" in v1.profile_note
        assert list(v1.profile.area_by_block_um2) == [*SIX, "rv32i_pctl"] and v1.profile.clock_ns == pytest.approx(8.2)
        assert (v1.rvfi_passed, v1.rvfi_total) == (3, 3)

        # Deltas against the parent's CPU v1, computed in code from the two profiles.
        cmp = child.comparison
        assert cmp.fast.wait_cycles == 0 and cmp.fast.parent.cpi == pytest.approx(4.0) and cmp.fast.child.cpi == pytest.approx(1.5)
        assert cmp.fast.cpi_delta_pct == pytest.approx(-62.5)
        assert cmp.fast.bench[0].name == "crc32" and cmp.fast.bench[0].parent_cycles == 400 and cmp.fast.bench[0].cycles == 150
        assert cmp.fast.bench[0].cycles_delta_pct == pytest.approx(-62.5) and cmp.fast.bench[0].cpi == pytest.approx(1.5)
        assert cmp.flash is None
        parent_area = sum(100.0 + i for i in range(7))
        child_area = sum(100.0 + i for i in range(6)) + 50.0
        assert cmp.parent_area_um2 == pytest.approx(parent_area) and cmp.area_um2 == pytest.approx(child_area)
        assert cmp.area_delta_pct == pytest.approx((child_area / parent_area - 1) * 100)
        assert cmp.parent_clock_ns == pytest.approx(8.0) and cmp.clock_ns == pytest.approx(8.2)
        assert cmp.fast.bench_time_ratio == pytest.approx((450 * 8.2) / (1200 * 8.0))

        wire = to_wire(child)
        assert wire["specId"] == "rv32i_pipe2" and wire["parentJobId"] == parent.id and wire["upgradeId"] == "pipeline_2"
        assert wire["blocks"][0]["reused"] is True and wire["comparison"]["fast"]["cpiDeltaPct"] == pytest.approx(-62.5)
        assert wire["comparison"]["flash"] is None
        # No improve rounds in a child job, and the parent is untouched.
        assert [v.version for v in child.versions] == [1]
        assert (await runs.cpu_jobs.get(parent.id, session_id="owner", role="team")).status == "passed"
    finally:
        await store.close()


@pytest.mark.parametrize("checks,text,status", [
    ([("insn_add", "fail"), ("reg", "pass"), ("insn_sub", "fail")], "riscv-formal failed: insn_add, insn_sub", "failed"),
    ([], "riscv-formal could not run", "failed"),
    ([("a", "error"), ("b", "error")], "riscv-formal could not run", "failed"),
    ([("insn_add", "pass"), ("reg", "timeout"), ("pc_fwd", "timeout")], "2 of 3 checks not proven within the time limit", "passed"),
])
async def test_pipeline_job_riscv_formal_gate(tmp_path, cpu_packs, checks, text, status):
    _, store, runs, parent = await passed_parent(tmp_path, cpu_packs)
    try:
        runs._tools.rvfi_checks = checks
        _, child = await upgrade(runs, parent.id, "pipeline_2")
        assert child.status == status and text in child.message
        # The core's tests passed either way; the numbers are still shown.
        assert child.core.status == "passed" and child.versions[0].profile is not None and child.comparison is not None
        if status == "passed":
            assert child.versions[0].rvfi_timeouts == 2
    finally:
        await store.close()


async def test_a_pipeline_core_that_fails_its_run_fails_the_job(tmp_path, cpu_packs):
    _, store, runs, parent = await passed_parent(tmp_path, cpu_packs)
    try:
        original = runs.start_cpu_run
        calls_before = len(runs._tools.rvfi_calls)

        async def fail_core(**kwargs):
            if kwargs["preset_id"].startswith("rv32i_pipe2_core_"):
                runs.failures[kwargs["preset_id"]] = 1
            return await original(**kwargs)

        runs.start_cpu_run = fail_core
        _, child = await upgrade(runs, parent.id, "pipeline_2")
        assert child.status == "failed" and child.core.status == "failed" and child.comparison is None
        assert len(runs._tools.rvfi_calls) == calls_before
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# the cache upgrade job
# ---------------------------------------------------------------------------
async def test_cache_upgrade_wraps_the_parent_core_and_reports_both_memories(tmp_path, cpu_packs):
    _, store, runs, parent = await passed_parent(tmp_path, cpu_packs)
    try:
        saved = await store.get_cpu_job(parent.id)
        before = len(runs.calls)
        runs._tools.bench_calls.clear()
        # Fast memory is unchanged; flash-like memory drops from 500 to 420 cycles (fetch and data waits shrink).
        runs._tools.bench_results[3] = suite(420, wait=3, fetch=60, data=40)
        runs.ppas["rv32i_cache"] = [(300.0, 1.2, 0.4)]
        _, child = await upgrade(runs, parent.id, "cache_16")
        assert child.status == "passed" and child.spec_id == "rv32i_cached" and child.upgrade_id == "cache_16"
        calls = runs.calls[before:]
        assert calls[0]["preset_id"] == "rv32i_cache" and len(calls) == 2
        assert [b.block_id for b in child.blocks] == [*BLOCK_IDS, "rv32i_cache"]
        assert all(b.reused for b in child.blocks[:7]) and not child.blocks[7].reused

        pack = runs._packs[child.core.pack_id]
        cache_text = (await store.read_run_file(child.blocks[7].run_id, "final/winner.v")).decode()
        assert pack.top == "rv32i_cache_core"
        # Seven block winners, the parent's core winner (it defines rv32i_core), then the cache.
        assert saved.core_text.startswith("module rv32i_core;")
        expected = "\n\n".join([*(saved.winners[b].rstrip() for b in BLOCK_IDS), saved.core_text.rstrip(), cache_text.rstrip()]) + "\n"
        assert (pack.root / "blocks.v").read_text() == expected

        assert runs._tools.bench_calls == [("rv32i_cache_core", 0), ("rv32i_cache_core", 3)]
        assert runs._tools.rvfi_calls[-1] == "rv32i_cache_core"
        (v1,) = child.versions
        assert v1.profile.wait_cycles == 0 and v1.flash_profile.wait_cycles == 3
        assert v1.flash_profile.cpi == pytest.approx(4.2) and "rv32i_cache" in v1.profile.area_by_block_um2
        cmp = child.comparison
        assert cmp.fast.child.cpi == pytest.approx(4.0) and cmp.fast.cpi_delta_pct == pytest.approx(0.0)
        assert cmp.flash.wait_cycles == 3 and cmp.flash.parent.cpi == pytest.approx(5.0) and cmp.flash.child.cpi == pytest.approx(4.2)
        assert cmp.flash.cpi_delta_pct == pytest.approx(-16.0)
        assert cmp.flash.bench[2].name == "edn" and cmp.flash.bench[2].cycles_delta_pct == pytest.approx(-16.0)
        assert cmp.area_um2 == pytest.approx(sum(100.0 + i for i in range(7)) + 300.0)
        assert cmp.parent_clock_ns == pytest.approx(8.0) and cmp.clock_ns == pytest.approx(7.9)
        assert (v1.rvfi_passed, v1.rvfi_total) == (3, 3)
    finally:
        await store.close()


async def test_cache_job_riscv_formal_is_reported_never_a_gate(tmp_path, cpu_packs):
    _, store, runs, parent = await passed_parent(tmp_path, cpu_packs)
    try:
        runs._tools.rvfi_checks = [("insn_add", "fail"), ("reg", "pass")]
        _, child = await upgrade(runs, parent.id, "cache_16")
        assert child.status == "passed" and child.versions[0].rvfi_failed == ["insn_add"]
    finally:
        await store.close()


async def test_a_child_job_uses_the_parents_v1_winners_even_after_improve_rounds(tmp_path, cpu_packs):
    _, store, runs = setup(tmp_path, cpu_packs)
    runs.tag_winners = True
    for i, block in enumerate(BLOCK_IDS):
        runs.ppas[block] = [(100.0 + i, 1.0, 0.5)]
    runs.ppas["rv32i_rf"] = [(900.0, 1.0, 0.5), (800.0, 1.0, 0.5)]
    await store.open()
    try:
        parent = await finish(runs, (await runs.cpu_jobs.create(req(), session_id="owner", role="team")).id)
        assert [v.version for v in parent.versions] == [1, 2] and parent.versions[1].changed_block == "rv32i_rf"
        v1_rf = (await store.read_run_file(next(b for b in parent.blocks if b.block_id == "rv32i_rf").run_id, "final/winner.v")).decode()
        v2_rf = (await store.read_run_file(parent.versions[1].block_run_id, "final/winner.v")).decode()
        assert v1_rf != v2_rf
        _, child = await upgrade(runs, parent.id, "pipeline_2")
        blocks = (runs._packs[child.core.pack_id].root / "blocks.v").read_text()
        assert v1_rf.rstrip() in blocks and v2_rf.rstrip() not in blocks
        # Compared with CPU v1: the same winners, so the numbers are like for like.
        assert child.comparison.fast.parent == parent.versions[0].profile
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# admission and refusals
# ---------------------------------------------------------------------------
async def test_upgrade_refusals_have_plain_reasons_and_cost_nothing(tmp_path, cpu_packs):
    _, store, runs, parent = await passed_parent(tmp_path, cpu_packs)
    try:
        credits = await runs.get_credits("owner")

        async def refused(job_id, upgrade_id, **kw):
            with pytest.raises((Conflict, Forbidden)) as exc:
                await runs.cpu_jobs.upgrade(
                    job_id, UpgradeRequest(upgradeId=upgrade_id), session_id=kw.get("session_id", "owner"), role=kw.get("role", "team"),
                )
            return exc.type, str(exc.value)

        assert await refused(parent.id, "rv32e") == (Conflict, "Not buildable yet")
        assert await refused(parent.id, "no_such_upgrade") == (Conflict, "Not buildable yet")
        assert await refused(parent.id, "pipeline_2", session_id="other", role="user") == (
            Forbidden, "This CPU job belongs to another account.",
        )
        # Signed in is enough; only a visitor with no session is refused.
        assert await refused(parent.id, "pipeline_2", role="anonymous") == (Forbidden, "Sign in to build a CPU.")
        # A parent that is still running, failed or stopped is not a passed job.
        job = await store.get_cpu_job(parent.id)
        for status in ("running", "improving", "failed", "stopped"):
            job.view.status = status
            await store.save_cpu_job(job)
            kind, text = await refused(parent.id, "pipeline_2")
            assert kind is Conflict and "has not passed" in text
        job.view.status = "passed"
        # A job without saved winners (made before upgrades existed) cannot be upgraded.
        saved = dict(job.winners)
        job.winners = {}
        await store.save_cpu_job(job)
        assert "no saved block winners" in (await refused(parent.id, "cache_16"))[1]
        job.winners = saved
        await store.save_cpu_job(job)
        assert await runs.get_credits("owner") == credits
        # An upgrade job is not itself upgraded.
        _, child = await upgrade(runs, parent.id, "pipeline_2")
        assert await refused(child.id, "cache_16") == (Conflict, "Only a passed RV32I core can be upgraded.")
        assert await runs.get_credits("owner") == credits  # an upgrade takes no credit
    finally:
        await store.close()


async def test_a_signed_in_visitor_with_no_credits_upgrades_and_is_never_charged(tmp_path, cpu_packs):
    _, store, runs, parent = await passed_parent(tmp_path, cpu_packs)
    try:
        await store.set_credits("owner", 0)
        await upgrade(runs, parent.id, "pipeline_2")
        assert await runs.get_credits("owner") == 0
        started = await runs.cpu_jobs.upgrade(parent.id, UpgradeRequest(upgradeId="cache_16"), session_id="owner", role="user")
        assert started.status == "running" and started.upgrade_id == "cache_16"
        await finish(runs, started.id)
        assert await runs.get_credits("owner") == 0
    finally:
        await store.close()


def test_every_buildable_catalog_option_has_a_job_recipe():
    assert {u.id for u in UPGRADES if u.buildable} == set(UPGRADE_JOBS)


def test_upgrade_route_answers_202_409_and_403(tmp_path, cpu_packs):
    settings, store, runs = setup(tmp_path, cpu_packs, cpu_job=CpuJobConfig(improve_rounds=0))
    app = create_app(settings, runs, startup=[store.open], shutdown=[store.close])
    principal = Principal(sid="owner", role="team")
    app.dependency_overrides[session_guard] = lambda: principal
    with TestClient(app) as client:
        created = client.post("/cpu-jobs", json={"specId": "rv32i_micro", "goal": "speed"})
        assert created.status_code == 201
        job_id = created.json()["id"]
        url = f"/cpu-jobs/{job_id}/upgrade"
        # The parent is still running (or has just ended): until it has passed, the answer is 409 with a plain reason.
        if client.get(f"/cpu-jobs/{job_id}").json()["status"] != "passed":
            result = client.post(url, json={"upgradeId": "pipeline_2"})
            assert result.status_code == 409 and "has not passed" in result.json()["error"]

        async def settle():
            await asyncio.wait_for(runs.cpu_jobs._tasks[job_id], 10)

        client.portal.call(settle)
        assert client.get(f"/cpu-jobs/{job_id}").json()["status"] == "passed"

        # 409 for a non-buildable id (and an unknown one), 422 for a bad body.
        for bad in ("rv32e", "ext_m", "made_up"):
            result = client.post(url, json={"upgradeId": bad})
            assert result.status_code == 409 and result.json()["error"] == "Not buildable yet"
        assert client.post(url, json={}).status_code == 422

        # Another account: 403 (job lookup). The owner with no session at all: 403.
        principal = Principal(sid="other", role="user")
        assert client.post(url, json={"upgradeId": "pipeline_2"}).status_code == 403
        principal = Principal(sid="owner", role="anonymous", anonymous=True)
        refused = client.post(url, json={"upgradeId": "pipeline_2"})
        assert refused.status_code == 403 and refused.json()["error"] == "Sign in to build a CPU."
        # An admin who is not the owner may upgrade, with no credits and without being charged.
        principal = Principal(sid="empty", role="team")
        client.portal.call(store.set_credits, "empty", 0)
        result = client.post(url, json={"upgradeId": "pipeline_2"})
        assert result.status_code == 202
        client.portal.call(finish, runs, result.json()["id"])
        assert client.portal.call(store.get_credits, "empty") == 0

        # 202 with the new job's view.
        principal = Principal(sid="owner", role="team")
        accepted = client.post(url, json={"upgradeId": "cache_16"})
        assert accepted.status_code == 202
        body = accepted.json()
        assert body["specId"] == "rv32i_cached" and body["parentJobId"] == job_id and body["upgradeId"] == "cache_16"
        assert body["status"] == "running" and body["comparison"] is None and len(body["blocks"]) == 8
        assert sum(b["reused"] for b in body["blocks"]) == 7
        assert client.get(f"/cpu-jobs/{body['id']}").json()["parentJobId"] == job_id

        async def settle_child():
            await asyncio.wait_for(runs.cpu_jobs._tasks[body["id"]], 10)

        client.portal.call(settle_child)
        assert client.get(f"/cpu-jobs/{body['id']}").json()["status"] == "passed"


# ---------------------------------------------------------------------------
# restart
# ---------------------------------------------------------------------------
async def test_restart_keeps_the_parent_upgradable_and_stops_a_running_child(tmp_path, cpu_packs):
    settings, store, runs, parent = await passed_parent(tmp_path, cpu_packs)
    # A child cut off mid-run is stopped cleanly after a restart, like a plain job.
    started = await runs.cpu_jobs.upgrade(parent.id, UpgradeRequest(upgradeId="pipeline_2"), session_id="owner", role="team")
    await runs.cpu_jobs.shutdown()
    cut = await store.get_cpu_job(started.id)
    assert cut.view.status == "stopped" and not runs.cpu_jobs._tasks
    cut.view.status, cut.view.finished_at = "integrating", None
    await store.save_cpu_job(cut)
    await store.close()

    store2 = SqliteStore(settings)
    await store2.open()
    try:
        runs2 = type(runs)(settings, store2, cpu_packs)
        runs2.calls = list(runs.calls)  # run ids continue where the first server stopped
        await runs2.cpu_jobs.start()
        recovered = await runs2.cpu_jobs.get(started.id, session_id="owner", role="team")
        assert recovered.status == "stopped" and recovered.message == "server restarted" and recovered.parent_job_id == parent.id
        assert all(b.status == "passed" for b in recovered.blocks[:6]) and recovered.blocks[0].reused
        # The saved winners came back from SQLite, so the parent can still be upgraded.
        runs2.tag_winners = True
        _, child = await upgrade(runs2, parent.id, "cache_16")
        assert child.status == "passed" and child.comparison.fast.parent is not None
    finally:
        await store2.close()
