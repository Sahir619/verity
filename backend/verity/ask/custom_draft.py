"""draft_custom_block's own nested call (CE3, ask/agent_tools.py): GLM drafts a spec, worked examples and a Python
golden model from one sentence; the sandbox self-checks the golden model against its own worked examples, with one
redraft on failure. Never writes anything to disk: that happens only at Approve (verity/runs/custom_blocks.py)."""

from __future__ import annotations

import ast
import json
import logging
import re
from dataclasses import dataclass

from pydantic import ValidationError

from ..config import Settings
from ..contracts import CustomBlockCase, CustomBlockDraft
from ..core.budget import price_usd
from ..core.llm_types import LLMRequest
from ..core.protocols import LLMProvider, ToolRunner
from . import prompt

log = logging.getLogger("verity.ask")

DRAFT_TAG = "custom_draft"
MAX_REDRAFTS = 1
# Defense in depth: golden.py only ever runs inside the sandbox (never on the backend host), but a draft that
# imports or reaches outside pure Python is rejected before it is even sent there.
_DENYLIST = re.compile(
    r"\b(import\s+(?!math\b)|__import__|exec\s*\(|eval\s*\(|open\s*\(|os\.|sys\.|socket|subprocess|requests\.|urllib)",
)


@dataclass
class DraftFailed(Exception):
    reason: str  # the plain sentence the model reads (never raised to the user directly)
    usd: float = 0.0  # cost already spent on failed attempts, so the caller still records it


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
    return text.strip()


def _validate_shape(draft: CustomBlockDraft) -> None:
    """Checks the fixed CE3 limits the pydantic model cannot express alone, and that golden.py is plausibly safe
    (it will only ever run inside the sandbox, never here, but a draft that is obviously unsafe is refused before
    it even gets that far)."""
    if not any(p.dir == "out" for p in draft.ports):
        raise DraftFailed("the draft has no output port.")
    names = [p.name for p in draft.ports]
    if len(names) != len(set(names)):
        raise DraftFailed("the draft has duplicate port names.")
    if draft.clock_name and draft.clock_name not in names:
        raise DraftFailed("the draft's clock name is not one of its ports.")
    if draft.reset_name and draft.reset_name not in names:
        raise DraftFailed("the draft's reset name is not one of its ports.")
    if draft.latency_cycles > 0 and not draft.clock_name:
        raise DraftFailed("the draft has latency but no clock.")
    try:
        ast.parse(draft.golden_py)
    except SyntaxError as exc:
        raise DraftFailed(f"golden.py does not parse: {exc}") from exc
    if "def golden_step" not in draft.golden_py:
        raise DraftFailed("golden.py does not define golden_step.")
    if _DENYLIST.search(draft.golden_py):
        raise DraftFailed("golden.py reaches outside plain Python (an import, a file or a network call).")


def _cases_payload(cases: list[CustomBlockCase]) -> list[dict]:
    return [
        {
            "name": c.name,
            "steps": [{"inputs": s.inputs, "rst": s.rst, "expected": s.expected} for s in c.steps],
        }
        for c in cases
    ]


async def _call(provider: LLMProvider, settings: Settings, user: str) -> tuple[dict, float]:
    model = settings.model_for("chat")
    request = LLMRequest(
        role="chat",
        model=model,
        system=prompt.CUSTOM_DRAFT_SYSTEM,
        user=user,
        max_output_tokens=settings.max_output_for("chat"),
        temperature=settings.temperature_for("chat"),
        response_format="json",
        timeout_s=float(settings.llm_timeout_for("chat")),
        thinking=settings.thinking_for("chat"),
        reasoning_effort=settings.reasoning_effort_for("chat"),
        tags={"purpose": DRAFT_TAG},
    )
    response = await provider.complete(request)
    served = response.model or model
    usd = price_usd(settings.price_for(served), response.input_tokens, response.output_tokens)
    try:
        data = json.loads(_strip_fences(response.text))
    except ValueError as exc:
        raise DraftFailed(f"the draft was not valid JSON: {exc}", usd=usd) from None
    if not isinstance(data, dict):
        raise DraftFailed("the draft was not a JSON object.", usd=usd)
    return data, usd


async def draft_custom_block(
    provider: LLMProvider,
    tools: ToolRunner,
    settings: Settings,
    description: str,
    known_ids: set[str],
) -> tuple[CustomBlockDraft, float]:
    """Drafts, validates and self-checks one custom block. Raises DraftFailed (never built, nothing written to
    disk) when the model's draft does not parse, breaks a CE3 limit, or fails its own worked examples twice."""
    total_usd = 0.0
    user = f"Request: {description}"
    for attempt in range(MAX_REDRAFTS + 1):
        data, usd = await _call(provider, settings, user)
        total_usd += usd
        try:
            draft = CustomBlockDraft.model_validate(data)
        except ValidationError as exc:
            if attempt < MAX_REDRAFTS:
                user = f"Request: {description}\n\nYour last answer did not fit the required JSON shape: {exc}. Answer again, the same shape, fixed."
                continue
            raise DraftFailed(f"the draft did not fit the required shape: {exc}", usd=total_usd) from None
        try:
            _validate_shape(draft)
        except DraftFailed as exc:
            if attempt < MAX_REDRAFTS:
                user = f"Request: {description}\n\n{exc.reason} Answer again, the same JSON shape, fixed. This is your last attempt."
                continue
            raise DraftFailed(exc.reason, usd=total_usd) from None
        if draft.id in known_ids:
            draft = draft.model_copy(update={"id": f"{draft.id}_2"})
        result = await tools.check_golden(draft.golden_py, _cases_payload(draft.cases))
        if result.passed_all(len(draft.cases)):
            return draft, total_usd
        if attempt < MAX_REDRAFTS:
            failures = [f.model_dump() for f in result.failures]
            user = f"Request: {description}\n\n{prompt.custom_draft_redraft_note(failures)}"
            continue
        raise DraftFailed(
            f"the golden model did not match its own worked examples after a redraft: {result.message or 'see the failing cases'}.",
            usd=total_usd,
        )
    raise DraftFailed("could not draft a working block for that.", usd=total_usd)  # unreachable, satisfies the type checker
