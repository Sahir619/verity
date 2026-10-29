"""Waveform of one test case (CONTRACTS 6.3, artifact waves/<candidateId>.json).

Two callers: the build loop, for a version that failed its tests, right before its repair (the cycle table goes into the
repair prompt); and the sign-off, for the winner (its first directed case, since it passes). One sandbox call each
(/verity/bin/wave). A waveform is evidence only: any problem means no waveform, never a failed run.
"""

import asyncio
import logging

from ..contracts import to_json
from ..contracts.signoff import Waves, WaveSignal
from ..core.errors import RunStopped
from ..core.results import WaveTrace
from ..core.rtl import Rtl
from .candidates import Cand
from .state import RunState

log = logging.getLogger("verity.orchestrator")


async def capture(st: RunState, rtl: Rtl, case: str | None) -> WaveTrace | None:
    """Simulate one case (None: the first case) with a waveform dump. None when anything goes wrong."""
    ctx = st.ctx
    try:
        with ctx.busy():
            result = await ctx.tools.waveform(rtl, ctx.pack, case)
    except (asyncio.CancelledError, RunStopped):
        raise
    except Exception as exc:  # a missing script in an older image, a sandbox problem: no waveform
        log.warning("run %s: waveform failed: %s", ctx.run_id, exc)
        return None
    if result.status != "ok" or result.wave is None:
        log.info("run %s: no waveform (%s): %s", ctx.run_id, result.status, result.message)
        return None
    return result.wave


def to_wire(cand: Cand, wave: WaveTrace) -> Waves:
    return Waves(
        candidate_id=cand.id,
        case=wave.case,
        fail_cycle=wave.fail_cycle,
        start_cycle=wave.start_cycle,
        signals=[WaveSignal(name=s.name, width=s.width, got=list(s.got), expected=s.expected) for s in wave.signals],
        note=wave.note or None,
    )


async def publish(st: RunState, cand: Cand, wave: WaveTrace, *, file_name: str) -> None:
    """Emit waves/<candidateId>.json (a later one for the same version replaces it) and keep a run file."""
    body = to_json(to_wire(cand, wave))
    await st.em.artifact(f"waves/{cand.id}.json", body)
    await st.save(f"candidates/{cand.id}/{file_name}", body, "application/json")
