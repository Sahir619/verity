"""build_provider(settings): pick the LLM provider and enforce the submission guard (ARCHITECTURE 21).

With VERITY_ENV=production the provider must be `nebius` and the base URL host must be the Token Factory host
(config llm.production_host). Anything else refuses to build, so a submitted demo can never silently run on a
fake or non-Nebius model.
"""

from urllib.parse import urlparse

from ..config import Settings
from ..core.errors import VerityError
from ..core.protocols import LLMProvider
from .cassette import CassetteProvider
from .fake import FakeProvider


class ProviderGuardError(VerityError):
    """The configured provider is not allowed in this environment."""


def check_provider_guard(settings: Settings) -> None:
    """Raises ProviderGuardError. No-op outside production."""
    if settings.env != "production":
        return
    problems: list[str] = []
    if settings.provider != "nebius":
        problems.append(f"provider must be 'nebius' in production, got '{settings.provider}'")
    host = urlparse(settings.llm.base_url).hostname or ""
    if host != settings.llm.production_host:
        problems.append(f"LLM_BASE_URL host must be {settings.llm.production_host}, got '{host}'")
    if problems:
        raise ProviderGuardError("; ".join(problems))


def build_provider(settings: Settings) -> LLMProvider:
    check_provider_guard(settings)

    provider: LLMProvider
    if settings.provider == "fake":
        return FakeProvider(settings)  # fake answers are already deterministic: never wrapped in a cassette
    if settings.provider == "nebius":
        from .nebius import NebiusProvider  # imported here so the fake path does not need `openai`

        provider = NebiusProvider(settings)
    else:
        from .openai_compat import OpenAICompatProvider

        provider = OpenAICompatProvider(settings)

    # Never record or replay in production: judges must see real calls.
    if settings.llm.cache_responses and settings.env != "production":
        provider = CassetteProvider(provider, settings.data_dir / "cassettes")
    return provider
