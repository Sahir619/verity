"""evals/report.md generator (ARCHITECTURE 14.4).

Reads every JSONL file under evals/results/ (or the files given) and writes a markdown report with the aggregates and
comparisons across git_sha and prompt_versions. Honesty rules: gains are relative to our own v1 only, every figure
states how many rows it comes from, and rows from the fake provider are reported apart (they test the pipeline, not
the model).
"""

import statistics
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from .record import EvalRecord, load_records


def median(values: Iterable[float | None]) -> float | None:
    xs = [v for v in values if v is not None]
    return statistics.median(xs) if xs else None


def share(rows: Sequence[EvalRecord], flag: str) -> tuple[float | None, int]:
    """(fraction of rows where `flag` is True, number of rows where it is not None)."""
    known = [getattr(r, flag) for r in rows if getattr(r, flag) is not None]
    if not known:
        return None, 0
    return sum(1 for v in known if v) / len(known), len(known)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.0f}%"


def _num(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _cell(value: float | None, n: int, as_pct: bool = True, suffix: str = "") -> str:
    return f"{_pct(value) if as_pct else _num(value)}{suffix} (n={n})"


def _gain(base: float | None, value: float | None) -> float | None:
    if base is None or value is None or base <= 0:
        return None
    return 100.0 * (base - value) / base


def _by(rows: Sequence[EvalRecord], suite: str) -> list[EvalRecord]:
    return [r for r in rows if r.suite == suite and not r.error]


def _counted(rows: Sequence[EvalRecord]) -> list[EvalRecord]:
    """Repair rows count only when the injected bug was detected by the locked tests."""
    return [r for r in rows if r.extra.get("counted") is True]


def _table(header: Sequence[str], body: Sequence[Sequence[str]]) -> list[str]:
    if not body:
        return ["_no rows_", ""]
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in body]
    return [*lines, ""]


def _section_provider(provider: str, rows: list[EvalRecord]) -> list[str]:
    out = [f"## Provider: {provider}", ""]
    if provider == "fake":
        out += ["_Rows from the fake provider test the pipeline, not model quality. Do not quote them as results._", ""]

    functional = _by(rows, "functional")
    repair = _counted(_by(rows, "repair"))
    optimization = _by(rows, "optimization")
    regression = [r for r in rows if r.suite == "regression"]
    performance = _by(rows, "performance")

    p1, n1 = share(functional, "initial_pass")
    after, n2 = share(repair, "final_pass")
    fix, n3 = share(repair, "repair_success")
    improved, n4 = share(optimization, "improved")
    winners = [r for r in optimization if r.improved]
    proven = [1.0 if r.equivalence == "proven" else 0.0 for r in winners]
    area_gain = median(_gain(r.baseline_area_um2, r.final_area_um2) for r in optimization)
    clock_gain = median(_gain(r.baseline_clock_ns, r.final_clock_ns) for r in optimization)

    out += ["### Headline figures", ""]
    out += _table(
        ["Figure", "Value"],
        [
            ["pass@1 (functional, no repair)", _cell(p1, n1)],
            ["pass after repair (repair suite, detected bugs only)", _cell(after, n2)],
            ["repair success rate", _cell(fix, n3)],
            ["median area gain vs v1 (optimization)", _cell(area_gain, len(optimization), as_pct=False, suffix=" %")],
            ["median clock period gain vs v1 (optimization)", _cell(clock_gain, len(optimization), as_pct=False, suffix=" %")],
            ["share of runs that improved on v1", _cell(improved, n4)],
            ["share of improved winners proven equivalent", _cell(sum(proven) / len(proven) if proven else None, len(proven))],
            ["median cost per run (USD, estimated)", _cell(median(r.est_usd for r in performance), len(performance), as_pct=False)],
            ["median runtime per run (s)", _cell(median(r.runtime_s for r in performance), len(performance), as_pct=False)],
        ],
    )

    errors = [r for r in rows if r.error and r.suite in ("functional", "repair", "optimization", "performance")]
    if errors:
        out += [
            f"**{len(errors)} row(s) ended with an error (LLM, sandbox or crash) and are excluded from the rates above:** "
            + "; ".join(sorted({f"{r.suite}/{r.task_id}: {(r.error or '')[:80]}" for r in errors})[:5]),
            "",
        ]

    out += _functional_by_task(functional)
    out += _repair_by_case(repair)
    out += _optimization_by_task(optimization)
    out += _regression(regression)
    out += _performance(performance)
    out += _self_checks(rows, "packs", "Pack self-tests")
    out += _self_checks(rows, "isolation", "Isolation checks")
    out += _compare_versions(rows)
    return out


def _functional_by_task(rows: list[EvalRecord]) -> list[str]:
    if not rows:
        return []
    by_task: dict[str, list[EvalRecord]] = defaultdict(list)
    for r in rows:
        by_task[r.task_id].append(r)
    body = [[t, _cell(share(rs, "initial_pass")[0], len(rs)), _num(median(r.est_usd for r in rs), 4)] for t, rs in sorted(by_task.items())]
    return ["### Functional by task", "", *_table(["Task", "pass@1", "median USD"], body)]


def _repair_by_case(rows: list[EvalRecord]) -> list[str]:
    if not rows:
        return []
    by_case: dict[str, list[EvalRecord]] = defaultdict(list)
    for r in rows:
        by_case[r.case or "?"].append(r)
    body = [
        [c, _cell(share(rs, "repair_success")[0], len(rs)), _num(median(r.repairs_used for r in rs), 1)]
        for c, rs in sorted(by_case.items())
    ]
    return ["### Repair by bug type", "", *_table(["Bug", "repair success", "median attempts"], body)]


