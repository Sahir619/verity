"""Write a fake-run RunLog and let the web's own validator judge it.

Shells out to `node frontend/scripts/validate-fixture.ts <path>` (Node 24 type stripping, same as `npm run validate:fixture`).
Skipped when node is missing, when frontend/node_modules has no zod, or when the script cannot take a path argument yet
(that is a frontend hookup item: today it always validates public/runs/golden.json, so a pass would prove nothing).

Known frontend mismatches that this test tolerates and prints instead of failing on (see backend/README.md checklist):
  - "stored weights do not match priority": the script wants the BASE weights, the backend stores the EFFECTIVE
    (renormalized) weights because power is not measured (CONTRACTS S8), so the UI breakdown rows add up.
  - "RTL has N lines (expected 20 to 40)": a fixture-hygiene rule for the sample recording, not for live runs.
"""

import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from verity.contracts import to_json
from verity.runs.runlog import build_run_log
from verity_test_support import WEB_ROOT, execute_fake_run, make_settings

SCRIPT = WEB_ROOT / "scripts" / "validate-fixture.ts"
TOLERATED = (
    re.compile(r"stored weights do not match priority"),
    re.compile(r"attempt \d+ RTL has \d+ lines"),
)


def _script_takes_a_path() -> bool:
    return SCRIPT.is_file() and "process.argv" in SCRIPT.read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.skipif(not SCRIPT.is_file(), reason="frontend/scripts/validate-fixture.ts not found")
@pytest.mark.skipif(not (WEB_ROOT / "node_modules" / "zod").is_dir(), reason="frontend/node_modules is not installed (npm ci)")
@pytest.mark.skipif(
    SCRIPT.is_file() and not _script_takes_a_path(),
    reason="validate-fixture.ts has no path argument yet (frontend hookup item); it would validate golden.json instead",
)
def test_node_accepts_a_recorded_fake_run(tmp_path: Path, packs) -> None:
    settings = make_settings(tmp_path / "data")

    async def record() -> str:
        run = await execute_fake_run(settings, packs["mac4"], mode="full")
        try:
            assert run.error is None, run.error
            record_row = await run.store.get_run(run.run_id)
            assert record_row is not None
            return to_json(build_run_log(record_row, run.events, settings))
        finally:
            await run.store.close()

    path = tmp_path / "fake-run.json"
    path.write_text(asyncio.run(record()), encoding="utf-8")
    json.loads(path.read_text(encoding="utf-8"))  # valid JSON before node sees it

    done = subprocess.run(
        ["node", str(SCRIPT), str(path)], capture_output=True, text=True, timeout=120, cwd=str(WEB_ROOT), check=False
    )
    problems = [line.strip() for line in done.stderr.splitlines() if line.startswith("  ")]
    schema_failure = "does not match RunLogSchema" in done.stderr
    assert not schema_failure, "zod rejected the run log:\n" + done.stderr
    hard = [p for p in problems if not any(rx.search(p) for rx in TOLERATED)]
    assert not hard, "validate-fixture.ts reported problems:\n" + "\n".join(hard)
    if done.returncode != 0:
        print("tolerated validator complaints:\n" + "\n".join(problems))
