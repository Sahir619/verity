"""Low-level parsing of sandbox stdout (CONTRACTS 3.4, S4): the result is one JSON object on the LAST line.

Everything here is defensive: stdout may be truncated at the output cap, may carry tool chatter before the
result line, or may be empty when the script crashed. A parse failure is an infrastructure problem (the runner
retries once), never a test failure.
"""

import json
from typing import Any


class SandboxOutputError(ValueError):
    """stdout did not contain a usable result object."""


def parse_stdout_json(stdout: str) -> dict[str, Any]:
    """Return the last JSON object printed on stdout.

    The contract says the object is the last non-empty line. To survive stray trailing output (a tool that
    writes after the script's print) the search walks backwards over lines that start with `{`, and returns the
    first one that parses to a dict. A line cut off by output truncation fails to parse and is skipped, so a
    truncated result can never masquerade as an earlier, shorter object: only the final object is accepted
    when it is the last non-empty line, or an earlier object when what follows it is not JSON-like.
    """
    if not stdout or not stdout.strip():
        raise SandboxOutputError("sandbox printed nothing on stdout")
    lines = [line.strip() for line in stdout.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    lines = [line for line in lines if line]
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index]
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            # The final line looks like JSON but is broken (truncated output): fail hard rather than fall back
            # to an older object from earlier in the stream.
            if index == len(lines) - 1:
                raise SandboxOutputError("the last stdout line is not valid JSON (output truncated or malformed)") from None
            continue
        if isinstance(value, dict):
            return value
    raise SandboxOutputError("no JSON object found on stdout")
