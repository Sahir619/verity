"""backend/evals/quality_report.py on a tiny synthetic run log (the GET /runs/{id}/log shape)."""

import importlib.util
import json
import re
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "evals" / "quality_report.py"


@pytest.fixture(scope="module")
def qr():
    spec = importlib.util.spec_from_file_location("quality_report", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cand(cid: str, area=None, clock=None, cells=10, status="measured", fix=0, power=None) -> dict:
    ppa = None if area is None else {"areaUm2": area, "cellCount": cells, "clockPeriodNs": clock, "powerMw": power, "tools": [], "pdk": "Nangate45"}
    return {"t": "candidate", "candidate": {"id": cid, "label": "v" + cid[1:], "createdBy": "OPTIMIZE", "status": status, "fixCount": fix, "reason": "r", "ppa": ppa}}


def log(priority: str | None = "speed") -> dict:
    events: list[dict] = []
    if priority:
        events.append({"t": "spec", "spec": {"presetId": "cnt8", "priority": priority}})
    events += [
        {"t": "credits", "usedUsd": 0.01, "capUsd": 0.5},
        cand("c1", 100.0, 1.0, power=0.010),
        cand("c2", 90.0, 1.1, power=0.009),  # worse on speed
        cand("c2", 90.0, 1.1, power=0.009),  # a repeated event: only the latest counts
        cand("c3", 90.0, 1.1, power=0.009),  # copy of c2
        cand("c4", status="rejected"),
        cand("c5", 95.0, 0.9, power=0.011),  # better on speed
        cand("c6", 100.0, 1.0, power=0.010),  # copy of v1
        cand("c7", 80.0, 1.0, cells=None, power=0.010),  # incomplete measurement: never a copy
        {"t": "winner", "candidateId": "c5", "by": "ai", "aiPickId": "c5"},
        {"t": "human_ref", "ppa": {"areaUm2": 70.0, "pdk": "Nangate45"}, "eqy": {"status": "proven", "againstId": "c5"}},
        {
            "t": "artifact",
            "path": "signoff/c5.json",
            "content": json.dumps(
                {
                    "candidateId": "c5",
                    "tests": {"seedsRun": 9, "seedsPassed": 8},
                    "eqyVsV1": "proven",
                    "eqyVsHuman": "inconclusive",
                    "strength": {"planted": 6, "caught": 4, "harmless": 1, "undecided": 1, "status": "ok"},
                }
            ),
        },
        {"t": "credits", "usedUsd": 0.05, "capUsd": 0.5},
    ]
    return {"meta": {"runId": "r_x", "presetId": "cnt8", "durationMs": 12500}, "events": events}


def test_one_run_counts(qr, tmp_path: Path) -> None:
    path = tmp_path / "r_x.json"
    path.write_text(json.dumps(log()), encoding="utf-8")
    s = qr.analyze(path, None)
    assert (s.run_id, s.block, s.priority) == ("r_x", "cnt8", "speed")
    assert s.v1_first_try is True
    assert s.new_versions == 6
    assert s.copies == 2, "c3 repeats c2 and c6 repeats v1; c7 has no cell count; the rejected c4 has no result"
    assert s.worse_on_goal == 2, "c2 and c3 have a longer clock than v1; c6 is equal, which is not worse"
    assert s.rejected == 1
    assert s.winner == "c5"
    assert s.winner_change["clock"] == pytest.approx(-10.0) and s.winner_change["area"] == pytest.approx(-5.0)
    assert s.winner_change["power"] == pytest.approx(10.0)
    assert (s.eqy_v1, s.eqy_human) == ("proven", "inconclusive"), "the sign-off sheet is read before human_ref"
    assert (s.seeds_passed, s.seeds_run) == (8, 9)
    assert (s.planted, s.caught, s.harmless) == (6, 4, 1)
    assert s.spend_usd == pytest.approx(0.05) and s.wall_s == pytest.approx(12.5)


def test_the_goal_metric_follows_the_priority(qr, tmp_path: Path) -> None:
    path = tmp_path / "r_x.json"
    path.write_text(json.dumps(log("area")), encoding="utf-8")
    assert qr.analyze(path, None).worse_on_goal == 0, "no version has a larger area than v1"
    path.write_text(json.dumps(log("power")), encoding="utf-8")
    assert qr.analyze(path, None).worse_on_goal == 1, "only c5 draws more power than v1"


def test_priority_falls_back_to_the_argument_and_a_fix_count_is_not_a_first_try(qr, tmp_path: Path) -> None:
    data = log(None)
    data["events"][1] = cand("c1", 100.0, 1.0, fix=2)
    path = tmp_path / "r_x.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert qr.analyze(path, None).worse_on_goal is None, "no priority anywhere: not counted"
    s = qr.analyze(path, "speed")
    assert s.priority == "speed" and s.worse_on_goal == 2 and s.v1_first_try is False


def test_cli_prints_a_table_per_run_and_a_total_for_files_and_folders(qr, tmp_path: Path, capsys) -> None:
    (tmp_path / "a.json").write_text(json.dumps(log()), encoding="utf-8")
    second = log()
    second["meta"]["runId"] = "r_y"
    (tmp_path / "b.json").write_text(json.dumps(second), encoding="utf-8")
    assert qr.main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Run r_x" in out and "Run r_y" in out and "\nTotal\n" in out
    assert re.search(r"copies of an earlier version\s+2\n", out.split("\nTotal\n")[0])
    total = out.split("\nTotal\n")[1]
    assert re.search(r"runs\s+2\n", total) and re.search(r"copies of an earlier version\s+4\n", total)
    assert re.search(r"planted bugs caught or harmless\s+10 of 12\n", total)
    assert re.search(r"spend\s+\$0\.100\n", total)
    assert "\u2013" not in out and "\u2014" not in out and out.isascii()
    assert qr.main([str(tmp_path / "a.json")]) == 0
    assert qr.main([str(tmp_path / "missing.json")]) == 2
