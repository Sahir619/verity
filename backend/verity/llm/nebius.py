"""NebiusProvider: the OpenAI-compatible provider pointed at Nebius Token Factory.

Base URL and key come from Settings (LLM_BASE_URL, LLM_API_KEY). The production host check lives in
verity.llm.factory.build_provider (ARCHITECTURE 21 submission guard).
"""

from .openai_compat import OpenAICompatProvider

__all__ = ["NebiusProvider", "OpenAICompatProvider"]


class NebiusProvider(OpenAICompatProvider):
    name = "nebius"
    require_key = True  # no key: every call fails fast with LLMError, /health reports maintenance
