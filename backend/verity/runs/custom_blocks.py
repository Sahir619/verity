"""CE3: locking a drafted custom block in at Approve (runs/manager.py `approve_custom_block`). Writes the pack
files, re-runs the self-check against the data that will actually be written (never trusts a round-tripped wire
payload blindly), writes the manifest, loads it, and registers it. Nothing here ever touches `backend/tasks/`."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from ..contracts import CustomBlockDraft
from ..core.errors import PackFormatError, PackIntegrityError
from ..core.pack import TaskPack, write_manifest
from ..core.protocols import ToolRunner

SHARED_TESTBENCH = "_custom_shared/test_dut.py"


class ApproveFailed(Exception):
    """The draft could not be locked in: a shape problem, a re-check failure, or an id already taken."""


def _q(text: str) -> str:
    return json.dumps(text, ensure_ascii=True)


def _task_toml(draft: CustomBlockDraft) -> str:
    lines = [
        'schema = "verity.task/1"',
        f"id = {_q(draft.id)}",
        f"title = {_q(draft.title)}",
        f"summary = {_q(draft.summary)}",
        f"spec_text = {_q(draft.spec_text)}",
        f"top = {_q(draft.top)}",
        'family = "custom"',
        f'element_type = "{draft.element_type}"',
        f"latency_cycles = {draft.latency_cycles}",
        "max_lines = 300",
        f"test_count = {len(draft.cases)}",
        "is_custom = true",
        'default_priority = "area"',
        'priorities = ["area", "speed", "power"]',
        "",
        "[clock]",
        f"name = {_q(draft.clock_name)}",
        "",
        "[reset]",
        f"name = {_q(draft.reset_name)}",
        "",
    ]
    for port in draft.ports:
        lines += ["[[ports]]", f"name = {_q(port.name)}", f'dir = "{port.dir}"', f"width = {port.width}", f"signed = {str(port.signed).lower()}", ""]
    return "\n".join(lines)


def _cases_json(draft: CustomBlockDraft) -> str:
    return json.dumps(
        {
            "cases": [
                {"name": c.name, "steps": [{"inputs": s.inputs, "rst": s.rst, "expected": s.expected} for s in c.steps]}
                for c in draft.cases
            ]
        },
        indent=2,
    )


def _cases_payload(draft: CustomBlockDraft) -> list[dict]:
    return json.loads(_cases_json(draft))["cases"]


async def approve_custom_block(draft: CustomBlockDraft, *, tools: ToolRunner, custom_dir: Path, tasks_dir: Path) -> TaskPack:
    """Writes the pack under `custom_dir/<id>/`, re-checks it, loads it. Raises ApproveFailed (nothing left on
    disk) on any problem; the caller still owns registering the returned pack with the run manager."""
    if (custom_dir / draft.id).exists():
        raise ApproveFailed(f"a custom block named '{draft.id}' already exists.")
    result = await tools.check_golden(draft.golden_py, _cases_payload(draft))
    if not result.passed_all(len(draft.cases)):
        raise ApproveFailed(f"the golden model no longer passes its own worked examples: {result.message or 'see the failing cases'}.")

    shared_testbench = tasks_dir / SHARED_TESTBENCH
    root = custom_dir / draft.id
    root.mkdir(parents=True)
    try:
        (root / "task.toml").write_text(_task_toml(draft), encoding="utf-8")
        (root / "golden.py").write_text(draft.golden_py, encoding="utf-8")
        (root / "cases.json").write_text(_cases_json(draft), encoding="utf-8")
        (root / "test_dut.py").write_text(shared_testbench.read_text(encoding="utf-8"), encoding="utf-8")
        write_manifest(root)
        return TaskPack.load(root)
    except (OSError, PackFormatError, PackIntegrityError) as exc:
        shutil.rmtree(root, ignore_errors=True)
        raise ApproveFailed(f"could not lock the block in: {exc}") from exc
