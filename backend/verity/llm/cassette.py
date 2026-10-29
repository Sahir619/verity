"""Hash-keyed record/replay wrapper for a real provider (dev and evals; llm.cache_responses = true).

Key = sha256 of (model, system prompt, user prompt, max_output_tokens, temperature, response_format).
A hit replays the stored response (latency 0, request id "cassette:<key12>"); a miss calls the inner provider
and records the answer under <data_dir>/cassettes/<key>.json. Only successful responses are recorded.

This is separate from the tag-keyed cassettes that FakeProvider reads from backend/evals/cassettes/ (those are
hand-curated model answers per task, role, strategy and attempt).
"""

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from ..core.llm_types import LLMRequest, LLMResponse
from ..core.protocols import LLMProvider


def cassette_key(req: LLMRequest) -> str:
    fields: dict[str, Any] = {
        "model": req.model,
        "system": req.system,
        "user": req.user,
        "max_output_tokens": req.max_output_tokens,
        "temperature": req.temperature,
        "response_format": req.response_format,
    }
    if req.top_p is not None:  # only when set, so older cassette keys stay valid
        fields["top_p"] = req.top_p
    payload = json.dumps(
        fields,
        sort_keys=True,
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


class CassetteProvider:
    """Wraps another provider. `name` is the inner provider's name so health and run records stay truthful."""

    def __init__(self, inner: LLMProvider, directory: Path) -> None:
        self._inner = inner
        self._dir = directory
        self.name = inner.name

    async def complete(self, req: LLMRequest) -> LLMResponse:
        key = cassette_key(req)
        path = self._dir / f"{key}.json"
        hit = await asyncio.to_thread(self._read, path)
        if hit is not None:
            return hit
        response = await self._inner.complete(req)
        await asyncio.to_thread(self._write, path, key, req, response)
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()

    # ---------- disk ----------
    @staticmethod
    def _read(path: Path) -> LLMResponse | None:
        if not path.is_file():
            return None
        try:
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            return LLMResponse(
                text=str(data["text"]),
                input_tokens=int(data["input_tokens"]),
                output_tokens=int(data["output_tokens"]),
                model=str(data["model"]),
                request_id=f"cassette:{path.stem[:12]}",
                latency_ms=0,
                reasoning_tokens=int(data.get("reasoning_tokens", 0)),
                finish_reason=str(data.get("finish_reason", "")),
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None  # a damaged cassette is a miss, never a failure

    def _write(self, path: Path, key: str, req: LLMRequest, response: LLMResponse) -> None:
        record = {
            "key": key,
            "role": req.role,
            "model": response.model,
            "text": response.text,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "reasoning_tokens": response.reasoning_tokens,
            "finish_reason": response.finish_reason,
            "recorded_request_id": response.request_id,
        }
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(record, indent=1, ensure_ascii=True), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass  # recording is best effort


__all__ = ["CassetteProvider", "cassette_key"]
