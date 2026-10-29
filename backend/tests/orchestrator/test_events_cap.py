"""Artifact content cap (CONTRACTS 6.2: 64 KB per event). Written, not yet run."""

import json

from verity.orchestrator.events import TRUNCATION_MARKER, cap_content


def test_small_content_is_unchanged() -> None:
    assert cap_content("module m; endmodule\n", 65_536) == "module m; endmodule\n"


def test_large_content_is_cut_and_marked() -> None:
    text = "line with \"quotes\"\n" * 20_000
    capped = cap_content(text, 65_536)
    assert capped.endswith(TRUNCATION_MARKER)
    assert len(json.dumps(capped).encode("utf-8")) <= 65_536 - 1024 + 2
