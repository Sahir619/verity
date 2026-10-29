"""Winners saved before the core, and the core retry of a failed job. Fakes only, no network, $0."""

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from test_cpu_jobs import finish, setup

from verity.api.app import create_app
from verity.api.auth import Principal
from verity.api.routes import session_guard
from verity.config import CpuJobConfig
from verity.contracts import Candidate, to_wire
from verity.contracts.cpu import CreateCpuJobRequest
from verity.contracts.events import PPA
from verity.core.errors import Conflict, Forbidden, NotFound
from verity.runs.cpu_job import BLOCKS

BLOCK_IDS = [p for p, _ in BLOCKS]
CORE_FAILED = "Core run ended without a passing winner."


def req(thread_id=None):
    return CreateCpuJobRequest(specId="rv32i_micro", goal="speed", threadId=thread_id)


def fail_cores(runs):
    """Every derived core pack fails both of its tries. Returns the original start, to put back."""
    original = runs.start_cpu_run

    async def fail_core(**kwargs):
        if kwargs["preset_id"].startswith("rv32i_core_"):
            runs.failures[kwargs["preset_id"]] = 2
        return await original(**kwargs)

    runs.start_cpu_run = fail_core
    return original


def prepare(runs):
    runs.tag_winners = True
    for i, block in enumerate(BLOCK_IDS):
        runs.ppas[block] = [(100.0 + i, 1.0 + i / 10, 0.5)]
    runs.ppas["rv32i_core"] = [(900.0, 8.0, 1.0)]


async def failed_job(tmp_path, cpu_packs, thread_id=None, **updates):
    """A job whose seven blocks all passed and whose core failed both tries. The core passes again from here on."""
    updates.setdefault("cpu_job", CpuJobConfig(improve_rounds=0))
    settings, store, runs = setup(tmp_path, cpu_packs, **updates)
    await store.open()
    prepare(runs)
    original = fail_cores(runs)
    view = await runs.cpu_jobs.create(req(thread_id), session_id="owner", role="team")
    view = await finish(runs, view.id)
    assert view.status == "failed" and view.message == CORE_FAILED and view.core.status == "failed"
    assert all(b.status == "passed" for b in view.blocks)
    runs.start_cpu_run = original
    return settings, store, runs, view


async def retry(runs, job_id, **kw):
    view = await runs.cpu_jobs.retry(job_id, session_id=kw.pop("session_id", "owner"), role=kw.pop("role", "team"))
    return view, await finish(runs, view.id)


# ---------------------------------------------------------------------------
# winners are kept before the core runs
# ---------------------------------------------------------------------------
async def test_winners_and_measures_are_saved_before_the_core_so_a_failed_core_keeps_them(tmp_path, cpu_packs):
    _, store, runs, view = await failed_job(tmp_path, cpu_packs)
    try:
        saved = await store.get_cpu_job(view.id)
        assert set(saved.winners) == set(BLOCK_IDS) and set(saved.measures) == set(BLOCK_IDS)
        assert saved.measures["rv32i_rf"].area_um2 == 105.0 and saved.measures["rv32i_rf"].run_id.startswith("r_")
        assert all(f"module {p};" in saved.winners[p] for p in BLOCK_IDS)
        assert saved.core_text is None  # only a passed core saves its text
        assert sum(c["preset_id"].startswith("rv32i_core_") for c in runs.calls) == 2  # the core got its two tries
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# the retry job
# ---------------------------------------------------------------------------
async def test_retry_runs_only_the_core_reusing_the_blocks_and_keeps_the_chat(tmp_path, cpu_packs):
    thread = str(uuid.uuid4())
    settings, store, runs, failed = await failed_job(tmp_path, cpu_packs, thread_id=thread)
    try:
        saved = await store.get_cpu_job(failed.id)
        assert saved.thread_id == thread
        credits, before = await runs.get_credits("owner"), len(runs.calls)
        started, child = await retry(runs, failed.id)
        assert started.status == "running" and started.retry_of == failed.id and started.parent_job_id is None
        assert child.status == "passed" and child.spec_id == "rv32i_micro" and child.goal == "speed"
        assert child.upgrade_id is None and child.retry_of == failed.id

        # No research and no block run: exactly one core run.
        calls = runs.calls[before:]
        assert len(calls) == 1 and calls[0]["preset_id"].startswith("rv32i_core_") and calls[0]["mode"] == "fast"
        assert calls[0]["thread_id"] == thread
        assert await runs.get_credits("owner") == credits  # a retry takes no credit

        # Seven reused rows: the failed job's run ids and evidence, no attempts.
        rows = {b.block_id: b for b in failed.blocks}
        assert [b.block_id for b in child.blocks] == BLOCK_IDS
        assert all(b.reused and b.status == "passed" and b.attempts == 0 for b in child.blocks)
        assert all(b.run_id == rows[b.block_id].run_id and b.evidence == rows[b.block_id].evidence for b in child.blocks)
        assert child.spent_usd == pytest.approx(0.1)  # the failed job's runs are not counted again

        # The derived pack is the failed job's winners, in contract order.
        pack = runs._packs[child.core.pack_id]
        pack.reverify()
        expected = "\n\n".join(saved.winners[p].rstrip() for p in BLOCK_IDS) + "\n"
        assert (pack.root / "blocks.v").read_text() == expected
        assert pack.root.parent == settings.data_dir / "cpu_packs" / child.id

        # A retry is a plain job in every other way: v1 saved, CPU v1 profiled, and the chat is the failed job's.
        again = await store.get_cpu_job(child.id)
        assert again.thread_id == thread and again.winners == saved.winners and again.measures == saved.measures
        assert again.core_text and [v.version for v in child.versions] == [1]
        assert to_wire(child)["retryOf"] == failed.id
        # The failed job is untouched.
        assert (await runs.cpu_jobs.get(failed.id, session_id="owner", role="team")).status == "failed"
    finally:
        await store.close()


