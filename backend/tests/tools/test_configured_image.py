"""The image in config.toml must hold every script the runner calls.

The default once stayed on an image without /verity/bin/bench and /verity/bin/rvfi; a probe that set the image by
hand passed, and the first real CPU job lost its benchmark and riscv-formal steps.
"""

import shutil
import subprocess

import pytest

import verity.tools.rvfi  # noqa: F401  registers SCRIPT_PATHS["rvfi"]
from verity.config import load_settings
from verity.tools.base import SCRIPT_PATHS


def test_the_configured_image_has_every_runner_script() -> None:
    tools = load_settings().tools
    if shutil.which(tools.docker_bin) is None:
        pytest.skip("Docker is not installed")
    try:
        probe = subprocess.run([tools.docker_bin, "image", "inspect", tools.image], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("Docker is not available")
    if probe.returncode != 0:
        pytest.skip(f"{tools.image} is not built here")
    listing = subprocess.run(
        [tools.docker_bin, "run", "--rm", "--entrypoint", "ls", tools.image, "/verity/bin"],
        capture_output=True, text=True, timeout=120, check=True,
    )
    present = {f"/verity/bin/{name}" for name in listing.stdout.split()}
    assert set(SCRIPT_PATHS.values()) <= present, f"{tools.image} lacks {sorted(set(SCRIPT_PATHS.values()) - present)}"
