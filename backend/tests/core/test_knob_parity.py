"""Tests for Knob self-validation, frontend-backend parity, render cache pruning and parse edge cases."""

import json
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from verity.config import AGENT_ROOT
from verity.core.family import Knob, ParamsError, family_module, prune_render_cache
from verity.core.pack import TaskPack
from verity.core.parse import _interpret


def test_knob_validates_default_at_creation() -> None:
    # Valid knob defaults pass
    k = Knob(id="lanes", kind="int", default=4, min=1, max=8)
    assert k.default == 4

    # Out of range int default raises error
    with pytest.raises((ParamsError, ValidationError)):
        Knob(id="lanes", kind="int", default=99, min=1, max=8)

    # Wrong type for bool raises error
    with pytest.raises((ParamsError, ValidationError)):
        Knob(id="en", kind="bool", default="yes")

    # Invalid characters or length for bits raises error
    with pytest.raises((ParamsError, ValidationError)):
        Knob(id="pat", kind="bits", default="1021", min_len=3, max_len=8)

    with pytest.raises((ParamsError, ValidationError)):
        Knob(id="pat", kind="bits", default="10", min_len=3, max_len=8)


def test_knob_parity_with_frontend_presets() -> None:
    frontend_presets = Path(__file__).parents[3] / "frontend" / "src" / "lib" / "contracts" / "presets.ts"
    assert frontend_presets.is_file(), f"presets.ts not found at {frontend_presets}"
    content = frontend_presets.read_text(encoding="utf-8")

    backend_families = {}
    for fid in ("mac4", "add8", "cnt8", "fsm_seq"):
        pack = TaskPack.load(AGENT_ROOT / "tasks" / fid)
        mod = family_module(pack)
        assert mod is not None
        backend_families[fid] = mod.KNOBS

    # Verify each backend family knob exists in frontend FAMILIES table
    for family_id, knobs in backend_families.items():
        assert f'id: "{family_id}"' in content
        for k in knobs:
            knob_id = k["id"]
            assert f'id: "{knob_id}"' in content
            assert f'kind: "{k["kind"]}"' in content
            if "min" in k:
                assert f"min: {k['min']}" in content
            if "max" in k:
                assert f"max: {k['max']}" in content
            if "min_len" in k:
                assert f"minLen: {k['min_len']}" in content
            if "max_len" in k:
                assert f"maxLen: {k['max_len']}" in content


def test_prune_render_cache(tmp_path: Path) -> None:
    # Create 5 cache folders
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    folders = []
    for i in range(5):
        f = cache_dir / f"pack-{i}"
        f.mkdir()
        folders.append(f)
        time.sleep(0.01)

    # Pruning to max_entries=2 removes 3 oldest
    removed = prune_render_cache(cache_dir, max_entries=2)
    assert removed == 3
    remaining = [f.name for f in cache_dir.iterdir() if f.is_dir()]
    assert len(remaining) == 2
    assert "pack-3" in remaining
    assert "pack-4" in remaining


def test_parse_notes_dropped_unsupported_keys() -> None:
    cnt8_pack = TaskPack.load(AGENT_ROOT / "tasks" / "cnt8")
    mod = family_module(cnt8_pack)
    assert mod is not None
    families = {
        "cnt8": [Knob(**k) for k in mod.KNOBS],
    }
    # Model returns in_range for cnt8 with valid width plus invented key "clock"
    raw_response = json.dumps({
        "result": "in_range",
        "family": "cnt8",
        "params": {"width": 12, "clock": 100},
        "note": "Parsed counter.",
    })
    reply = _interpret(raw_response, families).reply  # _interpret returns the ParsedIntent (fact check)
    assert reply.result == "in_range"
    assert reply.family == "cnt8"
    assert reply.params == {"width": 12, "enable": True}
    assert reply.note is not None
    assert "Ignored unsupported settings: clock" in reply.note
