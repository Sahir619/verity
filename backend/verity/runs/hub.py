"""EventHub: the EventSink implementation (CONTRACTS section 6, ARCHITECTURE 4 step 9 and 9.2).

One hub serves every run. For each emitted event it
  1. stamps `seq` (gap-free per run, from 1) and `tMs` (ms since run start, non-decreasing),
  2. writes the event to the Store FIRST,
  3. then fans it out to the live subscribers' queues.
Reconnecting clients therefore never lose an event: they replay from the store and then follow the queue.

On a CreditsEvent it also persists runs.spent_json and adds the spend delta to the daily total.
After the first terminal `run` event (done | error | cap_reached | stopped) any further terminal event is dropped,
so a run can never end twice. Non-terminal events after the terminal one are still accepted (golden.json ends with
a `done` caption after `run done`).
"""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..config import Settings
from ..contracts import CreditsEvent, EventBase, is_terminal
from ..core.types import Ledger, RunContext, RunPatch, Store

log = logging.getLogger("verity.runs.hub")

HEARTBEAT_MIN_INTERVAL_S = 1.0


@dataclass(slots=True)
class _Stream:
    run_id: str
    started_monotonic: float
    seq: int = 0
    last_t_ms: int = 0
    terminal_seen: bool = False
    ledger: Ledger | None = None
    ctx: RunContext | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    last_credit_usd: float = 0.0
    last_heartbeat: float = 0.0


class EventHub:
    """Implements core.protocols.EventSink and owns the per-run live subscriber queues."""

    def __init__(self, store: Store, settings: Settings, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._store = store
        self._settings = settings
        self._clock = clock
        self._streams: dict[str, _Stream] = {}

    # ---------- lifecycle ----------
    def open_run(
        self,
        run_id: str,
        *,
        started_monotonic: float,
        ledger: Ledger | None = None,
        ctx: RunContext | None = None,
        last_seq: int = 0,
        last_t_ms: int = 0,
        terminal_seen: bool = False,
    ) -> None:
        """Start (or resume, for startup recovery) the stream of one run."""
        self._streams[run_id] = _Stream(
            run_id=run_id,
            started_monotonic=started_monotonic,
            seq=last_seq,
            last_t_ms=last_t_ms,
            terminal_seen=terminal_seen,
            ledger=ledger,
            ctx=ctx,
        )

    def close_run(self, run_id: str) -> None:
        """End the live stream: wake every subscriber with a sentinel (None) and forget the run."""
        stream = self._streams.pop(run_id, None)
        if stream is None:
            return
        for queue in stream.subscribers:
            queue.put_nowait(None)
        stream.subscribers.clear()

    def is_live(self, run_id: str) -> bool:
        return run_id in self._streams

    def has_terminal(self, run_id: str) -> bool:
        stream = self._streams.get(run_id)
        return bool(stream and stream.terminal_seen)

    # ---------- subscribers ----------
    def subscribe(self, run_id: str) -> asyncio.Queue | None:
        """A queue that receives every event emitted from now on, then None when the stream closes.
        Returns None when the run is not live (everything is already in the store)."""
        stream = self._streams.get(run_id)
        if stream is None:
            return None
        queue: asyncio.Queue = asyncio.Queue()
        stream.subscribers.append(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue) -> None:
        stream = self._streams.get(run_id)
        if stream is not None and queue in stream.subscribers:
            stream.subscribers.remove(queue)

    # ---------- EventSink ----------
    async def emit(self, run_id: str, event: EventBase) -> EventBase:
        stream = self._streams.get(run_id)
        if stream is None:
            raise RuntimeError(f"event for run {run_id} which has no open stream")
        terminal = is_terminal(event)
        async with stream.lock:
            if terminal and stream.terminal_seen:
                log.warning("run %s: dropped a second terminal event", run_id)
                return event
            seq = stream.seq + 1
            t_ms = max(stream.last_t_ms, int((self._clock() - stream.started_monotonic) * 1000))
            stamped = event.model_copy(update={"seq": seq, "t_ms": t_ms})
            # Consume the seq before the write so a cancellation in the middle of the write can never
            # make the next event reuse it. A failed write (not a cancellation) gives the seq back.
            # UNVERIFIED: behavior of shield + cancel against the real SqliteStore. Check that cancelling a run in
            # the middle of an emit never leaves a seq gap or a duplicate (events table primary key is (run_id, seq)).
            stream.seq = seq
            stream.last_t_ms = t_ms
            try:
                await asyncio.shield(self._store.append_event(run_id, stamped))
            except asyncio.CancelledError:
                raise
            except Exception:
                stream.seq = seq - 1
                raise
            if terminal:
                stream.terminal_seen = True
            for queue in stream.subscribers:
                queue.put_nowait(stamped)

        if stream.ctx is not None:
            stream.ctx.beat()
        await self._touch_heartbeat(stream)
        if isinstance(stamped, CreditsEvent):
            await self._on_credits(stream, stamped)
        return stamped

    # ---------- side effects ----------
    async def _touch_heartbeat(self, stream: _Stream) -> None:
        now = self._clock()
        if now - stream.last_heartbeat < HEARTBEAT_MIN_INTERVAL_S:
            return
        stream.last_heartbeat = now
        try:
            await self._store.touch_heartbeat(stream.run_id, datetime.now(UTC))
        except Exception:
            log.exception("run %s: heartbeat write failed", stream.run_id)

    async def _on_credits(self, stream: _Stream, event: CreditsEvent) -> None:
        try:
            if stream.ledger is not None:
                await self._store.update_run(stream.run_id, RunPatch(spent=stream.ledger.snapshot()))
            delta = event.used_usd - stream.last_credit_usd
            stream.last_credit_usd = max(stream.last_credit_usd, event.used_usd)
            # The fake provider spends nothing: its estimated tokens must not eat the daily budget.
            if delta > 0 and self._settings.provider != "fake":
                day = datetime.now(UTC).strftime("%Y-%m-%d")
                await self._store.add_daily_spend(day, delta, stream.ctx.session_id if stream.ctx else None)
        except Exception:
            log.exception("run %s: could not persist spend", stream.run_id)
