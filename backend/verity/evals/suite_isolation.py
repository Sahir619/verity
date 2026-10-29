"""`isolation` suite ($0, no LLM): the golden model stays out of reach and banned RTL never reaches a simulator.

Per pack, one row with these checks (`extra.checks` lists each as name, ok, detail):
  banned_rejected      reference.v plus each banned construct (initial block, system task, `include, force, non-ASCII,
                       a second module) must raise RtlRejected before any sandbox call
  sandbox_copy_clean   copy_for_sandbox() has no reference.v and no MANIFEST.sha256 and every file matches the pack
  brief_no_leak        public_brief() (the only pack text a role sees) contains no line of golden.py, test_dut.py or
                       reference.v
  pack_hash_stable     the pack hash is unchanged after the checks
  width_lock           (sandbox) the matmul4 reference renamed to mm4_int4 fails the interface check with a port
                       width message and never reaches simulation
"""

import logging
import re
import tempfile
from pathlib import Path

from ..core.errors import RtlRejected, ToolInfraError
from ..core.pack import REFERENCE_FILE, MANIFEST_NAME, TaskPack
from .harness import EvalEnv, pack_unchanged
from .record import EvalRecord

log = logging.getLogger("verity.evals")

# name -> text inserted before the last `endmodule`.
BANNED_SNIPPETS: dict[str, str] = {
    "initial_block": "  initial begin\n  end\n",
    "system_task": '  always @(*) $display("x");\n',
    "include": '`include "other.v"\n',
    "force": "  always @(*) force dummy_net = 1'b0;\n",
    "dpi": '  import "DPI-C" function void leak();\n',
}
MIN_LEAK_LINE = 24  # shorter lines (keywords, `end`, `endmodule`) are not evidence of a leak


def with_snippet(reference: str, snippet: str) -> str:
    index = reference.rfind("endmodule")
    return reference + "\n" + snippet if index < 0 else reference[:index] + snippet + reference[index:]


def banned_variants(reference: str) -> dict[str, str]:
    variants = {name: with_snippet(reference, snippet) for name, snippet in BANNED_SNIPPETS.items()}
    variants["non_ascii"] = with_snippet(reference, "  // caf" + chr(0xE9) + " is not ascii\n")
    variants["second_top_module"] = reference + "\n" + reference
    return variants


def leaked_lines(brief: str, secret_text: str) -> list[str]:
    """Lines of `secret_text` (long enough to be meaningful) that also appear in `brief`."""
    hits: list[str] = []
    for raw in secret_text.splitlines():
        line = raw.strip()
        if len(line) >= MIN_LEAK_LINE and line in brief:
            hits.append(line[:60])
    return hits


async def run(env: EvalEnv, packs: list[TaskPack], repeats: int) -> None:
    del repeats
    for pack in packs:
        record = env.new_record("isolation", pack.id)
        checks: list[dict[str, object]] = []
        record.extra["checks"] = checks
        await _pack_checks(env, pack, checks)
        if pack.id == "mm4_int4" and "matmul4" in env.packs and not env.skip_sandbox:
            await _width_lock(env, pack, env.packs["matmul4"], checks)
        record.pack_hash_ok = pack_unchanged(pack)
        checks.append({"name": "pack_hash_stable", "ok": bool(record.pack_hash_ok), "detail": pack.sha256[:12]})
        failed = [str(c["name"]) for c in checks if not c["ok"]]
        record.ok = not failed
        record.error = ("failed: " + ", ".join(failed)) if failed else None
        await env.write(record)


async def _pack_checks(env: EvalEnv, pack: TaskPack, checks: list[dict[str, object]]) -> None:
    reference = pack.reference_rtl()
    if reference is None:
        checks.append({"name": "banned_rejected", "ok": False, "detail": "no reference.v"})
    else:
        accepted: list[str] = []
        for name, text in banned_variants(reference).items():
            try:
                pack.validate_rtl(text, max_bytes=env.settings.rtl.max_bytes)
                accepted.append(name)
            except RtlRejected:
                pass
        checks.append({"name": "banned_rejected", "ok": not accepted, "detail": ", ".join(accepted) or "all rejected"})

    with tempfile.TemporaryDirectory(prefix="verity-iso-") as tmp:
        dest = Path(tmp) / "pack"
        try:
            pack.copy_for_sandbox(dest)
            files = {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()}
            leaked = sorted(files & {REFERENCE_FILE, MANIFEST_NAME})
            checks.append({"name": "sandbox_copy_clean", "ok": not leaked, "detail": ", ".join(leaked) or f"{len(files)} files"})
        except Exception as exc:
            checks.append({"name": "sandbox_copy_clean", "ok": False, "detail": f"{type(exc).__name__}: {exc}"})

    brief = pack.public_brief()
    leaks: list[str] = []
    for name in ("golden.py", "test_dut.py", REFERENCE_FILE):
        path = pack.root / name
        if path.is_file():
            leaks += [f"{name}: {line}" for line in leaked_lines(brief, path.read_text(encoding="utf-8"))]
    checks.append({"name": "brief_no_leak", "ok": not leaks, "detail": "; ".join(leaks[:3]) or "clean"})


async def _width_lock(env: EvalEnv, narrow: TaskPack, wide: TaskPack, checks: list[dict[str, object]]) -> None:
    """Feed the wide pack's reference as a candidate for the narrow pack: the interface check must stop it."""
    reference = wide.reference_rtl()
    if reference is None:
        checks.append({"name": "width_lock", "ok": False, "detail": "matmul4 has no reference.v"})
        return
    renamed = re.sub(rf"\bmodule\s+{re.escape(wide.top)}\b", f"module {narrow.top}", reference, count=1)
    try:
        rtl = narrow.validate_rtl(renamed, max_bytes=env.settings.rtl.max_bytes)
        result = await env.tools.verify(rtl, narrow)
    except (RtlRejected, ToolInfraError) as exc:
        checks.append({"name": "width_lock", "ok": False, "detail": f"{type(exc).__name__}: {exc}"})
        return
    errors = result.contract.errors if result.contract else []
    ok = result.status == "contract_fail" and any("width" in e for e in errors) and result.sim is None
    checks.append({"name": "width_lock", "ok": ok, "detail": errors[0] if errors else result.status})
