import asyncio
import json
import re
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from verity.api.app import create_app
from verity.api.auth import Principal
from verity.api.routes import session_guard
from verity.config import CpuJobConfig, Settings
from verity.contracts import Candidate, to_wire
from verity.contracts.base import to_json
from verity.contracts.events import PPA
from verity.contracts.signoff import SeedTally, Signoff, SignoffLint, SignoffStrength, SignoffTests
from verity.contracts.cpu import CreateCpuJobRequest
from verity.core.budget import BudgetLimits, SpendSnapshot
from verity.core.context import RunOutcome
from verity.core.errors import AdmissionDenied, Forbidden, ToolInfraError, UnknownPreset
from verity.core.pack import TaskPack, write_manifest
from verity.core.records import RunPatch, RunRecord
from verity.llm.fake import FakeProvider
from verity.runs.cpu_job import BLOCKS
from verity.runs.manager import RunManager
from verity.store import SqliteStore
from verity.tools.base import SandboxRunner
from verity_test_support import FakeToolRunner


class ScriptedRuns(RunManager):
    def __init__(self, settings, store, packs):
        super().__init__(settings, store, FakeProvider(settings, delay_s=0), FakeToolRunner(), packs)
        self.calls = []
        self.failures = {}
        self.active = 0
        self.peak = 0
        self.run_cost = 0.1
        # Per pack id, one entry per call in order (the last repeats): (area, clock, power) of the winner, and the
        # sign-off sheet's EQY word. A pack without an entry reports no measurement and "proven".
        self.ppas = {}
        self.eqys = {}
        self.pack_calls = {}
        self.repairs = {}
        self.tag_winners = False
        self.after_run = None
        self.sheet_modes = {}

    @staticmethod
    def _nth(table, key, n, default):
        rows = table.get(key)
        return default if not rows else rows[min(n, len(rows) - 1)]

    async def start_cpu_run(self, **kwargs):
        self.calls.append(kwargs)
        pack = self._packs[kwargs["preset_id"]]
        self.pack_calls[pack.id] = self.pack_calls.get(pack.id, 0) + 1
        run = RunRecord(
            id=f"r_{len(self.calls)}", session_id=kwargs["session_id"], preset_id=pack.id,
            priority=kwargs["priority"], mode=kwargs["mode"], status="running",
            created_at=datetime.now(UTC), budget=BudgetLimits.from_settings(self._settings, {"max_usd": kwargs["max_usd"]}),
            task_pack_sha256=pack.sha256, title=pack.title,
        )
        await self._store.create_run(run)
        self.active += 1
        self.peak = max(self.peak, self.active)
        return run

    async def wait_for_run(self, run_id):
        run = await self.get_run(run_id)
        if run.status == "running":
            await asyncio.sleep(0.01)
            self.active -= 1
            failures = self.failures.get(run.preset_id, 0)
            if failures:
                self.failures[run.preset_id] -= 1
            winner = None if failures else "c1"
            await self._store.update_run(run_id, RunPatch(
                status="error" if failures else "done", winner_id=winner,
                ended_at=datetime.now(UTC), spent=SpendSnapshot(usd=min(run.budget.max_usd, self.run_cost), repairs=self.repairs.get(run.preset_id, 0)),
            ))
            if winner:
                n = sum(1 for c in self.calls[:int(run_id.removeprefix("r_"))] if c["preset_id"] == run.preset_id) - 1
                # A derived core pack (rv32i_core_<job>[_vN]) reads the PPA set for its static pack id.
                ppa = self._nth(self.ppas, run.preset_id, n, None) or self._nth(
                    self.ppas, re.sub(r"_[0-9a-f]{12}(_v\d+)?$", "", run.preset_id), n, None)
                await self._store.upsert_candidate(run_id, Candidate(
                    id="c1", label="v1", round=0, created_by="INIT", reason="fixture",
                    rtl="", status="pass",
                    ppa=None if ppa is None else PPA(area_um2=ppa[0], clock_period_ns=ppa[1], power_mw=ppa[2], pdk="fixture"),
                ))
                sheet = Signoff(
                    candidate_id="c1",
                    tests=SignoffTests(seeds_run=3, seeds_passed=3, per_seed=[
                        SeedTally(seed=1, passed=20, total=20), SeedTally(seed=2, passed=20, total=20),
                        SeedTally(seed=3, passed=20, total=20)]),
                    lint=SignoffLint(errors=0, warnings=0, latches=0),
                    eqy_vs_v1=self._nth(self.eqys, run.preset_id, n, "proven"),
                    eqy_vs_human="not_run",
                    strength=SignoffStrength(planted=10, caught=9, harmless=1, undecided=0, status="ok"),
                    duration_ms=1,
                )
                body = to_json(sheet).encode()
                mode = self.sheet_modes.get(run.preset_id)
                if mode == "garbage":
                    body = b"not json"
                elif mode == "other_version":
                    body = body.replace(b'"candidateId":"c1"', b'"candidateId":"c9"')
                await self._store.write_run_file(run_id, "final/signoff.json", body, "application/json")
                tag = f" // {run_id}" if self.tag_winners else ""
                await self._store.write_run_file(run_id, "final/winner.v", f"module {self._packs[run.preset_id].top}; endmodule{tag}\n".encode(), "text/x-verilog")
                if self.after_run is not None:
                    await self.after_run(run_id, run)
        return await self.get_run(run_id)

    async def stop(self, run_id, session_id):
        self.active -= 1
        await self._store.update_run(run_id, RunPatch(status="stopped", ended_at=datetime.now(UTC)))


