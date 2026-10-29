"""CE3 acceptance gate: drives the 5 sample custom blocks (CRC-8, priority encoder, popcount, saturating adder,
LFSR) through the real thread-agent path end to end: draft_custom_block -> self-check -> Approve -> a run on the
new preset -> wait for it to finish. Reports pass/fail per block and whether 4 of 5 passed.

Fake provider ($0): exercises drafting, validation and self-check for real, and a real run to the point RTL would
be asked for (it stops there with a plain error unless a cassette exists under evals/cassettes/<id>/rtl_gen.txt,
since custom packs have no reference.v for the fake provider to echo back). A `--full` run needs the real provider
(real Nemotron spend) to get a real design through the whole pipeline; it is never started without that flag.

Usage (from backend/):
    .venv/Scripts/python.exe evals/custom_blocks_gate.py            # draft, self-check, approve; $0
    .venv/Scripts/python.exe evals/custom_blocks_gate.py --full      # also starts a real run on each (spends real $)
"""

from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from verity.ask.custom_draft import DraftFailed, draft_custom_block  # noqa: E402
from verity.config import load_settings  # noqa: E402
from verity.core.pack import load_packs  # noqa: E402
from verity.llm.fake import FakeProvider  # noqa: E402
from verity.llm.factory import build_provider  # noqa: E402
from verity.runs.custom_blocks import ApproveFailed, approve_custom_block  # noqa: E402
from verity.runs.manager import RunManager  # noqa: E402
from verity.store import SqliteStore  # noqa: E402
from verity.tools.factory import build_tool_runner  # noqa: E402

SAMPLE_BLOCKS = [
    ("crc8", "a CRC-8 checksum over an 8-bit input stream, one byte per cycle, polynomial 0x07, with a synchronous reset that clears the CRC register to 0"),
    ("priority_encoder", "an 8-bit priority encoder: a 3-bit output is the index of the highest set bit of an 8-bit input, and a valid output is 0 when the input is all zero, combinational"),
    ("popcount8", "a combinational popcount: count the set bits of an 8-bit input, 4-bit output"),
    ("sat_adder8", "an 8-bit saturating adder: adds two 8-bit unsigned inputs and clamps the result to 255 instead of wrapping on overflow, combinational"),
    ("lfsr8", "an 8-bit Fibonacci LFSR with taps at bits 7, 5, 4 and 3 (maximal-length polynomial), shifting one bit per clock edge with a synchronous reset that loads it with 1"),
]
GATE_PASS_COUNT = 4


@dataclass
class BlockResult:
    id: str
    drafted: bool = False
    approved: bool = False
    run_status: str | None = None
    error: str = ""

    @property
    def passed(self) -> bool:
        return self.approved and (self.run_status is None or self.run_status == "done")


async def run_gate(full: bool) -> list[BlockResult]:
    settings = load_settings()
    store = SqliteStore(settings)
    await store.open()
    packs = load_packs(settings.tasks_dir)
    provider = build_provider(settings) if full else FakeProvider(settings, delay_s=0)
    tools = build_tool_runner(settings)
    manager = RunManager(settings, store, provider, tools, packs, research=None)

    results: list[BlockResult] = []
    for block_id, description in SAMPLE_BLOCKS:
        result = BlockResult(id=block_id)
        try:
            draft, _usd = await draft_custom_block(provider, tools, settings, description, set(manager.presets()))
        except DraftFailed as exc:
            result.error = f"draft: {exc.reason}"
            results.append(result)
            continue
        except Exception as exc:  # noqa: BLE001  (a sandbox-down or similar infra error fails this block, not the gate)
            result.error = f"draft: {exc}"
            results.append(result)
            continue
        result.drafted = True
        try:
            pack = await approve_custom_block(
                draft, tools=tools, custom_dir=settings.data_dir / "custom_packs", tasks_dir=settings.tasks_dir
            )
        except ApproveFailed as exc:
            result.error = f"approve: {exc}"
            results.append(result)
            continue
        except Exception as exc:  # noqa: BLE001
            result.error = f"approve: {exc}"
            results.append(result)
            continue
        manager.register_custom_pack(pack)
        result.approved = True

        session_id = f"gate-{block_id}"
        try:
            run = await manager.create_run(session_id=session_id, preset_id=pack.id, priority="area", mode="fast")
        except Exception as exc:  # noqa: BLE001  (admission can fail for many plain reasons; report, do not crash the gate)
            result.error = f"create_run: {exc}"
            results.append(result)
            continue

        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            record = await manager.get_run(run.id)
            if record is not None and record.status not in ("queued", "running"):
                result.run_status = record.status
                break
            await asyncio.sleep(2)
        else:
            result.run_status = "timed_out"
        results.append(result)

    await manager.shutdown()
    await store.close()
    return results


def main() -> None:
    full = "--full" in sys.argv
    if full:
        print("--full: this will spend real money on real model calls.", file=sys.stderr)
    results = asyncio.run(run_gate(full))
    passed = 0
    for r in results:
        ok = r.passed
        passed += 1 if ok else 0
        detail = r.error or f"run {r.run_status}" if r.approved else (r.error or "not approved")
        print(f"{'PASS' if ok else 'FAIL'} {r.id}: {detail}")
    print(f"\n{passed} of {len(results)} passed (gate needs {GATE_PASS_COUNT})")
    sys.exit(0 if passed >= GATE_PASS_COUNT else 1)


if __name__ == "__main__":
    main()
