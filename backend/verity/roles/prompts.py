"""Prompt templates: roles/prompts/<name>.v1.md.

A template file has two sections introduced by marker lines `<!-- system -->` and `<!-- user -->`, and
`{{name}}` placeholders. Rendering replaces placeholders in ONE pass, so substituted text (RTL, logs, plans) is
never scanned for placeholders again.

Prompt version = "<file name>@<first 12 hex of sha256 of the file, CRLF normalized>". It is recorded per attempt.
"""

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

_SYSTEM_MARK = "<!-- system -->"
_USER_MARK = "<!-- user -->"
_PLACEHOLDER_RE = re.compile(r"\{\{([a-z_]+)\}\}")


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    file: str
    version: str
    system: str
    user: str

    def render(self, **values: str) -> tuple[str, str]:
        """Returns (system, user). Unknown or missing placeholder names raise KeyError (a programming error)."""

        def fill(match: re.Match[str]) -> str:
            return values[match.group(1)]

        return _PLACEHOLDER_RE.sub(fill, self.system).strip() + "\n", _PLACEHOLDER_RE.sub(fill, self.user).strip() + "\n"


@lru_cache(maxsize=None)
def load_template(file: str) -> PromptTemplate:
    path = PROMPTS_DIR / file
    raw = path.read_bytes().replace(b"\r\n", b"\n")
    text = raw.decode("utf-8")
    sys_at = text.find(_SYSTEM_MARK)
    user_at = text.find(_USER_MARK)
    if sys_at < 0 or user_at < 0 or user_at < sys_at:
        raise ValueError(f"{file}: needs a '{_SYSTEM_MARK}' section followed by a '{_USER_MARK}' section")
    system = text[sys_at + len(_SYSTEM_MARK) : user_at]
    user = text[user_at + len(_USER_MARK) :]
    version = f"{file}@{hashlib.sha256(raw).hexdigest()[:12]}"
    return PromptTemplate(file=file, version=version, system=system.strip(), user=user.strip())
