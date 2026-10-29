"""The eval suites and the report generator, driven by the fake provider and the fake tool runner (no sandbox, $0)."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from verity.core.pack import TaskPack
from verity.evals import suite_functional, suite_isolation, suite_packs, suite_pipeline, suite_repair
from verity.evals.__main__ import SUITE_ORDER, build_parser, main
from verity.evals.harness import EvalEnv, select_packs, stage_times_from
from verity.evals.record import EvalRecord, ResultFile, load_records
from verity.evals.report import generate_report, write_report
from verity.contracts import StageEvent
from verity.llm.fake import FakeProvider
from verity.store import SqliteStore
from verity_test_support import FakeToolRunner, make_settings, tmp_cassettes


async def make_env(tmp_path: Path, packs, *, inject_bug: bool = True, tools=None, skip_sandbox: bool = False) -> EvalEnv:
    settings = make_settings(tmp_path / "data")
    store = SqliteStore(settings)
    await store.open()
    return EvalEnv(
        settings=settings,
        provider=FakeProvider(settings, delay_s=0.0, inject_bug=inject_bug, cassette_dir=tmp_cassettes(settings)),
        provider_name="fake",
        tools=tools or FakeToolRunner(eqy_status="inconclusive"),
        store=store,
        packs=dict(packs),
        eval_run_id="e_test",
        git_sha="abc1234",
        tool_image_digest="sha256:fake-image",
        results=ResultFile(tmp_path / "results", "e_test"),
        skip_sandbox=skip_sandbox,
    )


def rows(env: EvalEnv, suite: str | None = None) -> list[EvalRecord]:
    found = load_records(env.results.path) if env.results.path.exists() else []
    return [r for r in found if suite is None or r.suite == suite]


async def test_functional_suite_measures_pass_at_one(tmp_path, packs) -> None:
    env = await make_env(tmp_path, packs, inject_bug=False)
    try:
        await suite_functional.run(env, [packs["mac4"]], repeats=2)
        got = rows(env, "functional")
        assert len(got) == 2 and all(r.initial_pass is True and r.repairs_used == 0 for r in got)
        assert got[0].llm_calls == 2 and got[0].est_usd >= 0 and set(got[0].prompt_versions) == {"planner", "rtl_gen"}
        assert got[0].provider == "fake" and got[0].git_sha == "abc1234" and got[0].model_ids["planner"]
    finally:
        await env.aclose()

    buggy = await make_env(tmp_path / "b", packs, inject_bug=True)
    try:
        await suite_functional.run(buggy, [packs["mac4"]], repeats=1)
        assert rows(buggy, "functional")[0].initial_pass is False  # the fake injects one bug: no repair in this suite
    finally:
        await buggy.aclose()


async def test_repair_suite_counts_detected_bugs_and_repairs_them(tmp_path, packs) -> None:
    env = await make_env(tmp_path, packs)
    try:
        await suite_repair.run(env, [packs["mac4"]], repeats=1)
        got = rows(env, "repair")
        assert {r.case for r in got} == {"wrong_width", "off_by_one", "missing_reset", "swapped_operands"}
        for r in got:
            assert r.extra["counted"] is True and r.initial_pass is False
            assert r.final_pass is True and r.repair_success is True and r.repairs_used == 1
            assert r.llm_calls == 1 and "repair" in r.prompt_versions
    finally:
        await env.aclose()


async def test_isolation_suite_passes_on_every_pack(tmp_path, packs) -> None:
    env = await make_env(tmp_path, packs, skip_sandbox=True)
    try:
        await suite_isolation.run(env, select_packs(env, "all"), repeats=1)
        got = rows(env, "isolation")
        assert len(got) == len(packs)
        for r in got:
            assert r.ok is True, (r.task_id, r.error, r.extra["checks"])
            names = {c["name"] for c in r.extra["checks"]}
            assert {"banned_rejected", "sandbox_copy_clean", "brief_no_leak", "pack_hash_stable"} <= names
            assert r.pack_hash_ok is True
    finally:
        await env.aclose()


async def test_packs_suite_with_a_scripted_toolchain(tmp_path, packs) -> None:
    """Fake verify passes only the exact reference, so every mutant is killed; fake EQY says inconclusive."""
    env = await make_env(tmp_path, packs)
    try:
        await suite_packs.run(env, select_packs(env, "all"), repeats=1)
        got = rows(env, "packs")
        assert len(got) == len(packs)
        for r in got:
            assert r.ok is True, (r.task_id, r.error)
            assert r.extra["mutants_killed"] == r.extra["mutants_total"] >= 3
            assert r.final_pass is True and (r.final_area_um2 or 0) > 0
    finally:
        await env.aclose()


async def test_pipeline_suites_share_one_run_per_task(tmp_path, packs) -> None:
    tools = FakeToolRunner()
    env = await make_env(tmp_path, packs, tools=tools)
    try:
        mac4 = [packs["mac4"]]
        await suite_pipeline.run_optimization(env, mac4, 1, "full")
        measured_once = tools.measure_calls
        await suite_pipeline.run_performance(env, mac4, 1, "full")
        await suite_pipeline.run_regression(env, mac4, 1, "full")
        assert tools.measure_calls == measured_once, "performance and regression reuse the optimization run"

        opt = rows(env, "optimization")[0]
        assert opt.candidate_count and opt.candidate_count >= 4 and opt.baseline_area_um2 == 1000.0
        assert opt.initial_pass is False and opt.repairs_used == 1 and opt.repair_success is True and opt.final_pass is True
        assert opt.pack_hash_ok is True and "area_gain_pct" in opt.extra and opt.fitness_best is not None
        assert opt.stage_times.get("plan") is not None and opt.llm_calls >= 5

        perf = rows(env, "performance")[0]
        assert perf.runtime_s > 0 and perf.extra["spend"]["llm_calls"] == perf.llm_calls

        reg = rows(env, "regression")[0]
        assert reg.ok is True and reg.extra["winner_tests_pass"] is True
    finally:
        await env.aclose()


def test_stage_times_sum_active_to_terminal_per_stage() -> None:
    def stage(stage_id, status, t_ms) -> StageEvent:
        return StageEvent(stage=stage_id, status=status, t_ms=t_ms)  # type: ignore[arg-type]

    events = [
        stage("plan", "active", 1000), stage("plan", "done", 3500),
        stage("evolve", "active", 4000), stage("evolve", "done", 5000),
        stage("evolve", "active", 6000), stage("evolve", "skipped", 6500),
        stage("prove", "active", 7000),  # never finished: not counted
    ]
    assert stage_times_from(events) == {"plan": 2.5, "evolve": 1.5}


def test_select_packs(packs) -> None:
    env = SimpleNamespace(packs=packs)
    assert [p.id for p in select_packs(env, "all")] == sorted(packs)  # type: ignore[arg-type]
    assert [p.id for p in select_packs(env, "mac4, alu8")] == ["mac4", "alu8"]  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        select_packs(env, "nope")  # type: ignore[arg-type]


# ---------- records and report ----------
def record(**kw) -> EvalRecord:
    base = dict(eval_run_id="e_1", git_sha="abc1234", task_id="mac4", suite="functional", provider="nebius", prompt_versions={"planner": "planner.v1.md@aaaaaaaaaaaa"})
    base.update(kw)
    return EvalRecord(**base)  # type: ignore[arg-type]


def test_result_file_round_trip_skips_a_cut_line(tmp_path: Path) -> None:
    file = ResultFile(tmp_path, "e_1")
    file.write(record(initial_pass=True))
    file.write(record(task_id="alu8", initial_pass=False))
    with file.path.open("a", encoding="utf-8") as fh:
        fh.write('{"eval_run_id": "e_1", "task_')  # a crashed run leaves half a line
    loaded = load_records(file.path)
    assert [r.task_id for r in loaded] == ["mac4", "alu8"]
    first = json.loads(file.path.read_text(encoding="utf-8").splitlines()[0])
    for key in ("eval_run_id", "git_sha", "task_id", "suite", "provider", "model_ids", "prompt_versions", "tool_image_digest",
                "candidate_count", "llm_calls", "input_tokens", "output_tokens", "est_usd", "runtime_s", "stage_times",
                "initial_pass", "final_pass", "repairs_used", "repair_success", "baseline_area_um2", "final_area_um2",
                "baseline_clock_ns", "final_clock_ns", "fitness_best", "improved", "equivalence", "pack_hash_ok"):
        assert key in first, f"ARCHITECTURE 14.4 field {key} missing from the JSONL line"


def test_report_states_n_and_keeps_fake_rows_apart() -> None:
    records = [
        record(initial_pass=True), record(initial_pass=True, repeat=2), record(initial_pass=False, repeat=3),
        record(suite="repair", case="wrong_width", initial_pass=False, final_pass=True, repair_success=True, repairs_used=1, extra={"counted": True}),
        record(suite="repair", case="off_by_one", initial_pass=False, final_pass=False, repair_success=False, repairs_used=3, extra={"counted": True}),
        record(suite="repair", case="missing_reset", extra={"counted": False}, note="bug_not_detected"),
        record(suite="optimization", baseline_area_um2=1000.0, final_area_um2=800.0, baseline_clock_ns=3.0, final_clock_ns=3.3,
               fitness_best=11.2, improved=True, equivalence="proven", est_usd=0.08, runtime_s=300.0),
        record(suite="performance", est_usd=0.08, runtime_s=300.0, llm_calls=9, stage_times={"plan": 12.0}),
        record(suite="functional", task_id="alu8", error="LLMError: timeout"),
        record(provider="fake", initial_pass=True),
    ]
    text = generate_report(records)
    assert "## Provider: nebius" in text and "## Provider: fake" in text
    assert "pass@1 (functional, no repair) | 67% (n=3)" in text
    assert "repair success rate | 50% (n=2)" in text  # the undetected bug is not counted
    assert "median area gain vs v1 (optimization) | 20.00 % (n=1)" in text
    assert "improved winners proven equivalent | 100% (n=1)" in text
    assert "1 row(s) ended with an error" in text
    assert "test the pipeline, not model quality" in text
    assert "relative to our own v1" in text or "relative to our own v1" in generate_report([])
    assert "No result rows yet" in generate_report([])


def test_write_report_reads_every_jsonl_file(tmp_path: Path) -> None:
    ResultFile(tmp_path / "results", "e_1").write(record(initial_pass=True))
    ResultFile(tmp_path / "results", "e_2").write(record(initial_pass=False))
    out = write_report(tmp_path / "results", tmp_path / "report.md")
    assert "n=2" in out.read_text(encoding="utf-8")
    only = write_report(tmp_path / "results", tmp_path / "one.md", only_run="e_1")
    assert "n=1" in only.read_text(encoding="utf-8")


def test_cli_arguments_and_report_only(tmp_path: Path) -> None:
    parser = build_parser()
    args = parser.parse_args(["--suite", "functional", "--tasks", "add8", "--provider", "fake", "--repeats", "3"])
    assert (args.suite, args.tasks, args.provider, args.repeats) == ("functional", "add8", "fake", 3)
    assert set(SUITE_ORDER) == {"packs", "functional", "repair", "optimization", "regression", "performance", "isolation"}
    with pytest.raises(SystemExit):
        parser.parse_args(["--suite", "bogus"])
    ResultFile(tmp_path / "results", "e_1").write(record(initial_pass=True))
    with pytest.raises(SystemExit) as done:
        main(["--report-only", "--results-dir", str(tmp_path / "results"), "--report-out", str(tmp_path / "r.md")])
    assert done.value.code == 0 and (tmp_path / "r.md").read_text(encoding="utf-8").startswith("# Verity evals report")
    with pytest.raises(SystemExit) as usage:
        main([])
    assert usage.value.code == 2
