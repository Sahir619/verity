"""Task packs, checked without a sandbox: integrity, rules, isolation, and the live packs against presets.ts."""

import re
import shutil
from pathlib import Path

import pytest

from verity.config import AGENT_ROOT
from verity.core.errors import PackFormatError, PackIntegrityError, RtlRejected
from verity.core.pack import MANIFEST_NAME, REFERENCE_FILE, TaskPack, write_manifest
from verity_test_support import WEB_ROOT

TASKS = AGENT_ROOT / "tasks"
PRESETS_TS = WEB_ROOT / "src" / "lib" / "contracts" / "presets.ts"
EXPECTED_IDS = {
    "mac4", "alu8", "matmul4", "add8", "cnt8", "fsm_seq", "mm4_int4",
    "rv32i_imm", "rv32i_dec", "rv32i_alu", "rv32i_br", "rv32i_rf", "rv32i_lsu", "rv32i_ctl", "rv32i_pctl", "rv32i_core", "rv32i_cache", "rv32i_cache_core",
    "rv32i_pipe2_core",
}
LIVE_IDS = {"mac4", "alu8", "matmul4", "add8", "cnt8", "fsm_seq"}
WEB_PRESET_IDS = {"mac4", "alu8", "matmul4"}  # known gap: add8, cnt8 and fsm_seq are live but have no presetId block in presets.ts (bench knob families live in FAMILIES, not PRESETS)


def test_all_expected_packs_load(packs: dict[str, TaskPack]) -> None:
    assert set(packs) == EXPECTED_IDS
    assert {p.id for p in packs.values() if p.live_preset} == LIVE_IDS
    assert all(p.sha256 and len(p.sha256) == 64 for p in packs.values())


@pytest.mark.parametrize("pack_id", sorted(EXPECTED_IDS))
def test_reference_obeys_the_coding_rules(packs: dict[str, TaskPack], pack_id: str) -> None:
    pack = packs[pack_id]
    reference = pack.reference_rtl()
    assert reference is not None
    pack.validate_rtl(reference)  # raises RtlRejected with the problem list


@pytest.mark.parametrize("pack_id", sorted(EXPECTED_IDS))
def test_sandbox_copy_never_contains_the_reference(packs: dict[str, TaskPack], pack_id: str, tmp_path: Path) -> None:
    dest = tmp_path / "pack"
    packs[pack_id].copy_for_sandbox(dest)
    names = {p.name for p in dest.rglob("*") if p.is_file()}
    assert REFERENCE_FILE not in names and MANIFEST_NAME not in names
    assert {"task.toml", "golden.py", "test_dut.py"} <= names


@pytest.mark.parametrize("pack_id", sorted(EXPECTED_IDS))
def test_public_brief_does_not_leak_the_golden_files(packs: dict[str, TaskPack], pack_id: str) -> None:
    pack = packs[pack_id]
    brief = pack.public_brief()
    for name in ("golden.py", "test_dut.py", REFERENCE_FILE):
        for raw in (pack.root / name).read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if len(line) >= 24:
                assert line not in brief, f"{name} line leaked into the brief: {line[:60]}"


def test_tampering_with_a_pack_is_detected(tmp_path: Path) -> None:
    copy = tmp_path / "mac4"
    shutil.copytree(TASKS / "mac4", copy, ignore=shutil.ignore_patterns("__pycache__"))
    pack = TaskPack.load(copy)
    (copy / "golden.py").write_text("# changed\n", encoding="utf-8")
    with pytest.raises(PackIntegrityError):
        pack.reverify()
    with pytest.raises(PackIntegrityError):
        TaskPack.load(copy)
    write_manifest(copy)  # the author-side helper accepts the new content
    TaskPack.load(copy)


def test_unknown_task_toml_keys_are_rejected(tmp_path: Path) -> None:
    copy = tmp_path / "mac4"
    shutil.copytree(TASKS / "mac4", copy, ignore=shutil.ignore_patterns("__pycache__"))
    toml = copy / "task.toml"
    toml.write_text(toml.read_text(encoding="utf-8") + "\nsurprise = 1\n", encoding="utf-8")
    write_manifest(copy)
    with pytest.raises(PackFormatError):
        TaskPack.load(copy)


def test_banned_constructs_are_rejected_before_any_sandbox(mac4: TaskPack) -> None:
    reference = mac4.reference_rtl() or ""
    broken = reference.replace("endmodule", "  initial begin end\nendmodule", 1)
    with pytest.raises(RtlRejected):
        mac4.validate_rtl(broken)


# ---------- live packs must equal frontend/src/lib/contracts/presets.ts (CONTRACTS S1) ----------
def _presets_segment(source: str, pack_id: str) -> str:
    start = source.index(f'presetId: "{pack_id}"')
    nxt = source.find('presetId: "', start + 10)
    return source[start : nxt if nxt > 0 else len(source)]


@pytest.mark.skipif(not PRESETS_TS.is_file(), reason="web presets.ts not found")
@pytest.mark.parametrize("pack_id", sorted(WEB_PRESET_IDS))
def test_live_pack_matches_the_web_preset(packs: dict[str, TaskPack], pack_id: str) -> None:
    pack = packs[pack_id]
    segment = _presets_segment(PRESETS_TS.read_text(encoding="utf-8"), pack_id)
    assert f'title: "{pack.title}"' in segment
    assert pack.spec_text in segment
    assert f"testCount: {pack.test_count}" in segment
    assert f"maxLines: {pack.max_lines}" in segment
    for port in pack.ports:
        count = f", count: {port.count}" if port.count > 1 else ""
        expected = f'{{ name: "{port.name}", dir: "{port.dir}", width: {port.width}{count} }}'
        assert expected in segment, f"port line missing in presets.ts: {expected}"
    # Same number of ports, in the same order.
    assert len(re.findall(r'\{ name: "', segment)) == len(pack.ports)
