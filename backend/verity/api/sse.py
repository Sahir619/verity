"""Hand-rolled SSE framing (CONTRACTS S12, ARCHITECTURE 9.2).

Wire format: `retry: 3000` (250 for the replay of a finished run), then per event `id: <seq>` and `data: <RunEvent JSON>`, and a `: ping` comment every
`sse.ping_s` seconds so proxies keep the connection open. EventSource ignores comment lines.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import suppress

from ..contracts import EventBase, event_to_json, is_terminal

log = logging.getLogger("verity.api")

RETRY_MS = 3000  # how long the browser waits before it reconnects
FINISHED_RETRY_MS = 250  # a finished run replays and ends: the reconnect that is answered 204 should come at once
PING_FRAME = b": ping\n\n"
TERMINAL_GRACE_S = 5.0  # how long to wait for the stream to end after the terminal run event

# Cleanup tasks that must outlive a cancelled request handler. Held here so they are not garbage collected.
_cleanup_tasks: set[asyncio.Task[None]] = set()


def event_frame(event: EventBase) -> bytes:
    # event_to_json is compact single-line JSON, so one data: line is always enough.
    return f"id: {event.seq}\ndata: {event_to_json(event)}\n\n".encode()


async def close_stream(agen: AsyncIterator[EventBase], pending: "asyncio.Future[EventBase] | None" = None) -> None:
    """Stop a service.stream() iterator: wait out a cancelled __anext__, then aclose it."""
    if pending is not None:
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
    aclose = getattr(agen, "aclose", None)
    if aclose is not None:
        try:
            await aclose()
        except Exception:
            log.debug("closing the event stream raised", exc_info=True)


def _schedule_close(agen: AsyncIterator[EventBase], pending: "asyncio.Future[EventBase] | None") -> None:
    # UNVERIFIED: that Starlette cancels the response generator on client disconnect and that service.stream's
    # subscriber is released by this aclose (check the subscriber count after closing a browser tab).
    """Run close_stream in its own task. The request task may already be cancelled (client disconnect), and
    awaiting inside a cancelled scope would abort the cleanup half way."""
    if pending is not None and not pending.done():
        pending.cancel()
    with suppress(RuntimeError):
        task = asyncio.get_running_loop().create_task(close_stream(agen, pending))
        _cleanup_tasks.add(task)
        task.add_done_callback(_cleanup_tasks.discard)


async def sse_body(
    agen: AsyncIterator[EventBase], ping_s: float, first: EventBase | None = None, retry_ms: int = RETRY_MS
) -> AsyncIterator[bytes]:
    """Yield SSE frames until the service stream ends.

    The stream does NOT stop at the terminal `run` event: golden.json (and every live run) ends with a `done` caption
    AFTER `run done`, and the RunManager closes its stream right after the last event. After the terminal event the
    reader waits at most TERMINAL_GRACE_S for the next event or the end of the stream, so a stuck producer can never
    keep the connection open.

    `first` is an event already taken from `agen` (the route peeks one event to decide between 200 and 204).
    """
    pending: asyncio.Future[EventBase] | None = None
    terminal_seen = False
    try:
        yield f"retry: {retry_ms}\n\n".encode()
        if first is not None:
            yield event_frame(first)
            terminal_seen = is_terminal(first)
        while True:
            pending = asyncio.ensure_future(agen.__anext__())
            while True:
                done, _ = await asyncio.wait({pending}, timeout=TERMINAL_GRACE_S if terminal_seen else ping_s)
                if done:
                    break
                if terminal_seen:
                    return  # the run is over and the service did not close the stream in time
                yield PING_FRAME
            try:
                event = pending.result()
            except StopAsyncIteration:
                pending = None
                return
            pending = None
            yield event_frame(event)
            if is_terminal(event):
                terminal_seen = True
    finally:
        _schedule_close(agen, pending)
