"""LocalDockerRunner: one `docker run` per sandbox call (CONTRACTS 3.3, ARCHITECTURE 11.6).

Isolation per call: no network, read-only root, tmpfs /tmp, memory and CPU and pid limits, uid 1000, all
capabilities dropped, no-new-privileges. The pack is mounted read-only from a FRESH copy (reference.v and the
manifest are never copied). dut.v (and b.v) are written as files; the command line is a constant template, no
model text is ever interpolated.

Windows: bind mounts use `--mount` (the `-v C:\\path:/x` form is ambiguous because of the drive colon) and the
temp paths are resolved to their long form.
"""

import asyncio
import contextlib
import logging
import os
import secrets
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from ..config import Settings
from ..core import captions as cap
from ..core.errors import ToolInfraError
from .base import SCRIPT_PATHS, FatalToolError, RawOutput, SandboxCall, SandboxRunner
from .process import ProcessResult, run_process

log = logging.getLogger("verity.tools")

MAX_PARALLEL_CONTAINERS = 4  # each container may use 2 CPUs and 2 GB; evolve rounds run up to 3 children at once
_TMPFS = "/tmp:rw,exec,size=512m"
_KILL_TIMEOUT_S = 15
PROBE_TTL_S = 15.0  # how long one Docker check answers /health and POST /runs
PROBE_TIMEOUT_S = 2.0  # one `docker image inspect`; Docker Desktop answers in about 0.5 s


