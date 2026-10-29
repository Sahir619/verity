"""Edge proof for the mac4, add8, cnt8 and fsm_seq knob families: at min, default and max knobs, the rendered pack's reference.v must
pass its own locked testbench (golden + cocotb + Icarus) in the real sandbox image. Skipped when Docker or the image
is missing.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from verity.config import AGENT_ROOT
from verity.core.family import render_pack
from verity.core.pack import TaskPack
from verity.tools.local_docker import LocalDockerRunner
from verity_test_support import make_settings

EDGES = [
    ("mac4", {"lanes": 1, "width": 4, "cycles": 4}),
    ("mac4", {"lanes": 8, "width": 8, "cycles": 32}),
    ("mac4", {"lanes": 6, "width": 6, "cycles": 20}),
    ("add8", {"width": 4}),
    ("add8", {"width": 8}),
    ("add8", {"width": 32}),
    ("cnt8", {"width": 4, "enable": True}),
    ("cnt8", {"width": 8, "enable": True}),
    ("cnt8", {"width": 4, "enable": False}),
    ("cnt8", {"width": 16, "enable": True}),
    ("cnt8", {"width": 16, "enable": False}),
    ("fsm_seq", {"pattern": "1011", "overlap": True}),
    ("fsm_seq", {"pattern": "111", "overlap": True}),
    ("fsm_seq", {"pattern": "111", "overlap": False}),
    ("fsm_seq", {"pattern": "10110011", "overlap": True}),
    ("fsm_seq", {"pattern": "10110011", "overlap": False}),
    ("fsm_seq", {"pattern": "0010", "overlap": False}),
    ("fsm_seq", {"pattern": "100", "overlap": True}),  # no border: overlap cannot matter
]


def _docker_image_ready(image: str) -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "image", "inspect", image], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@pytest.mark.parametrize("pack_id,params", EDGES, ids=[f"{p}-{'-'.join(f'{k}{v}' for k, v in d.items())}" for p, d in EDGES])
async def test_reference_passes_its_testbench_at_the_edges(tmp_path: Path, pack_id: str, params: dict) -> None:
    settings = make_settings(tmp_path / "data")
    if not _docker_image_ready(settings.tools.image):
        pytest.skip("docker or the verity-tools image is not available")
    base = TaskPack.load(AGENT_ROOT / "tasks" / pack_id)
    pack = render_pack(base, params, tmp_path / "packs")
    rtl = pack.validate_rtl(pack.reference_rtl() or "", max_bytes=settings.rtl.max_bytes)
    runner = LocalDockerRunner(settings)
    result = await runner.verify(rtl, pack)
    assert result.passed_all(pack.test_count), f"{pack_id} {params}: status {result.status}, {result.message}, sim {result.sim}"
    assert result.lint is None or result.lint.ok
    again = await runner.verify(rtl, pack)
    assert (again.status, again.sim.total if again.sim else None) == (result.status, result.sim.total if result.sim else None)


@pytest.mark.parametrize("pattern", ["1011", "111", "1010", "10110011"])
async def test_fsm_seq_locked_tests_tell_overlap_from_no_overlap(tmp_path: Path, pattern: str) -> None:
    """For a pattern with a border, the reference of one overlap setting must FAIL the locked tests of the other."""
    settings = make_settings(tmp_path / "data")
    if not _docker_image_ready(settings.tools.image):
        pytest.skip("docker or the verity-tools image is not available")
    base = TaskPack.load(AGENT_ROOT / "tasks" / "fsm_seq")
    runner = LocalDockerRunner(settings)
    packs = {flag: render_pack(base, {"pattern": pattern, "overlap": flag}, tmp_path / "packs") for flag in (True, False)}
    for flag, pack in packs.items():
        other = packs[not flag]
        rtl = pack.validate_rtl(other.reference_rtl() or "", max_bytes=settings.rtl.max_bytes)
        result = await runner.verify(rtl, pack)
        assert not result.passed_all(pack.test_count), f"{pattern}: the overlap={not flag} design passed the overlap={flag} tests"