async def test_a_retry_by_another_admin_does_not_take_the_owners_chat(tmp_path, cpu_packs):
    _, store, runs, failed = await failed_job(tmp_path, cpu_packs, thread_id=str(uuid.uuid4()))
    try:
        _, child = await retry(runs, failed.id, session_id="admin2", role="team")
        assert child.status == "passed"
        assert (await store.get_cpu_job(child.id)).thread_id is None
    finally:
        await store.close()


async def test_the_improve_rounds_follow_a_retried_core_as_usual(tmp_path, cpu_packs):
    from test_cpu_improve import FakeResearch

    _, store, runs = setup(tmp_path, cpu_packs)
    runs._research = FakeResearch()
    await store.open()
    try:
        prepare(runs)
        runs.ppas["rv32i_ctl"] = [(500.0, 3.0, 0.5), (400.0, 2.0, 0.5)]
        original = fail_cores(runs)
        failed = await finish(runs, (await runs.cpu_jobs.create(req(), session_id="owner", role="team")).id)
        assert failed.status == "failed" and failed.versions == []
        runs.start_cpu_run = original
        before = len(runs.calls)
        _, child = await retry(runs, failed.id)
        assert child.status == "passed" and [v.version for v in child.versions] == [1, 2]
        assert child.versions[1].changed_block == "rv32i_ctl"
        kinds = ["core" if c["preset_id"].startswith("rv32i_core_") else c["preset_id"] for c in runs.calls[before:]]
        assert kinds[:3] == ["core", "rv32i_ctl", "core"] and set(kinds[3:]) <= {"rv32i_ctl"}  # a third round finds nothing better
        # No new research, but an improve run reads the block's research as in a plain job.
        assert len(runs._research.requests) == 7
        improve = [c for c in runs.calls[before:] if c["preset_id"] == "rv32i_ctl"]
        assert improve and all(c["research_id"] == "rs_rv32i_ctl" and c["candidate_multiplier"] == 2 for c in improve)
    finally:
        await store.close()


async def test_a_failed_retry_can_be_retried_again(tmp_path, cpu_packs):
    _, store, runs, failed = await failed_job(tmp_path, cpu_packs)
    try:
        original = fail_cores(runs)
        _, second = await retry(runs, failed.id)
        assert second.status == "failed" and second.message == CORE_FAILED and second.retry_of == failed.id
        assert set((await store.get_cpu_job(second.id)).winners) == set(BLOCK_IDS)
        runs.start_cpu_run = original
        _, third = await retry(runs, second.id)
        assert third.status == "passed" and third.retry_of == second.id and all(b.reused for b in third.blocks)
    finally:
        await store.close()


