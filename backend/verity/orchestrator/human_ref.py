"""The human-reference measurement (bench "Verity vs human"): reference.v measured with the same tools as the
candidates, plus EQY of the reference (gold) against the winner (gate).

The reference text is never passed to a role or an LLM. This stage is not an LLM call and never raises (except for
stop and cancel): a failure yields null metrics and/or an inconclusive proof, and the run's status is untouched.
"""

import asyncio
import logging

from ..contracts import PPA, Equivalence
from ..core import captions as cap
from ..core.errors import RtlRejected
from ..core.results import DEFAULT_PDK, not_run_equivalence
from ..core.rtl import Rtl
from ..core.types import RunStopped
from . import evolve
from .state import RunState

log = logging.getLogger("verity.orchestrator")

# Human-written files may be longer than the LLM line limit; every other Rtl check still applies.
REFERENCE_MAX_LINES = 2_000
REFERENCE_MAX_BYTES = 200_000


async def measure_human_ref(st: RunState, winner_id: str) -> Equivalence | None:
    """Emit one human_ref event for the winner, or nothing when the pack has no reference.v. Returns the proof it sent
    (the sign-off reports it as eqyVsHuman), or None when it sent none."""
    ctx, em, pack = st.ctx, st.em, st.ctx.pack
    try:
        text = pack.reference_rtl()
        winner = st.cands.get(winner_id)
        if text is None or winner is None or winner.rtl_obj is None:
            return None
        ppa = PPA(pdk=DEFAULT_PDK)
        eqy = Equivalence(status="inconclusive", against_id=winner_id)
        if ctx.ledger.exhausted() == "max_wall_s":
            eqy = not_run_equivalence(winner_id, "Not run: the time limit was reached.")
            await em.human_ref(ppa, eqy)
            return eqy
        await em.caption(cap.measuring_human_reference())
        try:
            ref = Rtl.validate(text, top=pack.top, max_lines=REFERENCE_MAX_LINES, max_bytes=REFERENCE_MAX_BYTES)
        except RtlRejected as exc:
            log.warning("run %s: reference.v rejected: %s", ctx.run_id, exc)
            await em.human_ref(ppa, eqy)
            return eqy

        try:
            with ctx.busy():
                result = await ctx.tools.measure(ref, pack)
            ppa = result.to_ppa()
        except (asyncio.CancelledError, RunStopped):
            raise
        except Exception as exc:
            log.warning("run %s: reference measure failed: %s", ctx.run_id, exc)

        if not pack.eqy.enabled:
            eqy = not_run_equivalence(winner_id, "Equivalence checking is turned off for this task.")
        elif ctx.ledger.exhausted() == "max_wall_s":
            eqy = not_run_equivalence(winner_id, "Not run: the time limit was reached.")
        elif winner.eqy_timed_out:
            eqy = not_run_equivalence(winner_id, "Not run: equivalence timed out on this design size.")
        else:
            try:
                eqy = (await evolve.run_eqy(ctx, ref, winner.rtl_obj)).to_equivalence(winner_id)
            except (asyncio.CancelledError, RunStopped):
                raise
            except Exception as exc:
                log.warning("run %s: reference EQY failed: %s", ctx.run_id, exc)
        await em.human_ref(ppa, eqy)
        return eqy
    except (asyncio.CancelledError, RunStopped):
        raise
    except Exception:
        log.exception("run %s: human reference stage failed", ctx.run_id)
        return None
