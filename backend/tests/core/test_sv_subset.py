"""R1: restricted SystemVerilog subset, one behavioral module, and the sandbox command flags."""

import re
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path, PurePosixPath

import pytest

from verity.config import AGENT_ROOT
from verity.core.pack import TaskPack
from verity.roles import optimizer, planner, repair, rtl_gen
from verity.roles.prompts import load_template

BIN = AGENT_ROOT / "sandbox" / "bin"


def _bin_text(name: str) -> str:
    return (BIN / name).read_text(encoding="utf-8")


def test_every_header_port_is_logic(packs: dict[str, TaskPack]) -> None:
    for pack in packs.values():
        header = pack.module_header()
        code = [line for line in header.split("\n") if not line.startswith("//")]
        assert not re.search(r"\b(reg|wire)\b", "\n".join(code)), pack.id
        decls = [line for line in code if re.match(r"\s+(input|output)", line)]
        assert len(decls) == len(pack.ports)
        assert all(" logic" in line for line in decls), pack.id


def test_mac4_header_exact(mac4: TaskPack) -> None:
    header = mac4.module_header()
    assert "    input  logic clk," in header
    assert "    output logic [21:0] acc" in header
    assert "output reg" not in header


def test_signed_port_decl_keeps_signed_after_logic() -> None:
    from verity.core.pack import TaskPort

    assert TaskPort(name="x", dir="in", width=4, signed=True).verilog_decl() == "input  logic signed [3:0] x"
    assert TaskPort(name="y", dir="out", width=1).verilog_decl() == "output logic y"


def test_brief_states_the_subset_rules(mac4: TaskPack) -> None:
    brief = mac4.public_brief()
    assert "SystemVerilog" in brief and "Verilog-2005" not in brief
    for needle in ("always_ff", "always_comb", "`logic`", "generate", "exactly one module"):
        assert needle in brief


PROMPT_FILES = [
    "rtl_gen.v2.md", "planner.v2.md", "repair.v2.md", "optimize.v2.md", "repair.v3.md", "optimize.v3.md",
    "rtl_gen.v3.md", "repair.v4.md", "optimize.v4.md",
]


def test_roles_point_at_current_prompts() -> None:
    assert rtl_gen.TEMPLATE_FILE == "rtl_gen.v3.md"  # v3: prevention rules, ASCII hyphens, sized constants
    assert planner.TEMPLATE_FILE == "planner.v2.md"
    assert repair.TEMPLATE_FILE == "repair.v4.md"  # v4: self-check, the passing parent of a failing child
    assert optimizer.TEMPLATE_FILE == "optimize.v4.md"  # v4: keep control registers, what was already tried


@pytest.mark.parametrize("name", PROMPT_FILES)
def test_v2_prompts_drop_verilog_2005_and_reg(name: str) -> None:
    text = load_template(name)
    body = text.system + text.user
    assert "Verilog-2005" not in body
    assert "output reg" not in body
    assert "declared reg" not in body


def test_rtl_gen_v2_rules() -> None:
    system = load_template("rtl_gen.v2.md").system
    assert "helper modules" in system and "You may add small helper modules" not in system
    for needle in ("`logic`", "always_ff", "always_comb", "`generate`", "`*` and `+`", "Never use `wire` or `reg`"):
        assert needle in system


def test_rtl_gen_v3_adds_prevention_rules_and_ascii_hyphens() -> None:
    system = load_template("rtl_gen.v3.md").system
    for needle in (
        "Declare every signal at module level before the first always block",
        "Start every always_comb with a default for each signal it assigns and give every case a default",
        "type every hyphen as '-'",
        "Size every constant to its target width",
        "`*` and `+`",
    ):
        assert needle in system
    assert "Do not print the table" not in system, "no self-check in first drafts yet"


def test_planner_v2_says_blocks_are_sections_of_one_module() -> None:
    system = load_template("planner.v2.md").system
    assert "sections of that ONE module" in system and "never separate modules" in system


def test_v1_prompts_are_kept_unchanged() -> None:
    assert "output reg" in load_template("rtl_gen.v1.md").system


def test_sandbox_commands_use_systemverilog_flags() -> None:
    verify = _bin_text("verify")
    assert '"iverilog", "-g2012"' in verify and "-g2005" not in verify
    assert "read_verilog -sv {dut}" in verify
    assert "read_verilog -sv {dut}" in _bin_text("measure")
    eqy = _bin_text("eqy")
    for name in ("gold", "gate", "miter"):
        assert f"read_verilog -sv -formal {name}.v" in eqy


def test_eqy_config_reads_with_sv() -> None:
    sys.path.insert(0, str(AGENT_ROOT / "sandbox" / "lib"))
    try:
        module = SourceFileLoader("verity_eqy_bin", str(BIN / "eqy")).load_module()
    finally:
        sys.path.pop(0)
    config = module.eqy_config("top", PurePosixPath("/work/dut.v"), PurePosixPath("/work/b.v"), 4, "smtbmc yices")
    assert "read_verilog -sv /work/dut.v" in config and "read_verilog -sv /work/b.v" in config