async def test_winners_are_rebuilt_from_the_block_runs_when_the_job_saved_none(tmp_path, cpu_packs):
    """A job that failed before winners were saved early: rebuilt with the rules block() uses, v1 fallback included."""
    settings, store, runs = setup(tmp_path, cpu_packs, cpu_job=CpuJobConfig(improve_rounds=0))
    await store.open()
    try:
        prepare(runs)
        runs.eqys["rv32i_alu"] = ["inconclusive"]

        async def add_v1(run_id, run):
            if run.preset_id != "rv32i_alu":
                return
            await store.upsert_candidate(run_id, Candidate(
                id="c0", label="v1", round=0, created_by="INIT", reason="fixture", rtl="", status="pass", is_baseline=True,
                ppa=PPA(area_um2=500.0, clock_period_ns=2.0, power_mw=0.5, pdk="fixture"),
            ))
            await store.write_run_file(run_id, "candidates/c0/attempt-1.v", b"module rv32i_alu; endmodule // v1\n", "text/x-verilog")
            await store.write_run_file(run_id, "candidates/c0/attempt-1.verify.json", b'{"status": "pass"}', "application/json")

        runs.after_run = add_v1
        original = fail_cores(runs)
        failed = await finish(runs, (await runs.cpu_jobs.create(req(), session_id="owner", role="team")).id)
        assert failed.status == "failed" and next(b for b in failed.blocks if b.block_id == "rv32i_alu").used_version == "v1"
        runs.start_cpu_run = original
        runs.after_run = None
        job = await store.get_cpu_job(failed.id)
        winners, measures = dict(job.winners), dict(job.measures)
        assert "// v1" in winners["rv32i_alu"] and measures["rv32i_alu"].area_um2 == 500.0
        job.winners, job.measures = {}, {}
        await store.save_cpu_job(job)

        _, child = await retry(runs, failed.id)
        assert child.status == "passed"
        rebuilt = await store.get_cpu_job(child.id)
        assert rebuilt.winners == winners and rebuilt.measures == measures
        assert "endmodule // v1" in (runs._packs[child.core.pack_id].root / "blocks.v").read_text()
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# refusals
# ---------------------------------------------------------------------------
async def test_retry_refusals_have_plain_reasons_and_cost_nothing(tmp_path, cpu_packs):
    _, store, runs, failed = await failed_job(tmp_path, cpu_packs)
    try:
        credits, calls = await runs.get_credits("owner"), len(runs.calls)

        async def refused(job_id=failed.id, **kw):
            with pytest.raises((Conflict, Forbidden, NotFound)) as exc:
                await runs.cpu_jobs.retry(job_id, session_id=kw.get("session_id", "owner"), role=kw.get("role", "team"))
            return exc.type, str(exc.value)

        async def change(**fields):
            job = await store.get_cpu_job(failed.id)
            for name, value in fields.items():
                setattr(job.view, name, value)
            await store.save_cpu_job(job)

        assert (await refused("cpu_" + "0" * 32))[0] is NotFound
        assert await refused(session_id="other", role="user") == (Forbidden, "This CPU job belongs to another account.")
        # Signed in is enough; only a visitor with no session is refused.
        assert await refused(role="anonymous") == (Forbidden, "Sign in to build a CPU.")

        # Only a failed job (or one stopped before its core passed: tested below).
        for status in ("running", "integrating", "improving", "passed"):
            await change(status=status)
            kind, text = await refused()
            assert kind is Conflict and text == "A core retry starts from a failed CPU job. This one has not failed."
        await change(status="failed")

        # Only a plain RV32I core job.
        await change(upgrade_id="pipeline_2")
        assert await refused() == (Conflict, "Only a plain RV32I core job can retry its core.")
        await change(upgrade_id=None, spec_id="rv32i_pipe2")
        assert (await refused())[1] == "Only a plain RV32I core job can retry its core."
        await change(spec_id="rv32i_micro")

        # All seven blocks must have passed.
        job = await store.get_cpu_job(failed.id)
        job.view.blocks[2].status = "failed"
        await store.save_cpu_job(job)
        assert await refused() == (Conflict, "A core retry needs all 7 blocks passed. This job has a block that did not pass.")
        job.view.blocks[2].status = "passed"

        # No saved winners and a block that cannot be rebuilt.
        saved = dict(job.winners), dict(job.measures)
        job.winners, job.measures = {}, {}
        job.view.blocks[2].used_version = "v1"  # no baseline candidate in this run
        await store.save_cpu_job(job)
        assert await refused() == (Conflict, "This job's blocks cannot be reused.")
        job.view.blocks[2].used_version, job.view.blocks[2].run_id = None, None
        await store.save_cpu_job(job)
        assert (await refused())[1] == "This job's blocks cannot be reused."
        job.winners, job.measures = saved
        await store.save_cpu_job(job)

        assert await runs.get_credits("owner") == credits and len(runs.calls) == calls
        # Nothing wrong and no credit left: a signed-in visitor retries all the same, and nothing is charged.
        await store.set_credits("owner", 0)
        started = await runs.cpu_jobs.retry(failed.id, session_id="owner", role="user")
        assert (await finish(runs, started.id)).status == "passed"
        assert await runs.get_credits("owner") == 0 and len(runs.calls) == calls + 1
    finally:
        await store.close()


