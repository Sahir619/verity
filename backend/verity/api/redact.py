"""Log redaction (ARCHITECTURE 12.4): Authorization headers (Tavily sends its key as a bearer token), bearer tokens,
stream tokens, the Serper X-API-KEY header, the Supabase `apikey` header and any `tvly-` or `sb_secret_` key never reach
a log.

The uvicorn access log prints the full request path, which includes `?st=<stream token>`. `log_config()` returns
a uvicorn logging config whose handlers scrub those values.
"""

import copy
import logging
import re
from typing import Any

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"([?&](?:st|token|access_?code)=)[^&\s\"']+", re.IGNORECASE), r"\1[redacted]"),
    (re.compile(r"(\bBearer\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE), r"\1[redacted]"),
    (re.compile(r"(\bAuthorization['\"]?\s*[:=]\s*)[^\r\n,}]+", re.IGNORECASE), r"\1[redacted]"),
    # Serper's key header (research). Request headers are never logged, but a stray repr must not leak it either.
    (re.compile(r"(\bX-API-KEY['\"]?\s*[:=]\s*['\"]?)[^\r\n,}'\"]+", re.IGNORECASE), r"\1[redacted]"),
    # Tavily keys all start with "tvly-". The Authorization and Bearer rules above catch the header; this catches a
    # key that shows up anywhere else (a repr, an exception message, a pasted URL).
    (re.compile(r"\btvly-[A-Za-z0-9_-]+"), "tvly-[redacted]"),
    # Supabase: the secret key is the `apikey` header of the Data API. The header value, and any sb_secret_ key that shows
    # up elsewhere (a repr, an exception message), are scrubbed too.
    (re.compile(r"(\bapikey['\"]?\s*[:=]\s*['\"]?)[^\r\n,}'\"&\s]+", re.IGNORECASE), r"\1[redacted]"),
    (re.compile(r"\bsb_secret_[A-Za-z0-9_-]+"), "sb_secret_[redacted]"),
)


def redact(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: redact(v) if isinstance(v, str) else v for k, v in record.args.items()}
        return True


def log_config() -> dict[str, Any]:
    # UNVERIFIED: that uvicorn applies the handler-level filter (check `?st=` shows as [redacted] in the access log).
    """uvicorn's default logging config with RedactFilter on every handler."""
    from uvicorn.config import LOGGING_CONFIG

    config = copy.deepcopy(LOGGING_CONFIG)
    config.setdefault("filters", {})["redact"] = {"()": "verity.api.redact.RedactFilter"}
    for handler in config.get("handlers", {}).values():
        handler["filters"] = [*handler.get("filters", []), "redact"]
    return config