def setup(tmp_path, packs, cls=ScriptedRuns, **updates):
    settings = Settings(data_dir=tmp_path / "data", tasks_dir=tmp_path / "tasks", git_sha="test", **updates)
    store = SqliteStore(settings)
    if cls is ScriptedRuns:
        runs = cls(settings, store, packs)
    else:
        runs = RunManager(settings, store, FakeProvider(settings, delay_s=0), FakeToolRunner(), packs)
    return settings, store, runs


async def finish(runs, job_id):
    task = runs.cpu_jobs._tasks[job_id]
    await asyncio.wait_for(task, 10)
    return await runs.cpu_jobs.get(job_id, session_id="owner", role="team")


async def test_all_pass_derived_pack_width_and_view(tmp_path, cpu_packs):
    settings, store, runs = setup(tmp_path, cpu_packs)
    await store.open()
    try:
        await store.set_credits("owner", 1)
        initial = await runs.cpu_jobs.create(CreateCpuJobRequest(specId="rv32i_micro", goal="area"), session_id="owner", role="team")
        assert initial.status == "running" and initial.core.status == "locked"
        assert len(initial.blocks) == 7 and all(b.attempts == 0 for b in initial.blocks)
        view = await finish(runs, initial.id)
        assert view.status == "passed" and view.core.status == "passed"
        assert runs.peak == 3 and len(runs.calls) == 8
        assert runs.calls[-1]["mode"] == "fast"
        assert await runs.get_credits("owner") == 1  # a CPU job takes no credit
        assert view.spent_usd == pytest.approx(0.8)
        assert all(b.status == "passed" and b.attempts == 1 and b.summary.id == b.run_id for b in view.blocks)
        assert [b.block_id for b in view.blocks] == [p for p, _ in BLOCKS]
        pack = runs._packs[view.core.pack_id]
        pack.reverify()
        assert pack.eval_only and pack.top == "rv32i_core"
        assert pack.root.parent == settings.data_dir / "cpu_packs" / view.id
        # The hand-written top is copied (the fake provider echoes it); blocks.v is rewritten from the winners.
        static = runs._packs["rv32i_core"]
        assert (pack.root / "reference.v").read_text() == (static.root / "reference.v").read_text()
        assert TaskPack.load(pack.root).sha256 == pack.sha256
        assert (pack.root / "programs" / "example.s").read_text() == "ebreak\n"
        blocks = (pack.root / "blocks.v").read_text()
        assert blocks == "\n\n".join(f"module {p}; endmodule" for p, _ in BLOCKS) + "\n"
        wire = to_wire(view)
        assert set(wire) == {
            "id", "specId", "goal", "status", "createdAt", "finishedAt", "spentUsd", "message", "blocks", "core", "versions",
            "parentJobId", "upgradeId", "comparison", "retryOf", "retryJobId", "threadId",
        }
        assert wire["parentJobId"] is None and wire["upgradeId"] is None and wire["comparison"] is None
        assert wire["retryJobId"] is None
        assert wire["blocks"][0]["reused"] is False
        assert set(wire["blocks"][0]["evidence"]) == {
            "eqyVsV1", "testsPassed", "testsTotal", "seedsPassed", "seedsRun", "mutantsKilled", "mutantsTotal",
            "repairs", "techniques", "rvfiPassed", "rvfiTotal", "rvfiTimeouts", "rvfiFailed",
        }
        assert wire["blocks"][0]["evidence"]["eqyVsV1"] == "proven" and wire["blocks"][0]["evidence"]["testsTotal"] == 20
        assert [v["version"] for v in wire["versions"]] == [1] and wire["versions"][0]["coreRunId"] == wire["core"]["runId"]
        assert all(b["research"] == "off" and b["researchId"] is None for b in wire["blocks"])
        assert wire["blocks"][0]["summary"]["id"] == wire["blocks"][0]["runId"]
        assert "sessionId" not in wire and "owner" not in wire
        assert (await runs.cpu_jobs.mine("owner"))[0].id == view.id
        assert await runs.cpu_jobs.mine("other") == []
        with pytest.raises(Forbidden):
            await runs.cpu_jobs.get(view.id, session_id="other", role="user")
        assert (await runs.cpu_jobs.get(view.id, session_id="other", role="team")).id == view.id
    finally:
        await store.close()


