"""Export the real CPU jobs of one parent job to a landing run file. Read-only: only GET requests, no spend.

Fetches the parent CPU job and its upgrade jobs from a running backend, keeps the fields the landing page needs
(verbatim values, wire field names, job ids, times, spend) and writes frontend/public/runs/cpu-real.json. The upgrade
jobs are found through GET /cpu-jobs/mine (jobs whose parentJobId is the parent) and any --child ids. It never calls
/upgrades (that route can run research) and never creates a job.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe evals/export_cpu_fixture.py --parent cpu_ffd87205211749d985d0db0882abcd70 \
        --child cpu_05332b01dae74315af9cf9c178557991 --child cpu_e97a3b18caf84384ad65fc4e77250282

The output must not be hand-edited: run this script again instead.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT.parent / "frontend" / "public" / "runs" / "cpu-real.json"

NOTE = (
    "Exported by backend/evals/export_cpu_fixture.py from real Nebius CPU jobs (Nemotron wrote every block). "
    "Values are copied from the job records, never typed, except the provider, which this script sets because the job record does not store it. "
    "Do not hand-edit: run the script again."
)
DASHES = ("\u2013", "\u2014")


def _get(base: str, path: str) -> dict:
    try:
        with urllib.request.urlopen(f"{base.rstrip('/')}{path}", timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"GET {path} failed: HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise SystemExit(f"GET {path} failed: {exc.reason}") from None


def _profile(profile: dict | None) -> dict | None:
    """The numbers of one benchmarked core. Per-stall cycle splits and the block area map are kept; messages are not."""
    if profile is None:
        return None
    return {
        "cpi": profile["cpi"],
        "clockNs": profile["clockNs"],
        "waitCycles": profile["waitCycles"],
        "benchTimeUs": profile["benchTimeUs"],
        "cyclesByStall": profile["cyclesByStall"],
        "areaByBlockUm2": profile["areaByBlockUm2"],
        "bench": [
            {k: b[k] for k in ("name", "instructions", "cycles", "cpi", "passed")} for b in profile["bench"]
        ],
    }


def _block(block: dict) -> dict:
    summary = block.get("summary") or {}
    return {
        "blockId": block["blockId"],
        "title": block["title"],
        "status": block["status"],
        "attempts": block["attempts"],
        "reused": block["reused"],
        "usedVersion": block["usedVersion"],
        "runId": block["runId"],
        "winner": summary.get("winner"),
        "usd": (summary.get("outcome") or {}).get("usd"),
        "evidence": block["evidence"],
    }


def _version(version: dict) -> dict:
    return {
        "version": version["version"],
        "changedBlock": version["changedBlock"],
        "totals": version["totals"],
        "coreEvidence": version["coreEvidence"],
        "rvfiPassed": version["rvfiPassed"],
        "rvfiTotal": version["rvfiTotal"],
        "rvfiTimeouts": version["rvfiTimeouts"],
        "rvfiFailed": version["rvfiFailed"],
        "profile": _profile(version["profile"]),
        "flashProfile": _profile(version["flashProfile"]),
        "profileNote": version["profileNote"],
    }


def _comparison(comparison: dict | None) -> dict | None:
    if comparison is None:
        return None

    def mode(m: dict | None) -> dict | None:
        if m is None:
            return None
        return {
            "waitCycles": m["waitCycles"],
            "parent": _profile(m["parent"]),
            "child": _profile(m["child"]),
            "cpiDeltaPct": m["cpiDeltaPct"],
            "benchTimeRatio": m["benchTimeRatio"],
            "bench": m["bench"],
        }

    out = {k: comparison[k] for k in ("parentAreaUm2", "areaUm2", "areaDeltaPct", "parentClockNs", "clockNs", "clockDeltaPct")}
    out["fast"] = mode(comparison["fast"])
    out["flash"] = mode(comparison["flash"])
    return out


def _job(job: dict) -> dict:
    core = job["core"]
    return {
        "jobId": job["id"],
        "specId": job["specId"],
        "goal": job["goal"],
        "status": job["status"],
        "createdAt": job["createdAt"],
        "finishedAt": job["finishedAt"],
        "spentUsd": job["spentUsd"],
        "message": job["message"],
        "parentJobId": job["parentJobId"],
        "upgradeId": job["upgradeId"],
        "blocks": [_block(b) for b in job["blocks"]],
        "core": {
            "status": core["status"],
            "runId": core["runId"],
            "winner": (core.get("summary") or {}).get("winner"),
            "evidence": core["evidence"],
        },
        "versions": [_version(v) for v in job["versions"]],
        "comparison": _comparison(job["comparison"]),
    }


def _require_passed(job: dict, what: str) -> None:
    if job["status"] != "passed" or job["core"]["status"] != "passed":
        raise SystemExit(f"{what} {job['id']} is {job['status']} (core {job['core']['status']}), not passed.")


def main(args: argparse.Namespace) -> int:
    parent = _get(args.base_url, f"/cpu-jobs/{args.parent}")
    _require_passed(parent, "Parent job")
    if parent["parentJobId"] is not None:
        raise SystemExit(f"{parent['id']} is an upgrade job, not a parent.")

    child_ids = list(args.child)
    for job in _get(args.base_url, "/cpu-jobs/mine")["jobs"]:
        if job["parentJobId"] == parent["id"] and job["id"] not in child_ids:
            child_ids.append(job["id"])
    children = []
    for child_id in child_ids:
        child = _get(args.base_url, f"/cpu-jobs/{child_id}")
        _require_passed(child, "Upgrade job")
        if child["parentJobId"] != parent["id"]:
            raise SystemExit(f"{child_id} does not upgrade {parent['id']} (parentJobId {child['parentJobId']}).")
        children.append(child)
    if not children:
        raise SystemExit("No upgrade jobs found. Pass --child <job id> for each one.")
    children.sort(key=lambda j: j["createdAt"])

    out = {
        "note": NOTE,
        "recordedAt": parent["createdAt"][:10],
        # Set here, not read: the job record does not store the provider (these jobs ran on Nebius Token Factory).
        "provider": "nebius",
        "parent": _job(parent),
        "upgrades": [_job(c) for c in children],
    }
    text = json.dumps(out, indent=2, ensure_ascii=False) + "\n"
    for dash in DASHES:
        if dash in text:
            raise SystemExit("A job field holds an en or em dash; fix the source text, do not edit the output.")
    path = Path(args.out).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    spend = parent["spentUsd"] + sum(c["spentUsd"] for c in children)
    print(f"wrote {path} ({len(text.encode('utf-8'))} bytes)")
    print(f"parent {parent['id']} ${parent['spentUsd']:.4f}")
    for c in children:
        print(f"upgrade {c['upgradeId']} {c['id']} ${c['spentUsd']:.4f}")
    print(f"total ${spend:.4f}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--parent", required=True, help="id of the passed parent CPU job")
    parser.add_argument("--child", action="append", default=[], help="id of an upgrade job (repeatable)")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    sys.exit(main(parser.parse_args()))
