"""NebiusSandboxRunner: the tool image run as a disposable instance on Nebius Token Factory Sandboxes (ConTree).

Endpoints used, from docs.tokenfactory.nebius.com/api-reference/sandboxes (fetched 2026-10-05). Base URL
`https://api.tokenfactory.nebius.com/sandboxes/v1` unless `tools.nebius_sandbox.base_url` is set. Every request
carries `Authorization: Bearer <api key>` and `Project: <project id>`.

  POST   /images/import    {"registry": {"url": "docker://..."}, "timeout": 300}  -> 201 {"uuid"} (operation)
  POST   /files            body = raw bytes, application/octet-stream             -> 201 {"uuid","sha256","size"}
  POST   /instances        {"command","shell","image","env","files","disposable","timeout","networking"}
                                                                                  -> 201, Location: .../operations/<uuid>
  GET    /operations/<id>  status PENDING|ASSIGNED|EXECUTING|SUCCESS|FAILED|CANCELLED, result.state.exit_code,
                           result.state.timed_out, result.stdout/stderr {"value","encoding","truncated"}
  DELETE /operations/<id>  cancel (202)

Flow per call: upload dut.v (and b.v) plus the pack files (content-addressed, uploads cached by sha256), spawn one
disposable instance of the tool image with those files mapped to /work and /pack and the network disabled, poll
the operation, return stdout and exit code to the shared runner logic. The stdin-tar `/verity/bin/bundle`
fallback from CONTRACTS 3.3 is not needed because instances accept a `files` map.

Not covered by the documentation, so marked UNVERIFIED below: see the comments next to each mark.
"""

import asyncio
import base64
import contextlib
import hashlib
import logging
import os
import re
import time
from typing import Any

import httpx

from ..config import AGENT_ROOT, Settings, _read_dotenv
from ..core.errors import ToolInfraError
from .base import SCRIPT_PATHS, FatalToolError, RawOutput, SandboxCall, SandboxRunner

log = logging.getLogger("verity.tools")

DEFAULT_BASE_URL = "https://api.tokenfactory.nebius.com/sandboxes/v1"
MAX_PARALLEL_INSTANCES = 6  # the beta allows 50 running operations in total
# Project id env var names: ours first, then the names the ConTree CLI documents.
PROJECT_ENV_NAMES = ("SANDBOX_PROJECT_ID", "CONTREE_PROJECT", "NEBIUS_AI_PROJECT")
POLL_GRACE_S = 30  # how long past the instance timeout we keep polling before cancelling
IMPORT_TIMEOUT_S = 300
_TERMINAL = frozenset({"SUCCESS", "FAILED", "CANCELLED"})
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _project_id() -> str:
    """The Project header value. Not a secret, but it is not in Settings: read process env, then backend/.env."""
    dotenv = _read_dotenv(AGENT_ROOT / ".env")
    for name in PROJECT_ENV_NAMES:
        value = os.environ.get(name) or dotenv.get(name)
        if value:
            return value.strip()
    return ""


def _decode_stream(stream: Any) -> str:
    """Operation result stdout/stderr: {"value": str, "encoding": "ascii" | "base64", "truncated": bool}."""
    if not isinstance(stream, dict):
        return ""
    value = stream.get("value") or ""
    if stream.get("encoding") == "base64":
        try:
            return base64.b64decode(value).decode("utf-8", errors="replace")
        except ValueError:
            return ""
    return str(value)


def _cap_tail(text: str, cap_bytes: int) -> str:
    """Keep the tail: the sandbox result is the last stdout line."""
    data = text.encode("utf-8", errors="replace")
    return text if len(data) <= cap_bytes else data[-cap_bytes:].decode("utf-8", errors="replace")


def _operation_id(response: httpx.Response) -> str:
    location = response.headers.get("Location", "")
    if location:
        return location.rstrip("/").rsplit("/", 1)[-1]
    try:
        return str(response.json().get("uuid") or "")
    except ValueError:
        return ""


