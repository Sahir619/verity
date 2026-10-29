"""Watchdog (ARCHITECTURE 18.3): turns a stalled run into an `error` event instead of "Running" forever.

Every `watchdog.interval_s` (30 s) it looks at each live run and cancels it when
  - nothing has happened for `watchdog.stall_s` (90 s): not busy in a call and no step or event for that long, or
  - the run is past its wall-clock limit plus a grace period. The orchestrator treats `max_wall_s` as a soft
    budget limit (it stops starting new work and finishes up, which can include one EQY run), so the hard
    cutoff is `max_wall_s + eqy timeout + sandbox overhead`, otherwise cap_reached on wall time could never happen.
The run task wrapper then emits `run {phase: "error", message}` and sets the status `timed_out` (backend only).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable

from ..config import Settings
from .live import LiveRun

log = logging.getLogger("verity.runs.watchdog")


class Watchdog:
    def __init__(
        self,
        settings: Settings,
        live_runs: Callable[[], Iterable[LiveRun]],
        on_timeout: Callable[[LiveRun, str], Awaitable[None]],
    ) -> None:
        self._settings = settings
        self._live_runs = live_runs
        self._on_timeout = on_timeout
        self._task: asyncio.Task | None = None

    # ---------- loop ----------
    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="verity-watchdog")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _loop(self) -> None:
        interval = self._settings.watchdog.interval_s
        while True:
            await asyncio.sleep(interval)
            try:
                await self.check_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("watchdog pass failed")

    # ---------- one pass ----------
    def verdict(self, live: LiveRun) -> str | None:
        """The timeout message for this run, or None when it is fine."""
        ctx = live.ctx
        stall_s = self._settings.watchdog.stall_s
        max_wall = ctx.ledger.limits.max_wall_s
        grace = self._settings.timeouts.eqy_s + self._settings.timeouts.sandbox_overhead_s
        if ctx.elapsed_s() > max_wall + grace:
            return f"timed out: exceeded the {max_wall} s time limit"
        if not ctx.is_alive(stall_s):
            return f"timed out: no progress for {stall_s} s"
        return None

    async def check_once(self) -> list[str]:
        """Cancel every stalled run. Returns the ids of the runs it timed out."""
        timed_out: list[str] = []
        for live in list(self._live_runs()):
            if live.finishing:
                continue
            reason = self.verdict(live)
            if reason is None:
                continue
            log.warning("run %s: %s", live.run_id, reason)
            await self._on_timeout(live, reason)
            timed_out.append(live.run_id)
        return timed_out
