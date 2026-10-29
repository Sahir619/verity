"""One provider class over the OpenAI chat completions API (Nebius Token Factory and any compatible endpoint).

This is the ONLY module that reads the LLM base URL and API key (through Settings). It makes one request per
`complete()` call, except for a model with a fallback chain (llm.fallback): when that model times out, cannot be reached
or answers 5xx, the same request goes to the next model of the chain. It does not retry and does not touch the ledger:
the gateway (verity.llm.gateway) does both.
"""

import asyncio
import dataclasses
import inspect
import logging
import time
from typing import Any

import openai
from openai import AsyncOpenAI

from ..config import FallbackStep, Settings
from ..core.budget import estimate_tokens
from ..core.errors import LLMError
from ..core.llm_types import LLMRequest, LLMResponse

log = logging.getLogger("verity.llm")

_RETRYABLE_STATUS = frozenset({408, 409, 425, 429})
_PLACEHOLDER_KEY = "not-needed"  # local endpoints (Ollama) ignore the key but the SDK insists on one
_FALLBACK_MIN_TIMEOUT_S = 30.0  # a fallback model gets at least this long, whatever the shortened primary timeout was
_FALLBACK_MIN_LEFT_S = 5.0  # with a request deadline: a fallback attempt is skipped when less than this is left of it


def _now() -> float:
    """The clock of the cooldowns (a function so a test can move it without touching the event loop's clock)."""
    return time.monotonic()


class _ModelDown(LLMError):
    """A timeout, a connection error or a 5xx: the model, not the request, is the problem. The only errors that fall back."""