def _optimization_by_task(rows: list[EvalRecord]) -> list[str]:
    if not rows:
        return []
    by_task: dict[str, list[EvalRecord]] = defaultdict(list)
    for r in rows:
        by_task[r.task_id].append(r)
    body = []
    for task, rs in sorted(by_task.items()):
        body.append(
            [
                task,
                str(len(rs)),
                _num(median(_gain(r.baseline_area_um2, r.final_area_um2) for r in rs), 1),
                _num(median(_gain(r.baseline_clock_ns, r.final_clock_ns) for r in rs), 1),
                _cell(share(rs, "improved")[0], len(rs)),
                _num(median(r.fitness_best for r in rs), 2),
            ]
        )
    return [
        "### Optimization by task (gains are relative to our own v1)",
        "",
        *_table(["Task", "runs", "area gain %", "clock gain %", "improved", "best fitness"], body),
    ]


def _regression(rows: list[EvalRecord]) -> list[str]:
    if not rows:
        return []
    ok, n = share(rows, "ok")
    states: dict[str, int] = defaultdict(int)
    for r in rows:
        states[r.equivalence or "none"] += 1
    detail = ", ".join(f"{k}: {v}" for k, v in sorted(states.items()))
    return ["### Regression (winner vs v1)", "", f"- checks passed: {_cell(ok, n)}", f"- equivalence of winners: {detail}", ""]


def _finish_reasons(rows: list[EvalRecord]) -> str:
    totals: dict[str, int] = defaultdict(int)
    for r in rows:
        for reason, n in r.finish_reasons.items():
            totals[reason] += n
    return ", ".join(f"{k}: {v}" for k, v in sorted(totals.items())) or "none recorded"


def _performance(rows: list[EvalRecord]) -> list[str]:
    if not rows:
        return []
    stages: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        for stage, seconds in r.stage_times.items():
            stages[stage].append(seconds)
    body = [[s, _num(median(v), 1), str(len(v))] for s, v in sorted(stages.items())]
    head = [
        f"- median LLM calls per run: {_num(median(float(r.llm_calls) for r in rows), 1)}",
        f"- median input tokens: {_num(median(float(r.input_tokens) for r in rows), 0)}, output tokens: {_num(median(float(r.output_tokens) for r in rows), 0)}, of which reasoning tokens: {_num(median(float(r.reasoning_tokens) for r in rows), 0)}",
        f"- model calls by finish reason: {_finish_reasons(rows)} (length means cut at the output cap)",
        "",
    ]
    return ["### Performance", "", *head, *_table(["Stage", "median seconds", "runs"], body)]


def _self_checks(rows: list[EvalRecord], suite: str, title: str) -> list[str]:
    picked = [r for r in rows if r.suite == suite]
    if not picked:
        return []
    body = [
        [r.task_id, "pass" if r.ok else "FAIL", (r.error or r.note or "")[:120].replace("|", "/")]
        for r in sorted(picked, key=lambda r: r.task_id)
    ]
    return [f"### {title}", "", *_table(["Task", "result", "detail"], body)]


def _compare_versions(rows: list[EvalRecord]) -> list[str]:
    """Same metrics grouped by (git_sha, prompt versions): the table that shows whether a change helped."""
    groups: dict[tuple[str, str], list[EvalRecord]] = defaultdict(list)
    for r in rows:
        versions = ", ".join(f"{k}={v}" for k, v in sorted(r.prompt_versions.items())) or "none recorded"
        groups[(r.git_sha or "unknown", versions)].append(r)
    if len(groups) < 1:
        return []
    body = []
    for (sha, versions), rs in sorted(groups.items()):
        func = _by(rs, "functional")
        rep = _counted(_by(rs, "repair"))
        opt = _by(rs, "optimization")
        body.append(
            [
                sha,
                versions[:90],
                _cell(share(func, "initial_pass")[0], len(func)),
                _cell(share(rep, "repair_success")[0], len(rep)),
                _num(median(r.fitness_best for r in opt), 2) + f" (n={len(opt)})",
            ]
        )
    return ["### Comparison across git_sha and prompt versions", "", *_table(["git_sha", "prompt versions", "pass@1", "repair success", "median best fitness"], body)]


def generate_report(records: Sequence[EvalRecord]) -> str:
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    runs = sorted({r.eval_run_id for r in records})
    lines = [
        "# Verity evals report",
        "",
        f"Generated {stamp} from {len(records)} rows in {len(runs)} eval run(s).",
        "",
        "Honesty rules: gains are relative to our own v1 only. Each figure states how many rows it comes from (n). "
        "Nothing here is compared to published numbers.",
        "",
    ]
    by_provider: dict[str, list[EvalRecord]] = defaultdict(list)
    for r in records:
        by_provider[r.provider].append(r)
    if not records:
        lines += ["_No result rows yet. Run `python -m verity.evals --suite packs` first._", ""]
    for provider in sorted(by_provider):
        lines += _section_provider(provider, by_provider[provider])
    return "\n".join(lines).rstrip() + "\n"


def collect(results_dir: Path, only_run: str | None = None) -> list[EvalRecord]:
    paths = sorted(results_dir.glob("*.jsonl")) if results_dir.is_dir() else []
    if only_run:
        paths = [p for p in paths if p.stem == only_run]
    rows: list[EvalRecord] = []
    for path in paths:
        rows += load_records(path)
    return rows


def write_report(results_dir: Path, out_path: Path, only_run: str | None = None) -> Path:
    text = generate_report(collect(results_dir, only_run))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8", newline="\n")
    return out_path