class LocalDockerRunner(SandboxRunner):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings, max_parallel=MAX_PARALLEL_CONTAINERS)
        self._digest: str | None = None
        self._probe: str | None = None
        self._probe_at: float | None = None
        self._probe_lock = asyncio.Lock()

    # ---------- sandbox check (GET /health, POST /runs) ----------
    async def sandbox_problem(self) -> str | None:
        """DOCKER_DOWN or IMAGE_MISSING (plain sentences from core.captions), or None when Docker answers and the
        configured image is present. One `docker image inspect`, cached for PROBE_TTL_S, limited to PROBE_TIMEOUT_S."""
        async with self._probe_lock:  # concurrent /health calls share one docker call
            now = time.monotonic()
            if self._probe_at is None or now - self._probe_at >= PROBE_TTL_S:
                self._probe = await self._check_sandbox()
                self._probe_at = time.monotonic()
            return self._probe

    async def _check_sandbox(self) -> str | None:
        tools = self._settings.tools
        cmd = [tools.docker_bin, "image", "inspect", "--format", "{{.Id}}", tools.image]
        try:
            # The outer limit also covers a stuck pipe after the kill; cancelling run_process kills the process.
            result = await asyncio.wait_for(
                run_process(cmd, timeout_s=PROBE_TIMEOUT_S, cap=4_096), timeout=PROBE_TIMEOUT_S + 1.0
            )
        except (OSError, asyncio.TimeoutError) as exc:  # docker is not installed, not on PATH, or hung
            log.warning("sandbox check: cannot run docker: %r", exc)
            return cap.DOCKER_DOWN
        if result.timed_out:
            log.warning("sandbox check: docker did not answer within %.0f s", PROBE_TIMEOUT_S)
            return cap.DOCKER_DOWN
        if result.exit_code == 0:
            return None
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        log.warning("sandbox check failed: %s", detail[-300:])
        return cap.IMAGE_MISSING if cap.sandbox_failed(detail) == cap.IMAGE_MISSING else cap.DOCKER_DOWN

    async def _run(self, *args: Any, **kwargs: Any) -> Any:
        try:
            return await super()._run(*args, **kwargs)
        except ToolInfraError:
            # A check from before this failure must not keep answering "ready": the next /health looks at Docker again.
            self._probe_at = None
            raise

    # ---------- image digest ----------
    async def image_digest(self) -> str:
        """Local image id (the sha256 of the image config), cached. "" if docker or the image is unavailable."""
        if self._digest is not None:
            return self._digest
        tools = self._settings.tools
        try:
            result = await run_process(
                [tools.docker_bin, "image", "inspect", "--format", "{{.Id}}", tools.image], timeout_s=20, cap=4_096
            )
        except (OSError, asyncio.TimeoutError):
            return ""
        if result.exit_code != 0:
            return ""
        self._digest = result.stdout.decode("utf-8", errors="replace").strip()
        return self._digest

    # ---------- one call ----------
    async def _execute(self, call: SandboxCall) -> RawOutput:
        tools = self._settings.tools
        name = f"verity-{secrets.token_hex(6)}"
        started = time.monotonic()
        tmp = Path(tempfile.mkdtemp(prefix="verity-")).resolve()
        try:
            work = tmp / "work"
            pack_dir = tmp / "pack"
            work.mkdir()
            for filename, data in call.work_files.items():
                (work / filename).write_bytes(data)
            if os.name != "nt":
                # uid 1000 inside the container must be able to write /work whoever owns the host temp dir.
                work.chmod(0o777)
                for path in work.iterdir():
                    path.chmod(0o666)
            if call.pack is not None:
                await asyncio.to_thread(call.pack.copy_for_sandbox, pack_dir)  # re-verifies the manifest first
            else:
                pack_dir.mkdir()  # check_golden (CE3): no pack exists yet, mount an empty read-only dir

            # UNVERIFIED: Docker Desktop on Windows with bind mounts, --read-only and --user 1000:1000 (CONTRACTS 12.5).
            cmd = self._command(name, work, pack_dir, call)
            try:
                result = await run_process(cmd, timeout_s=call.timeout_s, cap=tools.output_cap_bytes)
            except (FileNotFoundError, PermissionError) as exc:
                raise FatalToolError(
                    f"cannot start docker ({tools.docker_bin!r}): {exc}", script=call.script
                ) from exc
            except (OSError, asyncio.TimeoutError) as exc:
                raise ToolInfraError(f"docker call failed: {exc!r}", script=call.script) from exc  # repr: a bare TimeoutError has no text
            if result.timed_out:
                await asyncio.shield(self._kill(name))  # before the temp dir is removed
        except asyncio.CancelledError:
            await asyncio.shield(self._kill(name))
            raise
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        return self._raw(result, started)

    @staticmethod
    def _raw(result: ProcessResult, started: float) -> RawOutput:
        return RawOutput(
            exit_code=result.exit_code,
            stdout=result.stdout.decode("utf-8", errors="replace"),
            stderr=result.stderr.decode("utf-8", errors="replace"),
            duration_ms=int((time.monotonic() - started) * 1000),
            timed_out=result.timed_out,
        )

    def _command(self, name: str, work: Path, pack_dir: Path, call: SandboxCall) -> list[str]:
        tools = self._settings.tools
        for path in (work, pack_dir):
            if "," in str(path):  # --mount is comma separated
                raise FatalToolError(f"temp path contains a comma, set TMP to another folder: {path}", script=call.script)
        cmd = [
            tools.docker_bin,
            "run",
            "--rm",
            "--name",
            name,
            "--pull",
            "never",
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            _TMPFS,
            "--memory",
            tools.memory,
            "--memory-swap",
            tools.memory,
            "--cpus",
            tools.cpus,
            "--pids-limit",
            str(tools.pids_limit),
            *(["--user", tools.docker_user] if tools.docker_user else []),
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--mount",
            f"type=bind,source={work},target=/work",
            "--mount",
            f"type=bind,source={pack_dir},target=/pack,readonly",
            "-e",
            "HOME=/tmp",
        ]
        for key, value in call.env.items():
            cmd += ["-e", f"{key}={value}"]
        cmd += [tools.image, SCRIPT_PATHS[call.script]]
        return cmd

    async def _kill(self, name: str) -> None:
        """Stop the container after an outer timeout or a cancel. Best effort: --rm removes it once it dies."""
        with contextlib.suppress(Exception):
            await run_process([self._settings.tools.docker_bin, "kill", name], timeout_s=_KILL_TIMEOUT_S, cap=2_048)