@pytest.mark.parametrize("failures,expected,attempts", [(1, "passed", 2), (2, "failed", 2)])
async def test_retry_once(tmp_path, cpu_packs, failures, expected, attempts):
    _, store, runs = setup(tmp_path, cpu_packs)
    runs.failures["rv32i_imm"] = failures
    await store.open()
    try:
        view = await runs.cpu_jobs.create(CreateCpuJobRequest(specId="rv32i_micro", goal="speed"), session_id="owner", role="super_admin")
        view = await finish(runs, view.id)
        assert view.status == expected and view.blocks[0].attempts == attempts
        assert sum(c["preset_id"] == "rv32i_imm" for c in runs.calls) == attempts
        assert runs.peak <= 3 and runs.active == 0
        if expected == "failed":
            assert view.core.status == "locked" and view.core.run_id is None
    finally:
        await store.close()


async def test_job_cap_counts_failed_attempts_and_inflight_caps(tmp_path, cpu_packs):
    _, store, runs = setup(tmp_path, cpu_packs, cpu_job=CpuJobConfig(max_usd=0.65))
    runs.failures["rv32i_imm"] = 1
    runs.run_cost = 0.5
    await store.open()
    try:
        view = await runs.cpu_jobs.create(CreateCpuJobRequest(specId="rv32i_micro", goal="power"), session_id="owner", role="team")
        view = await finish(runs, view.id)
        assert view.status == "failed"
        assert view.spent_usd <= 0.65
        assert sum(c["max_usd"] for c in runs.calls) <= 0.65 + 1e-9
    finally:
        await store.close()


async def test_unused_run_caps_are_released_before_job_budget_failure(tmp_path, cpu_packs):
    _, store, runs = setup(tmp_path, cpu_packs, cpu_job=CpuJobConfig(max_usd=1.0))
    await store.open()
    try:
        view = await runs.cpu_jobs.create(CreateCpuJobRequest(specId="rv32i_micro", goal="area"), session_id="owner", role="team")
        view = await finish(runs, view.id)
        assert view.status == "passed" and view.spent_usd == pytest.approx(0.8)
        assert runs.peak <= 3 and len(runs.calls) == 8
    finally:
        await store.close()


async def test_restart_persists_stopped_jobs(tmp_path, cpu_packs):
    settings, store, runs = setup(tmp_path, cpu_packs)
    await store.open()
    view = await runs.cpu_jobs.create(CreateCpuJobRequest(specId="rv32i_micro", goal="area"), session_id="owner", role="team")
    await runs.cpu_jobs.shutdown()
    job = await store.get_cpu_job(view.id)
    assert job.view.status == "stopped" and not runs.cpu_jobs._tasks
    # Simulate a process lost while integrating, then reopen the database.
    job.view.status = "integrating"
    job.view.finished_at = None
    await store.save_cpu_job(job)
    await store.close()
    store2 = SqliteStore(settings)
    await store2.open()
    try:
        runs2 = ScriptedRuns(settings, store2, cpu_packs)
        await runs2.cpu_jobs.start()
        recovered = await runs2.cpu_jobs.get(view.id, session_id="owner", role="user")
        assert recovered.status == "stopped" and recovered.finished_at and recovered.message == "server restarted"
        assert not runs2.calls
        assert (await runs2.cpu_jobs.mine("owner"))[0].id == view.id
    finally:
        await store2.close()