async def test_a_second_press_while_a_retry_runs_is_refused(tmp_path, cpu_packs):
    _, store, runs, failed = await failed_job(tmp_path, cpu_packs)
    try:
        gate = asyncio.Event()
        original = runs.start_cpu_run

        async def gated(**kwargs):
            if kwargs["preset_id"].startswith("rv32i_core_"):
                await gate.wait()
            return await original(**kwargs)

        runs.start_cpu_run = gated
        first = await runs.cpu_jobs.retry(failed.id, session_id="owner", role="team")
        credits = await runs.get_credits("owner")
        with pytest.raises(Conflict, match="A core retry is already running."):
            await runs.cpu_jobs.retry(failed.id, session_id="owner", role="team")
        assert await runs.get_credits("owner") == credits
        gate.set()
        assert (await finish(runs, first.id)).status == "passed"
    finally:
        await store.close()


def test_retry_route_answers_202_409_403_and_404(tmp_path, cpu_packs):
    settings, store, runs = setup(tmp_path, cpu_packs, cpu_job=CpuJobConfig(improve_rounds=0))
    prepare(runs)
    app = create_app(settings, runs, startup=[store.open], shutdown=[store.close])
    principal = Principal(sid="owner", role="team")
    app.dependency_overrides[session_guard] = lambda: principal
    original = fail_cores(runs)
    gate = asyncio.Event()

    async def settle(job_id):
        await asyncio.wait_for(runs.cpu_jobs._tasks[job_id], 10)

    async def release():
        gate.set()

    with TestClient(app) as client:
        failed_id = client.post("/cpu-jobs", json={"specId": "rv32i_micro", "goal": "speed"}).json()["id"]
        client.portal.call(settle, failed_id)
        assert client.get(f"/cpu-jobs/{failed_id}").json()["status"] == "failed"
        url = f"/cpu-jobs/{failed_id}/retry"
        runs.start_cpu_run = original

        # Another account: 403. The owner with no session at all: 403. An unknown job: 404.
        principal = Principal(sid="other", role="user")
        assert client.post(url).status_code == 403
        principal = Principal(sid="owner", role="anonymous", anonymous=True)
        refused = client.post(url)
        assert refused.status_code == 403 and refused.json()["error"] == "Sign in to build a CPU."
        principal = Principal(sid="owner", role="team")
        assert client.post("/cpu-jobs/cpu_" + "0" * 32 + "/retry").status_code == 404

        # A job that has not failed: 409 with a plain reason.
        passed_id = client.post("/cpu-jobs", json={"specId": "rv32i_micro", "goal": "speed"}).json()["id"]
        client.portal.call(settle, passed_id)
        assert client.get(f"/cpu-jobs/{passed_id}").json()["status"] == "passed"
        result = client.post(f"/cpu-jobs/{passed_id}/retry")
        assert result.status_code == 409 and "has not failed" in result.json()["error"]

        # 202 with the new job's view; a second press while it runs is 409.
        principal = Principal(sid="owner", role="team")

        async def gated(**kwargs):
            if kwargs["preset_id"].startswith("rv32i_core_"):
                await gate.wait()
            return await original(**kwargs)

        runs.start_cpu_run = gated
        accepted = client.post(url)
        assert accepted.status_code == 202
        body = accepted.json()
        assert body["retryOf"] == failed_id and body["specId"] == "rv32i_micro" and body["status"] == "running"
        assert len(body["blocks"]) == 7 and all(b["reused"] and b["status"] == "passed" for b in body["blocks"])
        second = client.post(url)
        assert second.status_code == 409 and second.json()["error"] == "A core retry is already running."
        client.portal.call(release)
        client.portal.call(settle, body["id"])
        assert client.get(f"/cpu-jobs/{body['id']}").json()["status"] == "passed"