class OpenAICompatProvider:
    """openai.AsyncOpenAI against settings.llm.base_url. Key is optional (local endpoints)."""

    name = "openai_compat"
    require_key = False

    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.llm.base_url
        secret = settings.llm.api_key
        self._api_key: str = secret.get_secret_value() if secret is not None else ""
        self._client: AsyncOpenAI | None = None
        # Model ids whose endpoint refused response_format json_object. Per model: one model's refusal must not turn
        # JSON mode off for the others (GLM and Nemotron share this provider).
        self._json_mode_unsupported: set[str] = set()
        self._thinking_prefixes = tuple(settings.llm.thinking_switch_prefixes)
        # Fallback chains by primary model id, and when each model that failed may be tried again (monotonic seconds).
        self._chains: dict[str, tuple[FallbackStep, ...]] = {
            primary: tuple(entry.chain) for primary, entry in settings.llm.fallback.items() if entry.chain
        }
        self._cooldown_s = settings.llm.fallback_cooldown_s
        self._down_until: dict[str, float] = {}

    def __repr__(self) -> str:  # never print the key
        return f"{type(self).__name__}(base_url={self._base_url!r})"

    # ---------- client ----------
    def _get_client(self) -> AsyncOpenAI:
        """Created lazily so a missing key is a per-call LLMError (health shows maintenance), not a startup crash."""
        if self._client is None:
            key = self._api_key
            if not key:
                if self.require_key:
                    raise LLMError("LLM_API_KEY is not set", retryable=False)
                key = _PLACEHOLDER_KEY
            # max_retries=0: the gateway owns retry and backoff so every attempt is visible to the ledger.
            self._client = AsyncOpenAI(base_url=self._base_url, api_key=key, max_retries=0)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    def takes_thinking_switch(self, model: str) -> bool:
        return any(model.startswith(prefix) for prefix in self._thinking_prefixes)

    def json_mode_supported(self, model: str) -> bool:
        return model not in self._json_mode_unsupported

    # ---------- fallback ----------
    async def complete(self, req: LLMRequest) -> LLMResponse:
        """One request. A model with a fallback chain that is down (timeout, connection error, 5xx; never a 4xx or a bad
        answer) is skipped for llm.fallback_cooldown_s and its requests go to the chain: each model once, in order, a model
        in its own cooldown skipped. `response.model` says which model served it. With every model in cooldown they are
        all tried anyway, so an outage cannot outlast the models' recovery. With `req.deadline_s`, each fallback attempt is
        capped at the time left of it and one with under 5 s left is not made."""
        chain = self._chains.get(req.model)
        if not chain:
            return await self._complete_one(req)
        attempts = [(req.model, req), *((step.model, self._fallback_request(req, step)) for step in chain)]
        started = _now()
        ready = [a for a in attempts if self._ready(a[0], started)]
        failure: LLMError | None = None
        for model, attempt in ready or attempts:
            if attempt is not req and req.deadline_s is not None:
                left = req.deadline_s - (_now() - started)
                if left < _FALLBACK_MIN_LEFT_S:
                    log.info("llm: no fallback attempt on %s, %.1f s left of the request deadline", model, max(left, 0.0))
                    break
                attempt = dataclasses.replace(attempt, timeout_s=min(attempt.timeout_s, left))
            try:
                response = await self._complete_one(attempt)
            except _ModelDown as exc:
                self._mark_down(model, exc)
                failure = exc
                continue
            self._mark_up(model)
            return response
        if failure is None:  # every attempt was skipped for the deadline
            raise LLMError("no time left for a fallback model", retryable=False)
        raise failure

    @staticmethod
    def _fallback_request(req: LLMRequest, step: FallbackStep) -> LLMRequest:
        """The same request for a chain model: its own thinking and reasoning_effort (the primary's are not sent), the
        same output cap, and at least 30 s."""
        return dataclasses.replace(
            req,
            model=step.model,
            thinking=step.thinking,
            reasoning_effort=step.reasoning_effort,
            timeout_s=max(req.timeout_s, _FALLBACK_MIN_TIMEOUT_S),
        )

    def _ready(self, model: str, now: float) -> bool:
        until = self._down_until.get(model)
        if until is None:
            return True
        if now < until:
            return False
        self._mark_up(model)  # the cooldown ran out: no success is needed to try it again
        return True

    def _mark_down(self, model: str, exc: LLMError) -> None:
        now = _now()
        already = self._down_until.get(model, 0.0) > now
        self._down_until[model] = now + self._cooldown_s
        if not already:
            reason = f"HTTP {exc.status}" if exc.status else str(exc)  # never the endpoint's own words
            log.warning("llm: %s is unhealthy (%s); its requests use the fallback for %.0f s", model, reason, self._cooldown_s)

    def _mark_up(self, model: str) -> None:
        if self._down_until.pop(model, None) is not None:
            log.warning("llm: %s is healthy again and is used first", model)

    # ---------- one call ----------
    async def _complete_one(self, req: LLMRequest) -> LLMResponse:
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "model": req.model,
            "messages": [
                {"role": "system", "content": req.system},
                {"role": "user", "content": req.user},
            ],
            # UNVERIFIED: Nebius accepts `max_tokens` for Nemotron (some OpenAI-compatible servers want
            # `max_completion_tokens`), and whether the cap includes reasoning tokens.
            "max_tokens": req.max_output_tokens,
            "temperature": req.temperature,
        }
        if req.top_p is not None:
            kwargs["top_p"] = req.top_p
        extra_body: dict[str, Any] = {}
        if req.thinking is not None and self.takes_thinking_switch(req.model):
            # Measured on Nebius 2026-10-16 (backend/README.md): both Nemotron models honour enable_thinking false.
            # GLM 5.3 accepts the field and ignores it (probe 2026-10-22), so it is not sent there.
            extra_body["chat_template_kwargs"] = {"enable_thinking": req.thinking}
        if req.reasoning_effort is not None:
            # Probe 2026-10-22: GLM 5.3 with reasoning_effort "low" used 17 reasoning tokens instead of 274.
            extra_body["reasoning_effort"] = req.reasoning_effort
        if extra_body:
            kwargs["extra_body"] = extra_body
        use_json = req.response_format == "json" and req.model not in self._json_mode_unsupported
        if use_json:
            # UNVERIFIED: JSON mode (response_format json_object) on Nebius for Nemotron. If the server rejects it
            # with a 400 we remember that and ask again without it; the planner parser tolerates plain text.
            kwargs["response_format"] = {"type": "json_object"}

        started = time.monotonic()
        try:
            try:
                raw = await self._create(client, kwargs, req.timeout_s)
            except openai.BadRequestError as exc:
                lowered = str(exc).lower()
                if not use_json or ("response_format" not in lowered and "json" not in lowered):
                    raise
                self._json_mode_unsupported.add(req.model)
                kwargs.pop("response_format", None)
                raw = await self._create(client, kwargs, req.timeout_s)
        except LLMError:
            raise
        except TimeoutError as exc:
            raise _ModelDown(f"model call timed out after {req.timeout_s:.0f} s", retryable=True) from exc
        except openai.OpenAIError as exc:
            raise self._map_error(exc) from exc
        latency_ms = int((time.monotonic() - started) * 1000)

        completion = raw.parse()
        # UNVERIFIED: LegacyAPIResponse.parse() is synchronous in openai 1.x; newer SDKs may return an awaitable.
        if inspect.isawaitable(completion):
            completion = await completion
        # UNVERIFIED: Nebius sends x-request-id; if not, the completion id is used.
        request_id = raw.headers.get("x-request-id") or getattr(completion, "id", "") or ""
        if not completion.choices:
            raise LLMError("model returned no choices", retryable=True)
        choice = completion.choices[0]
        text = choice.message.content or ""

        input_tokens, output_tokens, reasoning = self._usage(completion, req, text)
        return LLMResponse(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=getattr(completion, "model", "") or req.model,
            request_id=request_id,
            latency_ms=latency_ms,
            reasoning_tokens=reasoning,
            finish_reason=choice.finish_reason or "",
        )

    @staticmethod
    async def _create(client: AsyncOpenAI, kwargs: dict[str, Any], timeout_s: float) -> Any:
        # UNVERIFIED: a long Nemotron reasoning call (up to 120 s) may be cut by an idle proxy timeout on a
        # non-streaming request. If so, switch this call to stream=True and accumulate the deltas.
        return await asyncio.wait_for(
            client.chat.completions.with_raw_response.create(**kwargs, timeout=timeout_s),
            timeout=timeout_s + 10,
        )

    @staticmethod
    def _usage(completion: Any, req: LLMRequest, text: str) -> tuple[int, int, int]:
        usage = getattr(completion, "usage", None)
        if usage is None:
            return estimate_tokens(req.system + req.user), estimate_tokens(text), 0
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        details = getattr(usage, "completion_tokens_details", None)
        reasoning = int(getattr(details, "reasoning_tokens", 0) or 0) if details is not None else 0
        # UNVERIFIED: whether Nemotron on Nebius counts reasoning tokens inside completion_tokens (OpenAI
        # semantics) or reports them on top. If the reasoning count is larger than the whole completion count it
        # cannot be a subset, so add it; otherwise assume it is already included.
        if reasoning > output_tokens:
            output_tokens += reasoning
        return input_tokens, output_tokens, reasoning

    # ---------- errors ----------
    def _map_error(self, exc: openai.OpenAIError) -> LLMError:
        if isinstance(exc, openai.APITimeoutError):
            return _ModelDown("model call timed out", retryable=True)
        if isinstance(exc, openai.APIConnectionError):
            return _ModelDown("could not reach the model endpoint", retryable=True)
        if isinstance(exc, openai.APIStatusError):
            status = exc.status_code
            retryable = status >= 500 or status in _RETRYABLE_STATUS
            if status in (401, 403):
                return LLMError(
                    f"model endpoint refused the credentials (HTTP {status})", retryable=False, status=status
                )
            return (_ModelDown if status >= 500 else LLMError)(
                f"model endpoint error HTTP {status}: {self._clean(str(exc.message))}",
                retryable=retryable,
                status=status,
            )
        return LLMError(f"model call failed: {type(exc).__name__}", retryable=False)

    def _clean(self, message: str) -> str:
        if self._api_key:
            message = message.replace(self._api_key, "***")
        return " ".join(message.split())[:300]
