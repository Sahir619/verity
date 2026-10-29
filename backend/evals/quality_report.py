"""Quality report: read saved run logs and print one table per run and a total. Standard library only.

A run log is the JSON returned by GET /runs/{id}/log: {"meta": {...}, "events": [...]}. Give file paths, folders
(every *.json inside) or both. Numbers come straight from the events; nothing is estimated.

    python backend/evals/quality_report.py C:/path/to/runs/*.json
    python backend/evals/quality_report.py C:/path/to/runs --priority speed

What each row means:
    new versions      candidates other than v1 (rejected ones included)
    copies            measured new versions with the same cell count, area and clock period as an earlier measured
                      version (v1 included); the older version is the original
    worse on goal     measured new versions worse than v1 on the goal metric (area for "area", clock period for
                      "speed", power for "power"), strictly: equal is not worse
    rejected          new versions that ended rejected (failed tests, did not synthesize, dropped by the proof)
    EQY               from the sign-off sheet (signoff/<id>.json) and the human_ref event
    spend, wall time  the last credits event, and meta.durationMs
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

GOAL_FIELD = {"area": "areaUm2", "speed": "clockPeriodNs", "power": "powerMw"}
GOAL_NAME = {"area": "area", "speed": "clock period", "power": "power"}


@dataclass
class RunStats:
    path: str
    run_id: str = ""
    block: str = ""
    priority: str | None = None
    v1_first_try: bool | None = None
    new_versions: int = 0
    copies: int = 0
    worse_on_goal: int | None = None
    rejected: int = 0
    winner: str | None = None
    winner_change: dict[str, float | None] = field(default_factory=dict)
    eqy_v1: str | None = None
    eqy_human: str | None = None
    seeds_passed: int | None = None
    seeds_run: int | None = None
    planted: int | None = None
    caught: int | None = None
    harmless: int | None = None
    spend_usd: float | None = None
    wall_s: float | None = None


# ======================================================================
# reading a log
# ======================================================================
def load_events(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return {}, data
    return data.get("meta") or {}, data.get("events") or []


def find_priority(events: list[dict[str, Any]]) -> str | None:
    """The run's goal: the spec event, else the human spec input, else the plan or rules artifacts."""
    for e in events:
        if e.get("t") == "spec" and (e.get("spec") or {}).get("priority"):
            return e["spec"]["priority"]
    for e in events:
        if e.get("t") == "human" and (e.get("input") or {}).get("kind") == "spec" and e["input"].get("priority"):
            return e["input"]["priority"]
    for e in events:
        if e.get("t") == "artifact" and str(e.get("path", "")).startswith(("plan/", "spec/")):
            try:
                content = json.loads(e.get("content") or "{}")
            except ValueError:
                continue
            if isinstance(content, dict) and content.get("priority") in GOAL_FIELD:
                return content["priority"]
    return None


def candidate_number(c: dict[str, Any]) -> int:
    digits = "".join(ch for ch in str(c.get("id", "")) if ch.isdigit())
    return int(digits) if digits else 0


