"""Book-keeping for one run that is currently executing inside this process."""

import asyncio
from dataclasses import dataclass, field

from ..core.types import RunContext


@dataclass(slots=True)
class LiveRun:
    ctx: RunContext
    task: asyncio.Task | None = None
    ticker: asyncio.Task | None = None
    # Why the run task was cancelled, set BEFORE task.cancel() so the task wrapper knows which terminal event to emit.
    stop_requested: bool = False
    timeout_reason: str | None = None
    shutting_down: bool = False
    finishing: bool = field(default=False)
    resume: bool = False

    @property
    def run_id(self) -> str:
        return self.ctx.run_id

    @property
    def session_id(self) -> str:
        return self.ctx.session_id

    def cancel_with(self, *, stop: bool = False, timeout_reason: str | None = None, shutdown: bool = False) -> bool:
        """Mark why, then cancel the task. Returns False when the run is already being cancelled or has ended."""
        if self.finishing or self.task is None or self.task.done():
            return False
        self.finishing = True
        self.stop_requested = stop
        self.timeout_reason = timeout_reason
        self.shutting_down = shutdown
        if stop:
            self.ctx.stop.set()
        self.task.cancel()
        return True
