"""Run one subprocess from a fixed argument list (never a shell) with a hard timeout and bounded output.

Output is bounded by keeping the TAIL of each stream: the sandbox result is the last stdout line, so the tail is
the part that matters when a tool floods the pipe.

On Windows, asyncio subprocesses need the Proactor event loop (the default since Python 3.8). If the running
loop does not support subprocesses (NotImplementedError, for example a Selector loop forced by a server), the
call falls back to a worker thread with subprocess.Popen, so the runner works either way.
"""

import asyncio
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProcessResult:
    exit_code: int | None  # None when the process was killed on timeout
    stdout: bytes
    stderr: bytes
    timed_out: bool


async def _read_tail(stream: asyncio.StreamReader | None, cap: int) -> bytes:
    if stream is None:
        return b""
    buf = bytearray()
    while True:
        chunk = await stream.read(65_536)
        if not chunk:
            break
        buf += chunk
        if len(buf) > 2 * cap:
            del buf[: len(buf) - cap]
    return bytes(buf[-cap:])


def _run_blocking(cmd: Sequence[str], timeout_s: float, cap: int) -> ProcessResult:
    proc = subprocess.Popen(  # noqa: S603 - fixed argument list, no shell
        list(cmd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        out, err = proc.communicate(timeout=timeout_s)
        return ProcessResult(proc.returncode, out[-cap:], err[-cap:], False)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        return ProcessResult(None, out[-cap:], err[-cap:], True)


async def run_process(cmd: Sequence[str], *, timeout_s: float, cap: int) -> ProcessResult:
    """Raises FileNotFoundError / PermissionError if the executable cannot be started."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except NotImplementedError:
        return await asyncio.to_thread(_run_blocking, cmd, timeout_s, cap)

    readers = [asyncio.ensure_future(_read_tail(proc.stdout, cap)), asyncio.ensure_future(_read_tail(proc.stderr, cap))]
    timed_out = False
    try:
        try:
            await asyncio.wait_for(proc.wait(), timeout_s)
        except asyncio.TimeoutError:
            timed_out = True
            proc.kill()
            await proc.wait()
        out, err = await asyncio.wait_for(asyncio.gather(*readers), 10)
    except BaseException:
        # Cancellation (user pressed Stop) or a stuck pipe: do not leave the process or reader tasks behind.
        if proc.returncode is None:
            proc.kill()
        for reader in readers:
            reader.cancel()
        raise
    return ProcessResult(None if timed_out else proc.returncode, out, err, timed_out)