@pytest.mark.parametrize("role,email", [("team", None), ("super_admin", None), ("user", None), ("judge", None), ("user", "a@example.com")])
async def test_any_signed_in_role_builds_a_cpu_with_no_credits_and_is_never_charged(tmp_path, cpu_packs, role, email):
    _, store, runs = setup(tmp_path, cpu_packs)
    await store.open()
    try:
        class AccountStore:
            # Represents the Supabase credit backend, independent of local run storage.
            left = 1
            charged = 0

            async def deduct_credit(self, owner, default):
                self.charged += 1
                self.left -= 1
                return self.left

        runs._threads = AccountStore()
        await store.set_credits("owner", 0)
        for _ in range(2):  # a second job too: with 0 credits and nothing taken, nothing runs out
            view = await runs.cpu_jobs.create(CreateCpuJobRequest(specId="rv32i_micro", goal="area"), session_id="owner", role=role, email=email)
            assert (await finish(runs, view.id)).status == "passed"
        assert runs._threads.charged == 0 and runs._threads.left == 1
        assert await store.get_credits("owner") == 0
    finally:
        await store.close()


async def test_an_anonymous_visitor_is_refused_a_cpu_job(tmp_path, cpu_packs):
    _, store, runs = setup(tmp_path, cpu_packs)
    await store.open()
    try:
        with pytest.raises(Forbidden, match="Sign in to build a CPU."):
            await runs.cpu_jobs.create(CreateCpuJobRequest(specId="rv32i_micro", goal="area"), session_id="anon-1", role="anonymous")
        assert not runs.calls and await store.list_cpu_jobs() == []
    finally:
        await store.close()


def test_http_auth_credits_shape_and_ownership(tmp_path, cpu_packs):
    settings, store, runs = setup(tmp_path, cpu_packs)
    app = create_app(settings, runs, startup=[store.open], shutdown=[store.close])
    principal = Principal(sid="owner", role="user")
    app.dependency_overrides[session_guard] = lambda: principal
    with TestClient(app) as client:
        body = {"specId": "rv32i_micro", "goal": "area"}
        # A signed-in account that is not an admin builds; only a visitor with no session is refused.
        principal = Principal(sid="anon-1", role="anonymous", anonymous=True)
        refused = client.post("/cpu-jobs", json=body)
        assert refused.status_code == 403 and refused.json()["error"] == "Sign in to build a CPU."
        principal = Principal(sid="owner", role="user")
        assert client.post("/cpu-jobs", json={**body, "specId": "other"}).status_code == 422
        principal = Principal(sid="owner", role="team")
        assert client.post("/cpu-jobs", json={**body, "specId": "other"}).status_code == 422
        assert client.post("/cpu-jobs", json={**body, "threadId": "bad"}).status_code == 422
        result = client.post("/cpu-jobs", json=body)
        assert result.status_code == 201
        job_id = result.json()["id"]
        assert result.json()["core"]["runId"] is None
        assert len(client.get("/cpu-jobs/mine").json()["jobs"]) == 1
        assert client.get(f"/cpu-jobs/{job_id}").status_code == 200
        principal = Principal(sid="other", role="user")
        assert client.get(f"/cpu-jobs/{job_id}").status_code == 403
        assert client.get("/cpu-jobs/mine").json() == {"jobs": []}
        assert client.get("/cpu-jobs/cpu_missing").status_code == 404
        # An account with no credits left builds all the same, and its credits do not move.
        principal = Principal(sid="empty", role="user")
        client.portal.call(store.set_credits, "empty", 0)
        result = client.post("/cpu-jobs", json=body)
        assert result.status_code == 201
        assert client.portal.call(store.get_credits, "empty") == 0