class NebiusSandboxRunner(SandboxRunner):
    def __init__(self, settings: Settings, *, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(settings, max_parallel=MAX_PARALLEL_INSTANCES)
        cfg = settings.tools.nebius_sandbox
        key = settings.sandbox_api_key()
        self._base_url = (cfg.base_url or DEFAULT_BASE_URL).rstrip("/")
        self._token = key.get_secret_value() if key else ""
        self._project = _project_id()
        self._image_ref = cfg.image_ref.strip()
        self._client = client
        self._image_id: str | None = None
        self._image_lock = asyncio.Lock()
        self._file_ids: dict[str, str] = {}  # sha256 -> uploaded file uuid

    def config_problems(self) -> list[str]:
        problems = []
        if not self._token:
            problems.append("no sandbox API key (SANDBOX_API_KEY or LLM_API_KEY)")
        if not self._project:
            problems.append(f"no sandbox project id ({PROJECT_ENV_NAMES[0]})")
        if not self._image_ref:
            problems.append("tools.nebius_sandbox.image_ref is empty")
        return problems

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ---------- HTTP ----------
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url + "/",
                headers={"Authorization": f"Bearer {self._token}", "Project": self._project},
                timeout=httpx.Timeout(30.0, connect=10.0),
            )
        return self._client

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = await self._http().request(method, path.lstrip("/"), **kwargs)
        except httpx.HTTPError as exc:
            raise ToolInfraError(f"sandbox API {method} {path} failed: {type(exc).__name__}: {exc}") from exc
        if response.status_code in (401, 403):
            raise FatalToolError(
                f"sandbox API rejected the credentials ({response.status_code}): check the API key and project id"
            )
        if response.status_code >= 400:
            raise ToolInfraError(f"sandbox API {method} {path} returned {response.status_code}: {response.text[:300]}")
        return response

    # ---------- image ----------
    async def _resolve_image(self) -> str:
        """The image reference used for instances. A registry reference is imported once per process."""
        if self._image_id:
            return self._image_id
        async with self._image_lock:
            if self._image_id:
                return self._image_id
            ref = self._image_ref
            if not ref:
                raise FatalToolError("tools.nebius_sandbox.image_ref is empty (registry reference of the tool image)")
            if ref.startswith("tag:") or _UUID_RE.match(ref):
                # Already imported (for example with the CLI): "tag:<name>" or the image UUID.
                self._image_id = ref
                return ref
            url = ref if "://" in ref else f"docker://{ref}"
            # UNVERIFIED: private registries need credentials in the import body; none are configured here.
            response = await self._request(
                "POST", "/images/import", json={"registry": {"url": url}, "timeout": IMPORT_TIMEOUT_S}
            )
            op_id = _operation_id(response)
            if not op_id:
                raise ToolInfraError("image import returned no operation id")
            data = await self._poll(op_id, budget_s=IMPORT_TIMEOUT_S + POLL_GRACE_S)
            if data is None or data.get("status") != "SUCCESS":
                detail = (data or {}).get("error") or "did not finish"
                raise ToolInfraError(f"image import failed: {detail}")
            image = data.get("result_image_uuid") or (data.get("result") or {}).get("image")
            if not image:
                raise ToolInfraError("image import finished without an image uuid")
            self._image_id = str(image)
            return self._image_id

    async def image_digest(self) -> str:
        """The sandbox image uuid (content addressed: the import returns the same uuid for an unchanged image)."""
        if self.config_problems():
            return ""
        try:
            return await self._resolve_image()
        except (ToolInfraError, ValueError):
            return ""

    # ---------- files ----------
    async def _upload(self, data: bytes) -> str:
        sha = hashlib.sha256(data).hexdigest()
        known = self._file_ids.get(sha)
        if known:
            return known
        response = await self._request(
            "POST", "/files", content=data, headers={"Content-Type": "application/octet-stream"}
        )
        try:
            uuid = str(response.json()["uuid"])
        except (ValueError, KeyError) as exc:
            raise ToolInfraError("file upload returned no uuid") from exc
        self._file_ids[sha] = uuid
        return uuid

    # ---------- operations ----------
    async def _poll(self, op_id: str, *, budget_s: float) -> dict[str, Any] | None:
        """Poll until the operation is terminal. None (after a cancel) when the budget is spent."""
        deadline = time.monotonic() + budget_s
        try:
            while True:
                response = await self._request("GET", f"/operations/{op_id}")
                data = response.json()
                if not isinstance(data, dict):
                    raise ToolInfraError("operation status was not a JSON object")
                if data.get("status") in _TERMINAL:
                    return data
                if time.monotonic() > deadline:
                    await self._cancel(op_id)
                    return None
                try:
                    delay = float(response.headers.get("Retry-After", "1"))
                except ValueError:
                    delay = 1.0
                await asyncio.sleep(min(2.0, max(0.3, delay)))
        except asyncio.CancelledError:
            await asyncio.shield(self._cancel(op_id))
            raise

    async def _cancel(self, op_id: str) -> None:
        with contextlib.suppress(Exception):
            await self._request("DELETE", f"/operations/{op_id}")

    # ---------- one call ----------
    async def _execute(self, call: SandboxCall) -> RawOutput:
        problems = self.config_problems()
        if problems:
            raise FatalToolError("Nebius sandbox is not configured: " + "; ".join(problems), script=call.script)
        started = time.monotonic()
        try:
            image = await self._resolve_image()

            paths: dict[str, bytes] = {f"/work/{name}": data for name, data in call.work_files.items()}
            if call.pack is not None:
                pack_files = await asyncio.to_thread(call.pack.sandbox_files)  # re-verifies the manifest first
                for rel, data in pack_files.items():
                    paths[f"/pack/{rel.replace(chr(92), '/')}"] = data
            uuids = await asyncio.gather(*(self._upload(data) for data in paths.values()))
            # UNVERIFIED: running as root (uid 0, the default) so /work is writable whatever the image sets up.
            files = {
                path: {"uuid": uuid, "uid": 0, "gid": 0, "mode": "0644"} for path, uuid in zip(paths, uuids, strict=True)
            }
            body: dict[str, Any] = {
                "command": SCRIPT_PATHS[call.script],
                "shell": True,  # inherit the image ENV (PATH, PYTHONPATH, VERITY_LIBERTY) as the Dockerfile set it
                "image": image,
                "env": dict(call.env),
                "files": files,
                "disposable": True,
                "timeout": call.timeout_s,
                "networking": {"enabled": False},
            }
            # UNVERIFIED: the spawn body above follows the API reference; memory and CPU limits are not settable.
            response = await self._request("POST", "/instances", json=body)
            op_id = _operation_id(response)
            if not op_id:
                raise ToolInfraError("instance spawn returned no operation id", script=call.script)

            data = await self._poll(op_id, budget_s=call.timeout_s + POLL_GRACE_S)
        except FatalToolError:
            raise
        except ToolInfraError:
            self._file_ids.clear()  # a stale file uuid is one possible cause; the retry re-uploads
            raise

        duration_ms = int((time.monotonic() - started) * 1000)
        if data is None:  # we cancelled it: it ran past its own timeout
            return RawOutput(None, "", "", duration_ms, timed_out=True)

        result = data.get("result") or {}
        state = result.get("state") or {}
        if state.get("timed_out"):
            return RawOutput(None, "", "", duration_ms, timed_out=True)
        exit_code = state.get("exit_code")
        status = data.get("status")
        if status == "CANCELLED" or (status == "FAILED" and exit_code is None):
            raise ToolInfraError(
                f"sandbox operation {status.lower()}: {data.get('error') or 'no detail'}", script=call.script
            )
        # UNVERIFIED: a non-zero script exit may report status FAILED with result.state.exit_code set; it is
        # handled the same as SUCCESS here (the exit code decides).
        cap = self._settings.tools.output_cap_bytes
        return RawOutput(
            exit_code=int(exit_code) if exit_code is not None else None,
            stdout=_cap_tail(_decode_stream(result.get("stdout")), cap),
            stderr=_cap_tail(_decode_stream(result.get("stderr")), cap),
            duration_ms=duration_ms,
        )