def final_candidates(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The latest candidate event of each id."""
    final: dict[str, dict[str, Any]] = {}
    for e in events:
        if e.get("t") == "candidate" and isinstance(e.get("candidate"), dict):
            final[e["candidate"]["id"]] = e["candidate"]
    return final


def result_key(c: dict[str, Any]) -> tuple[Any, Any, Any] | None:
    ppa = c.get("ppa") or {}
    key = (ppa.get("cellCount"), ppa.get("areaUm2"), ppa.get("clockPeriodNs"))
    return None if any(v is None for v in key) else key


def last_artifact_json(events: list[dict[str, Any]], prefix: str) -> dict[str, Any] | None:
    found = None
    for e in events:
        if e.get("t") == "artifact" and str(e.get("path", "")).startswith(prefix):
            try:
                content = json.loads(e.get("content") or "")
            except ValueError:
                continue
            if isinstance(content, dict):
                found = content
    return found


def pct_change(base: float | None, value: float | None) -> float | None:
    if base is None or value is None or base == 0:
        return None
    return 100.0 * (value - base) / base


def analyze(path: Path, fallback_priority: str | None) -> RunStats:
    meta, events = load_events(path)
    stats = RunStats(path=str(path), run_id=str(meta.get("runId") or path.stem))
    spec = next((e.get("spec") for e in events if e.get("t") == "spec"), None) or {}
    stats.block = str(spec.get("presetId") or meta.get("presetId") or meta.get("title") or "?")
    stats.priority = find_priority(events) or fallback_priority

    cands = final_candidates(events)
    base = cands.get("c1")
    stats.v1_first_try = None if base is None else int(base.get("fixCount") or 0) == 0
    new = sorted((c for cid, c in cands.items() if cid != "c1"), key=candidate_number)
    stats.new_versions = len(new)
    stats.rejected = sum(1 for c in new if c.get("status") == "rejected")

    seen: dict[tuple[Any, Any, Any], str] = {}
    if base is not None and result_key(base) is not None and base.get("status") == "measured":
        seen[result_key(base)] = "c1"  # type: ignore[index]
    for c in new:
        key = result_key(c)
        if c.get("status") != "measured" or key is None:
            continue
        if key in seen:
            stats.copies += 1
        else:
            seen[key] = c["id"]

    goal_field = GOAL_FIELD.get(stats.priority or "")
    if goal_field is not None and base is not None:
        base_value = (base.get("ppa") or {}).get(goal_field)
        if base_value is not None:
            worse = 0
            for c in new:
                value = (c.get("ppa") or {}).get(goal_field)
                if c.get("status") == "measured" and value is not None and value > base_value:
                    worse += 1
            stats.worse_on_goal = worse

    win = next((e for e in reversed(events) if e.get("t") == "winner"), None)
    if win is not None:
        stats.winner = win.get("candidateId")
        won = cands.get(stats.winner or "")
        if won is not None and base is not None and won.get("id") != base.get("id"):
            for name, key in (("area", "areaUm2"), ("clock", "clockPeriodNs"), ("power", "powerMw")):
                stats.winner_change[name] = pct_change((base.get("ppa") or {}).get(key), (won.get("ppa") or {}).get(key))

    sheet = last_artifact_json(events, "signoff/")
    if sheet is not None:
        stats.eqy_v1 = sheet.get("eqyVsV1")
        stats.eqy_human = sheet.get("eqyVsHuman")
        tests = sheet.get("tests") or {}
        stats.seeds_passed, stats.seeds_run = tests.get("seedsPassed"), tests.get("seedsRun")
        strength = sheet.get("strength") or {}
        stats.planted = strength.get("planted")
        stats.caught = strength.get("caught")
        stats.harmless = strength.get("harmless")
    ref = next((e for e in reversed(events) if e.get("t") == "human_ref"), None)
    if ref is not None and stats.eqy_human is None:
        stats.eqy_human = (ref.get("eqy") or {}).get("status")
    if stats.eqy_v1 is None and win is not None:
        won = cands.get(stats.winner or "")
        if won is not None and won.get("eqy"):
            stats.eqy_v1 = won["eqy"].get("status")

    credits = [e for e in events if e.get("t") == "credits" and e.get("usedUsd") is not None]
    if credits:
        stats.spend_usd = float(credits[-1]["usedUsd"])
    if meta.get("durationMs") is not None:
        stats.wall_s = float(meta["durationMs"]) / 1000.0
    return stats


# ======================================================================
# printing
# ======================================================================
def money(x: float | None) -> str:
    return "n/a" if x is None else f"${x:.3f}"


def seconds(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.0f} s"


def count(x: int | None) -> str:
    return "n/a" if x is None else str(x)


def change_text(change: dict[str, float | None]) -> str:
    if not change:
        return "v1 kept"
    parts = [f"{name} {value:+.1f}%" for name, value in change.items() if value is not None]
    return ", ".join(parts) + " vs v1 (lower is better)" if parts else "no measurements"


def caught_text(s: RunStats) -> str:
    if s.planted is None or s.caught is None:
        return "n/a"
    got = s.caught + (s.harmless or 0)
    return f"{got} of {s.planted} ({s.caught} caught, {s.harmless or 0} harmless)"


def table(rows: list[tuple[str, str]], head: tuple[str, str] = ("measure", "value")) -> str:
    width = max(len(head[0]), *(len(label) for label, _ in rows))
    lines = [f"  {head[0]:<{width}}  {head[1]}", f"  {'-' * width}  {'-' * len(head[1])}"]
    lines += [f"  {label:<{width}}  {value}" for label, value in rows]
    return "\n".join(lines)


def run_table(s: RunStats) -> str:
    goal = s.priority or "unknown"
    worse_label = f"worse than v1 on the goal ({GOAL_NAME.get(goal, 'goal')})"
    first = "n/a" if s.v1_first_try is None else ("yes" if s.v1_first_try else "no")
    seeds = "n/a" if s.seeds_run is None else f"{s.seeds_passed} of {s.seeds_run}"
    rows = [
        ("block", s.block),
        ("priority", goal),
        ("v1 passed on the first try", first),
        ("new versions", count(s.new_versions)),
        ("copies of an earlier version", count(s.copies)),
        (worse_label, count(s.worse_on_goal)),
        ("rejected versions", count(s.rejected)),
        ("winner", s.winner or "n/a"),
        ("winner change", change_text(s.winner_change) if s.winner else "n/a"),
        ("EQY vs v1", s.eqy_v1 or "n/a"),
        ("EQY vs human", s.eqy_human or "n/a"),
        ("seeds passed", seeds),
        ("planted bugs caught or harmless", caught_text(s)),
        ("spend", money(s.spend_usd)),
        ("wall time", seconds(s.wall_s)),
    ]
    return f"Run {s.run_id}\n{table(rows)}"


def total_table(all_stats: list[RunStats]) -> str:
    def total(values: list[int | None]) -> int | None:
        present = [v for v in values if v is not None]
        return sum(present) if present else None

    def of(n: int, d: int) -> str:
        return f"{n} of {d}"

    runs = len(all_stats)
    first = [s for s in all_stats if s.v1_first_try is not None]
    moved = [s for s in all_stats if s.winner and s.winner != "c1"]
    v1_known = [s for s in moved if s.eqy_v1]
    human_known = [s for s in all_stats if s.eqy_human]
    seeds = [s for s in all_stats if s.seeds_run]
    planted = [s for s in all_stats if s.planted]
    got = sum((s.caught or 0) + (s.harmless or 0) for s in planted)
    spend = [s.spend_usd for s in all_stats if s.spend_usd is not None]
    wall = [s.wall_s for s in all_stats if s.wall_s is not None]
    worse = total([s.worse_on_goal for s in all_stats])
    rows = [
        ("runs", str(runs)),
        ("v1 passed on the first try", of(sum(1 for s in first if s.v1_first_try), len(first))),
        ("new versions", count(total([s.new_versions for s in all_stats]))),
        ("copies of an earlier version", count(total([s.copies for s in all_stats]))),
        ("worse than v1 on the goal", count(worse)),
        ("rejected versions", count(total([s.rejected for s in all_stats]))),
        ("winner is not v1", of(len(moved), runs)),
        ("EQY vs v1 proven (winner not v1)", of(sum(1 for s in v1_known if s.eqy_v1 == "proven"), len(v1_known))),
        ("EQY vs human proven", of(sum(1 for s in human_known if s.eqy_human == "proven"), len(human_known))),
        ("seeds passed", of(sum(s.seeds_passed or 0 for s in seeds), sum(s.seeds_run or 0 for s in seeds))),
        ("planted bugs caught or harmless", of(got, sum(s.planted or 0 for s in planted))),
        ("spend", money(sum(spend) if spend else None)),
        ("wall time", seconds(sum(wall) if wall else None)),
    ]
    return f"Total\n{table(rows)}"


def collect(inputs: list[str]) -> list[Path]:
    files: list[Path] = []
    for item in inputs:
        p = Path(item)
        if p.is_dir():
            files += sorted(p.glob("*.json"))
        elif p.is_file():
            files.append(p)
        else:
            print(f"quality_report: not found: {item}", file=sys.stderr)
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Quality report over saved Verity run logs (GET /runs/{id}/log).")
    parser.add_argument("paths", nargs="+", help="run log JSON files, or folders holding them")
    parser.add_argument("--priority", choices=sorted(GOAL_FIELD), help="goal to use when a log does not say")
    args = parser.parse_args(argv)
    files = collect(args.paths)
    if not files:
        print("quality_report: no run logs found", file=sys.stderr)
        return 2
    all_stats: list[RunStats] = []
    for path in files:
        try:
            all_stats.append(analyze(path, args.priority))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"quality_report: skipped {path}: {exc}", file=sys.stderr)
    if not all_stats:
        return 2
    for stats in all_stats:
        if stats.priority is None:
            print(f"quality_report: {stats.run_id}: no priority in the log, pass --priority", file=sys.stderr)
        print(run_table(stats))
        print()
    print(total_table(all_stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
