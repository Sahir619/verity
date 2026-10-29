"""Probe the Nebius Token Factory models that the chat roles may use (router, parser, chat, research).

    cd backend && .venv/Scripts/python scripts/probe_models.py [--max-tokens 800] [--cap-usd 0.02]

Uses the app's own settings loader (config.toml, backend/.env, env) for the base URL and key. The key is never printed.
Steps:
  1. GET /models (free): are zai-org/GLM-5.3, zai-org/GLM-5.3-Flash and nvidia/Nemotron-3_5-Lightning listed?
  2. GLM 5.3, one short JSON call: response_format json_object, reasoning_effort "low".
  3. GLM 5.3 with chat_template_kwargs enable_thinking false: accepted, rejected or ignored?
  4. Nemotron 3.5 Lightning, one short JSON call with thinking off.
Every call has a hard max_tokens, and the script stops before a call whose worst case would pass --cap-usd.
Prices come from [prices] in config.toml (USD per 1M tokens).
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from verity.config import load_settings  # noqa: E402

GLM = "zai-org/GLM-5.3"
GLM_FLASH = "zai-org/GLM-5.3-Flash"
LIGHTNING = "nvidia/Nemotron-3_5-Lightning"
WANTED = (GLM, GLM_FLASH, LIGHTNING)

SYSTEM = (
    "You sort one short request into a class. Answer with exactly one JSON object and nothing else: "
    '{"class": "not_chip" | "in_family" | "too_big", "label": "<at most 6 lowercase words>"}'
)
USER = "a 16-bit adder, smallest area"


class Probe:
    def __init__(self, cap_usd: float) -> None:
        self.settings = load_settings()
        secret = self.settings.llm.api_key
        self._key = secret.get_secret_value() if secret is not None else ""
        self.base = self.settings.llm.base_url.rstrip("/")
        self.cap_usd = cap_usd
        self.spent = 0.0
        self.client = httpx.Client(timeout=90.0, headers={"Authorization": f"Bearer {self._key}"})

    def clean(self, text: str) -> str:
        if self._key:
            text = text.replace(self._key, "***")
        return " ".join(text.split())[:400]

    def price(self, model: str) -> tuple[float, float]:
        p = self.settings.price_for(model)
        return p.input_per_m, p.output_per_m

    def models(self) -> dict[str, Any]:
        started = time.monotonic()
        res = self.client.get(f"{self.base}/models")
        out: dict[str, Any] = {"step": "models", "http": res.status_code, "ms": int((time.monotonic() - started) * 1000)}
        if res.status_code != 200:
            out["error"] = self.clean(res.text)
            return out
        ids = sorted(m.get("id", "") for m in res.json().get("data", []))
        out["listed"] = {m: m in ids for m in WANTED}
        out["related"] = [i for i in ids if "glm" in i.lower() or "nemotron" in i.lower()]
        return out

    def chat(self, step: str, model: str, max_tokens: int, extra: dict[str, Any]) -> dict[str, Any]:
        pin, pout = self.price(model)
        worst = (400 * pin + max_tokens * pout) / 1e6
        if self.spent + worst > self.cap_usd:
            return {"step": step, "model": model, "skipped": f"worst case ${worst:.5f} would pass the cap"}
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": USER}],
            "max_tokens": max_tokens,
            "temperature": 0,
            **extra,
        }
        started = time.monotonic()
        try:
            res = self.client.post(f"{self.base}/chat/completions", json=body)
        except httpx.HTTPError as exc:
            return {"step": step, "model": model, "error": type(exc).__name__}
        ms = int((time.monotonic() - started) * 1000)
        out: dict[str, Any] = {"step": step, "model": model, "sent": sorted(extra), "http": res.status_code, "ms": ms}
        if res.status_code != 200:
            out["error"] = self.clean(res.text)
            return out
        data = res.json()
        usage = data.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        prompt_t = int(usage.get("prompt_tokens") or 0)
        completion_t = int(usage.get("completion_tokens") or 0)
        reasoning_t = int(details.get("reasoning_tokens") or 0)
        billed_out = completion_t + (reasoning_t if reasoning_t > completion_t else 0)
        usd = (prompt_t * pin + billed_out * pout) / 1e6
        self.spent += usd
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        text = message.get("content") or ""
        try:
            parsed = json.loads(text.strip())
            json_ok = isinstance(parsed, dict) and "class" in parsed
        except ValueError:
            parsed, json_ok = None, False
        out.update(
            served=data.get("model"),
            finish=choice.get("finish_reason"),
            prompt_tokens=prompt_t,
            completion_tokens=completion_t,
            reasoning_tokens=reasoning_t,
            reasoning_text_chars=len(message.get("reasoning_content") or message.get("reasoning") or ""),
            json_ok=json_ok,
            answer=parsed if json_ok else self.clean(text)[:200],
            usd=round(usd, 6),
        )
        return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-tokens", type=int, default=800, help="hard cap for the GLM JSON call")
    parser.add_argument("--cap-usd", type=float, default=0.02, help="stop before the total could pass this")
    args = parser.parse_args()

    probe = Probe(args.cap_usd)
    print(json.dumps({"base_host": httpx.URL(probe.base).host, "key_set": bool(probe._key)}))
    if not probe._key:
        print(json.dumps({"stop": "no LLM_API_KEY configured"}))
        return
    results = [probe.models()]
    results.append(
        probe.chat(
            "glm_json_low",
            GLM,
            args.max_tokens,
            {"response_format": {"type": "json_object"}, "reasoning_effort": "low"},
        )
    )
    results.append(
        probe.chat(
            "glm_thinking_false",
            GLM,
            min(args.max_tokens, 400),
            {"chat_template_kwargs": {"enable_thinking": False}},
        )
    )
    results.append(
        probe.chat(
            "lightning_json_no_thinking",
            LIGHTNING,
            200,
            {"response_format": {"type": "json_object"}, "chat_template_kwargs": {"enable_thinking": False}},
        )
    )
    for row in results:
        print(json.dumps(row))
    print(json.dumps({"total_usd": round(probe.spent, 6), "cap_usd": args.cap_usd}))
    probe.client.close()


if __name__ == "__main__":
    main()
