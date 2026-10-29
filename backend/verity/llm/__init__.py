"""LLM providers (nebius, openai_compat, fake) and the per-run gateway (builder B).

Import from the submodules directly (verity.llm.factory, verity.llm.gateway, ...). Nothing is imported here so
that the fake provider path never needs the `openai` package.
"""
