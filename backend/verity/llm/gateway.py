"""RunLLM: the per-run, role-aware wrapper around an LLMProvider (LLMGateway protocol).

Route role -> model, check the prompt size, reserve on the ledger, call, retry retryable failures with
backoff, commit the real usage (or release the reservation on any failure, including cancellation).
Roles never talk to a provider directly.
"""

import asyncio
from collections.abc import Mapping

from ..config import RoleName, Settings
from ..core.budget import Usage, estimate_tokens, price_usd
from ..core.errors import LLMError
from ..core.llm_types import LLMRequest, LLMResponse, ResponseFormat
from ..core.protocols import Ledger, LLMProvider


class RunLLM:
    """One instance per run. Not shared between runs (it carries the per-run call counter)."""

    def __init__(self, provider: LLMProvider, settings: Settings, ledger: Ledger) -> None:
        self.provider = provider
        self.settings = settings
        self.ledger = ledger
        self._call_seq = 0
        # One entry per finished call (R3): what each call used and why it stopped. Written to final/llm-calls.json.
        self.calls: list[dict] = []

    async def call(
        self,
        role: RoleName,
        *,
        system: str,
        user: str,
        response_format: ResponseFormat = "text",
        prompt_version: str = "",
        tags: Mapping[str, str] | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
    ) -> tuple[LLMResponse, float]:
        settings = self.settings
        model = settings.model_for(role)

        estimated_input = estimate_tokens(system) + estimate_tokens(user)
        limit = settings.budget.call.max_prompt_tokens
        if estimated_input > limit:
            raise LLMError(
                f"prompt for {role} is about {estimated_input} tokens, the per-call limit is {limit}",
                retryable=False,
            )

        # Raises BudgetExhausted when the worst case would cross a run limit. Nothing was spent yet.
        reservation = self.ledger.reserve(
            role=role,
            model=model,
            max_input_tokens=estimated_input,
            max_output_tokens=settings.max_output_for(role),
        )

        self._call_seq += 1
        seq = self._call_seq  # captured now: parallel calls finish in any order and must keep their own number
        request_tags = dict(tags or {})
        request_tags.setdefault("prompt_version", prompt_version)
        request_tags["call_seq"] = str(seq)  # lets the fake provider keep parallel children distinct
        request = LLMRequest(
            role=role,
            model=model,
            system=system,
            user=user,
            max_output_tokens=settings.max_output_for(role),
            temperature=settings.temperature_for(role) if temperature is None else temperature,
            top_p=top_p,
            response_format=response_format,
            timeout_s=float(settings.llm_timeout_for(role)),
            tags=request_tags,
            thinking=settings.thinking_for(role),
            reasoning_effort=settings.reasoning_effort_for(role),
        )

        committed = False
        try:
            response = await self._complete_with_retries(request)
            served = response.model or model  # priced on the model that answered, which a fallback chain can change
            usd = price_usd(settings.price_for(served), response.input_tokens, response.output_tokens)
            self.ledger.commit(
                reservation,
                Usage(model=served, input_tokens=response.input_tokens, output_tokens=response.output_tokens, usd=usd),
            )
            committed = True
            self.calls.append(
                {
                    "seq": seq,
                    "role": role,
                    "model": model,
                    "servedModel": served,
                    "temperature": request.temperature,
                    "topP": request.top_p,
                    "inputTokens": response.input_tokens,
                    "outputTokens": response.output_tokens,
                    "reasoningTokens": response.reasoning_tokens,
                    "maxOutputTokens": request.max_output_tokens,
                    "finishReason": response.finish_reason,
                    "latencyMs": response.latency_ms,
                    "usd": round(usd, 6),
                    "tags": {k: v for k, v in request_tags.items() if k in ("attempt", "sample", "strategy", "parse_retry")},
                }
            )
            return response, usd
        finally:
            if not committed:
                self.ledger.release(reservation)

    async def _complete_with_retries(self, request: LLMRequest) -> LLMResponse:
        retries = max(0, self.settings.llm.retries)
        backoff = self.settings.llm.backoff_s
        attempt = 0
        while True:
            try:
                return await self.provider.complete(request)
            except LLMError as exc:
                if not exc.retryable or attempt >= retries:
                    raise
                await asyncio.sleep(backoff * (2**attempt))
                attempt += 1