def test_access_code_sessions_of_any_role_build_a_cpu_and_an_anonymous_visitor_does_not(tmp_path, cpu_packs):
    import hashlib

    from pydantic import SecretStr

    codes = ",".join(f"{role}:{hashlib.sha256((role + 'code').encode()).hexdigest()}" for role in ("team", "judge", "user"))
    settings, store, runs = setup(tmp_path, cpu_packs, access_code_hashes=SecretStr(codes))
    app = create_app(settings, runs, startup=[store.open], shutdown=[store.close])
    body = {"specId": "rv32i_micro", "goal": "area"}
    with TestClient(app) as client:
        assert client.post("/cpu-jobs", json=body).status_code == 403  # no session: the sign-in refusal
        for role in ("team", "judge", "user"):
            token = client.post("/auth/session", json={"accessCode": role + "code"}).json()["token"]
            me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
            assert me["canBuild"] is True and me["isAdmin"] is (role == "team")
            res = client.post("/cpu-jobs", json=body, headers={"Authorization": f"Bearer {token}"})
            assert res.status_code == 201, (role, res.text)
            job_id = res.json()["id"]
            client.portal.call(finish, runs, job_id)
            # Its own job is readable.
            assert client.get(f"/cpu-jobs/{job_id}", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_with_auth_required_a_cpu_build_without_a_session_is_a_401(tmp_path, cpu_packs):
    import hashlib

    from pydantic import SecretStr

    from verity.config import AuthConfig

    codes = f"user:{hashlib.sha256(b'usercode').hexdigest()}"
    settings, store, runs = setup(tmp_path, cpu_packs, auth=AuthConfig(required=True), access_code_hashes=SecretStr(codes))
    app = create_app(settings, runs, startup=[store.open], shutdown=[store.close])
    body = {"specId": "rv32i_micro", "goal": "area"}
    job = "cpu_" + "0" * 32
    with TestClient(app) as client:
        for res in (client.post("/cpu-jobs", json=body), client.post(f"/cpu-jobs/{job}/retry"),
                    client.post(f"/cpu-jobs/{job}/upgrade", json={"upgradeId": "pipeline_2"}),
                    client.post("/cpu-jobs", json=body, headers={"Authorization": "Bearer not-a-token"})):
            assert res.status_code == 401
        assert not runs.calls and client.portal.call(store.list_cpu_jobs) == []
        token = client.post("/auth/session", json={"accessCode": "usercode"}).json()["token"]
        res = client.post("/cpu-jobs", json=body, headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 201
        client.portal.call(finish, runs, res.json()["id"])


async def test_the_daily_budget_still_refuses_a_signed_in_cpu_job(tmp_path, cpu_packs):
    settings, store, runs = setup(tmp_path, cpu_packs)
    await store.open()
    try:
        await store._write("INSERT INTO daily_spend (day, usd) VALUES (?, ?)", (datetime.now(UTC).strftime("%Y-%m-%d"), settings.limits.daily_usd))
        with pytest.raises(AdmissionDenied) as exc:
            await runs.cpu_jobs.create(CreateCpuJobRequest(specId="rv32i_micro", goal="area"), session_id="owner", role="user")
        assert exc.value.reason == "budget_exhausted" and not runs.calls
    finally:
        await store.close()


async def test_internal_admission_preserves_public_refusal_and_daily_limit(tmp_path, cpu_packs, monkeypatch):
    settings, store, runs = setup(tmp_path, cpu_packs, cls=RunManager)
    await store.open()
    seen = []

    async def drive(ctx, **kwargs):
        # The real orchestration checks credits through the run's store.
        seen.append(await ctx.store.get_credits(ctx.session_id))
        ctx.outcome = RunOutcome(status="done", winner_id="c1")

    monkeypatch.setattr("verity.runs.manager.run_orchestrator", drive)

    async def forbidden_session_check(*args):
        raise AssertionError("CPU runs must bypass the session daily limit")

    monkeypatch.setattr(runs, "_session_spend", forbidden_session_check)
    try:
        await store.set_credits("owner", 0)
        with pytest.raises(UnknownPreset):
            await runs.create_run(session_id="owner", preset_id="rv32i_imm", priority="area", mode="fast")
        first, second = await asyncio.gather(*[
            runs.start_cpu_run(session_id="owner", preset_id=p, priority="area", mode="full", max_usd=4)
            for p in ("rv32i_imm", "rv32i_dec")
        ])
        for run in (first, second):
            assert run.budget.max_usd == 0.5
            assert (await runs.wait_for_run(run.id)).status == "done"
        assert seen == [1, 1] and await runs.get_credits("owner") == 0
        # Fake provider still respects an already exhausted global daily budget.
        await store._write("INSERT INTO daily_spend (day, usd) VALUES (?, ?)", (datetime.now(UTC).strftime("%Y-%m-%d"), settings.limits.daily_usd))
        with pytest.raises(AdmissionDenied) as exc:
            await runs.start_cpu_run(session_id="owner", preset_id="rv32i_alu", priority="area", mode="fast", max_usd=0.5)
        assert exc.value.reason == "budget_exhausted"
    finally:
        await runs.shutdown()
        await store.close()


async def test_shutdown_cancels_cpu_runs_and_pending_global_slot(tmp_path, cpu_packs, monkeypatch):
    _, store, runs = setup(tmp_path, cpu_packs, cls=RunManager)
    await store.open()
    started = asyncio.Event()
    count = 0

    async def drive(ctx, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("verity.runs.manager.run_orchestrator", drive)
    try:
        view = await runs.cpu_jobs.create(CreateCpuJobRequest(specId="rv32i_micro", goal="area"), session_id="owner", role="team")
        await asyncio.wait_for(started.wait(), 10)
        await asyncio.wait_for(runs.shutdown(), 10)
        job = await store.get_cpu_job(view.id)
        assert job.view.status == "stopped"
        assert not runs._live and not runs.cpu_jobs._tasks
        assert not await store.runs_with_status(["queued", "running"])
    finally:
        await runs.shutdown()
        await store.close()


async def test_core_failure_fails_job(tmp_path, cpu_packs):
    _, store, runs = setup(tmp_path, cpu_packs)
    original = runs.start_cpu_run

    async def fail_core(**kwargs):
        if kwargs["preset_id"].startswith("rv32i_core_"):
            runs.failures[kwargs["preset_id"]] = 1
        return await original(**kwargs)

    runs.start_cpu_run = fail_core
    await store.open()
    try:
        view = await runs.cpu_jobs.create(CreateCpuJobRequest(specId="rv32i_micro", goal="area"), session_id="owner", role="team")
        view = await finish(runs, view.id)
        assert view.status == "failed" and view.core.status == "failed"
        assert all(row.status == "passed" for row in view.blocks)
        assert len(runs.calls) == 9 and [c["mode"] for c in runs.calls[-2:]] == ["fast", "fast"]  # the core is tried twice
    finally:
        await store.close()


async def test_a_core_that_fails_once_is_retried_and_the_job_passes(tmp_path, cpu_packs):
    _, store, runs = setup(tmp_path, cpu_packs)
    original = runs.start_cpu_run
    cores = []

    async def fail_first_core(**kwargs):
        if kwargs["preset_id"].startswith("rv32i_core_"):
            if not cores:
                runs.failures[kwargs["preset_id"]] = 1
            cores.append(kwargs["preset_id"])
        return await original(**kwargs)

    runs.start_cpu_run = fail_first_core
    await store.open()
    try:
        view = await runs.cpu_jobs.create(CreateCpuJobRequest(specId="rv32i_micro", goal="area"), session_id="owner", role="team")
        view = await finish(runs, view.id)
        assert view.core.status == "passed" and view.status in ("passed", "improving")
        assert len(cores) >= 2 and cores[0] == cores[1]  # the same derived core pack, run again
    finally:
        await store.close()


class CaptureRunner(SandboxRunner):
    async def image_digest(self):
        return "fake"

    async def _execute(self, call):
        self.call = call
        raise ToolInfraError("captured")


@pytest.mark.parametrize("script", ["verify", "measure", "eqy", "seeds", "strength", "wave"])
async def test_blocks_appended_in_every_sandbox_call(tmp_path, cpu_packs, script):
    pack = cpu_packs["rv32i_core"]
    blocks = b"module frozen_block; endmodule\n"
    (pack.root / "blocks.v").write_bytes(blocks)
    write_manifest(pack.root)
    pack = TaskPack.load(pack.root)
    settings = Settings(data_dir=tmp_path / "data", tools={"infra_retries": 0})
    runner = CaptureRunner(settings, max_parallel=1)
    rtl = b"// line 1\nmodule rv32i_core;\nendmodule\n"
    files = {"dut.v": rtl}
    if script == "eqy":
        files["b.v"] = b"// gate\n" + rtl
    if script == "strength":
        files["mutants.json"] = json.dumps({"mutants": [{"id": "m1", "text": rtl.decode()}]}).encode()
    original = dict(files)
    with pytest.raises(ToolInfraError, match="captured"):
        await runner._run(script, pack, files, 1, lambda p: p, lambda r: r)
    assert runner.call.work_files["dut.v"] == rtl + b"\n\n" + blocks
    assert files == original
    if script == "eqy":
        assert runner.call.work_files["b.v"] == files["b.v"] + b"\n\n" + blocks
    if script == "strength":
        mutant = json.loads(runner.call.work_files["mutants.json"])["mutants"][0]
        assert mutant["text"] == rtl.decode() + "\n\n" + blocks.decode()
    assert "frozen_block" not in pack.public_brief()


async def test_pack_without_blocks_unchanged(tmp_path, cpu_packs):
    runner = CaptureRunner(Settings(data_dir=tmp_path / "data", tools={"infra_retries": 0}), max_parallel=1)
    files = {"dut.v": b"module rv32i_imm; endmodule"}
    with pytest.raises(ToolInfraError):
        await runner._run("verify", cpu_packs["rv32i_imm"], files, 1, lambda p: p, lambda r: r)
    assert runner.call.work_files == files


class FlakySandboxRuns(ScriptedRuns):
    """The sandbox refuses the first `refusals` block-run starts, as Docker does while a laptop wakes from standby."""

    def __init__(self, settings, store, packs, refusals):
        super().__init__(settings, store, packs)
        self.refusals = refusals

    async def start_cpu_run(self, **kwargs):
        if self.refusals > 0:
            self.refusals -= 1
            raise AdmissionDenied("sandbox_down", "The test machine (Docker) is not running.")
        return await super().start_cpu_run(**kwargs)


@pytest.mark.parametrize("refusals,expected", [(2, "passed"), (99, "failed")])
async def test_a_stalled_sandbox_is_retried_before_the_job_fails(tmp_path, cpu_packs, monkeypatch, refusals, expected):
    import verity.runs.cpu_job as cpu_job

    monkeypatch.setattr(cpu_job, "SANDBOX_RETRY_S", 0.0)
    settings = Settings(data_dir=tmp_path / "data", tasks_dir=tmp_path / "tasks", git_sha="test")
    store = SqliteStore(settings)
    runs = FlakySandboxRuns(settings, store, cpu_packs, refusals)
    await store.open()
    try:
        view = await runs.cpu_jobs.create(CreateCpuJobRequest(specId="rv32i_micro", goal="area"), session_id="owner", role="team")
        view = await finish(runs, view.id)
        assert view.status == expected
        if expected == "failed":
            assert "Docker" in (view.message or "")
    finally:
        await store.close()


@pytest.mark.parametrize("with_v1", [True, False])
async def test_an_unproven_block_winner_falls_back_to_its_v1(tmp_path, cpu_packs, with_v1):
    """Each block the CPU uses must be proven equal to its own v1; otherwise the CPU uses v1 itself."""
    _, store, runs = setup(tmp_path, cpu_packs)
    runs.eqys["rv32i_alu"] = ["inconclusive"]

    async def add_v1(run_id, run):
        if run.preset_id != "rv32i_alu" or not with_v1:
            return
        await store.upsert_candidate(run_id, Candidate(
            id="c0", label="v1", round=0, created_by="INIT", reason="fixture", rtl="", status="pass", is_baseline=True,
            ppa=PPA(area_um2=500.0, clock_period_ns=2.0, power_mw=0.5, pdk="fixture"),
        ))
        await store.write_run_file(run_id, "candidates/c0/attempt-1.v", b"module rv32i_alu; endmodule // broken\n", "text/x-verilog")
        await store.write_run_file(run_id, "candidates/c0/attempt-1.verify.json", b'{"status": "sim_fail"}', "application/json")
        await store.write_run_file(run_id, "candidates/c0/attempt-2.v", b"module rv32i_alu; endmodule // v1\n", "text/x-verilog")
        await store.write_run_file(run_id, "candidates/c0/attempt-2.verify.json", b'{"status": "pass"}', "application/json")

    runs.after_run = add_v1
    await store.open()
    try:
        view = await runs.cpu_jobs.create(CreateCpuJobRequest(specId="rv32i_micro", goal="area"), session_id="owner", role="team")
        view = await finish(runs, view.id)
        alu = next(b for b in view.blocks if b.block_id == "rv32i_alu")
        if with_v1:
            assert view.status == "passed" and alu.status == "passed" and alu.used_version == "v1"
            blocks = (runs._packs[view.core.pack_id].root / "blocks.v").read_text()
            assert "endmodule // v1" in blocks and "// broken" not in blocks
            assert all(b.used_version is None for b in view.blocks if b.block_id != "rv32i_alu")
        else:
            assert view.status == "failed" and alu.status == "failed" and alu.attempts == 2
    finally:
        await store.close()
