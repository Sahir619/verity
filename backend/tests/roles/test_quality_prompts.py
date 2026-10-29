"""Prompt versions optimize.v4, repair.v4 and rtl_gen.v3 (Nemotron quality): the already-tried list, the passing parent in
a child repair, the control-register rule, the self-check and the ASCII rules. No model is called."""

import pytest

from verity.contracts import PPA, TestCase, Verification
from verity.core.llm_types import LLMResponse, TriedVersion
from verity.core.pack import TaskPack
from verity.roles.common import NOTHING_TRIED, tried_block
from verity.roles.optimizer import STRATEGIES, optimize
from verity.roles.prompts import PROMPTS_DIR, load_template
from verity.roles.repair import PARENT_NOTE, repair

CHILD = "REASON: drop a register.\n```verilog\nmodule mac4 (input clk); endmodule\n```"
FIXED = "DIAGNOSIS: q skipped a value.\nREASON: add one.\n```verilog\nmodule mac4 (input clk); endmodule\n```"
FAILING = Verification(
    lint="pass",
    passed=9,
    total=10,
    failures=[TestCase(name="count_up", inputs="en high", expected="q=0x04", got="q=0x05", pass_=False, cycle=4)],
    tool="sim",
    duration_ms=1,
)
SELF_CHECK = "check it in your reasoning edge by edge"


class ScriptedGateway:
    def __init__(self, settings, text: str) -> None:
        self.settings = settings
        self.text = text
        self.calls: list[dict] = []

    async def call(self, role, *, system, user, response_format="text", prompt_version="", tags=None, **kw):
        self.calls.append({"role": role, "system": system, "user": user})
        return LLMResponse(text=self.text, input_tokens=100, output_tokens=50, model="m", request_id="r", latency_ms=5), 0.001


def ppa(area: float, clock: float) -> PPA:
    return PPA(area_um2=area, cell_count=10, clock_period_ns=clock, pdk="Nangate45")


@pytest.mark.parametrize("name", ["optimize.v4.md", "repair.v4.md", "rtl_gen.v3.md", "rtl_gen.v4.md"])
def test_new_templates_are_plain_ascii_with_the_ascii_and_width_rules(name: str) -> None:
    raw = (PROMPTS_DIR / name).read_text(encoding="utf-8")
    assert raw.isascii(), "no em dash, en dash or any other Unicode in a prompt"
    system = load_template(name).system
    assert "type every hyphen as '-'" in system and "never a non-breaking hyphen" in system
    assert "Size every constant to its target width" in system


def test_old_templates_stay_unchanged_for_the_runs_that_recorded_them() -> None:
    for name in ("optimize.v3.md", "repair.v3.md", "rtl_gen.v2.md"):
        assert load_template(name).version.startswith(name + "@")
        assert "Do not print the table" not in load_template(name).system


def test_self_check_is_in_optimizer_and_repair_but_not_in_first_drafts() -> None:
    assert SELF_CHECK in load_template("optimize.v4.md").system
    assert "compare each with the parent" in load_template("optimize.v4.md").system
    assert SELF_CHECK in load_template("repair.v4.md").system
    assert "Do not print the table" in load_template("repair.v4.md").system
    assert SELF_CHECK not in load_template("rtl_gen.v3.md").system


def test_optimizer_keeps_control_registers_and_does_not_hand_build_arithmetic() -> None:
    system = load_template("optimize.v4.md").system
    assert "Keep the parent's control registers" in system and "same names, widths, reset values" in system
    assert "compared with the parent on every cycle after reset" in system
    assert "Yosys and ABC already optimize +, *, == and simple counters" in system
    assert "carry-lookahead, carry-select or Booth" in system
    for text in STRATEGIES.values():
        assert "adder trees" not in text and "build the arithmetic" not in text


async def test_optimize_prompt_lists_what_was_tried(settings, mac4: TaskPack) -> None:
    gateway = ScriptedGateway(settings, CHILD)
    tried = [
        TriedVersion("v2", "SIMPLIFY", "Dropped a redundant register", ppa(98.0, 1.02)),
        TriedVersion("v3", "EXPLORE", "Rebuilt the multiplier by hand", None),
        TriedVersion("v4", "OPTIMIZE", "Same result as v2", ppa(98.0, 1.02)),
    ]
    out = await optimize(gateway, mac4, "module mac4; endmodule", ppa(100.0, 1.0), ppa(100.0, 1.0), "speed", "OPTIMIZE", tried=tried)
    user = gateway.calls[0]["user"]
    assert "## Already tried in this run (do something different from these)" in user
    assert "- v2 (SIMPLIFY): Dropped a redundant register. Result: measured area -2.0% vs v1, clock period +2.0% vs v1." in user
    assert "- v3 (EXPLORE): Rebuilt the multiplier by hand. Result: rejected." in user
    assert "- v4 (OPTIMIZE): Same result as v2. Result: measured area -2.0% vs v1" in user
    assert out.meta.prompt_version.startswith("optimize.v4.md@")


async def test_optimize_prompt_without_siblings_says_nothing_yet(settings, mac4: TaskPack) -> None:
    gateway = ScriptedGateway(settings, CHILD)
    await optimize(gateway, mac4, "module mac4; endmodule", ppa(100.0, 1.0), ppa(100.0, 1.0), "area", "SIMPLIFY")
    assert NOTHING_TRIED in gateway.calls[0]["user"]


def test_tried_block_cleans_the_reason_to_one_ascii_line() -> None:
    item = TriedVersion("v2", "EXPLORE", "Used a\nsmarter \u201cstate\u201d machine " + "x" * 300, None)
    line = tried_block([item], ppa(1.0, 1.0))
    assert "\n" not in line and line.isascii() and len(line) < 260


async def test_explore_with_an_idea_keeps_arithmetic_as_star_and_plus(settings, mac4: TaskPack) -> None:
    gateway = ScriptedGateway(settings, CHILD)
    await optimize(
        gateway, mac4, "module mac4; endmodule", ppa(100.0, 1.0), ppa(100.0, 1.0), "area", "EXPLORE", idea="Booth recoding: fewer partial products."
    )
    user = gateway.calls[0]["user"]
    assert "Booth recoding: fewer partial products. Use the idea for what each register holds and when each operation happens;" in user
    assert "keep writing arithmetic with `*` and `+` as the coding rules say." in user


async def test_a_child_repair_sees_the_passing_parent(settings, mac4: TaskPack) -> None:
    gateway = ScriptedGateway(settings, FIXED)
    out = await repair(
        gateway, mac4, "module mac4; // child\nendmodule", FAILING, attempt_n=2, parent_rtl="module mac4; // the parent\nendmodule\n"
    )
    user = gateway.calls[0]["user"]
    assert "## Parent design (passes every test)\n```verilog\nmodule mac4; // the parent\nendmodule\n```" in user
    assert PARENT_NOTE in user
    assert "The parent passes every test. The bug is in what this version changed. Keep the improvement if you can; otherwise restore the parent's logic for the broken part." in user
    assert user.index("## Evidence") < user.index("## Parent design"), "the evidence comes first, the parent after it"
    assert out.meta.prompt_version.startswith("repair.v4.md@")


async def test_a_repair_of_v1_has_no_parent_section(settings, mac4: TaskPack) -> None:
    gateway = ScriptedGateway(settings, FIXED)
    await repair(gateway, mac4, "module mac4; endmodule", FAILING, attempt_n=2)
    assert "Parent design" not in gateway.calls[0]["user"] and "{{" not in gateway.calls[0]["user"]


# ---------- rtl_gen v4: a CPU core retry is told what the failed core's checks reported ----------
def test_rtl_gen_v4_is_v3_plus_the_earlier_attempt_section() -> None:
    v3, v4 = load_template("rtl_gen.v3.md"), load_template("rtl_gen.v4.md")
    assert (PROMPTS_DIR / "rtl_gen.v4.md").read_text(encoding="utf-8").isascii()
    assert v4.system == v3.system and "{{prior_attempt}}" in v4.user and "{{prior_attempt}}" not in v3.user
    assert "An earlier attempt at this same design failed these checks" in v4.user and "Do not repeat these mistakes:" in v4.user


async def test_generate_rtl_uses_v4_only_with_prior_lines_and_cleans_them(settings, mac4) -> None:  # noqa: ANN001
    from verity.core.llm_types import PlanOut
    from verity.roles import rtl_gen

    plan = PlanOut(summary="s", blocks=[], latency_cycles=mac4.latency_cycles, estimated_lines=10)
    answer = "REASON: r\n\n```verilog\nmodule mac4 (input clk);\nendmodule\n```"
    plain = ScriptedGateway(settings, answer)
    out = await rtl_gen.generate_rtl(plain, mac4, plan, "area")
    assert out.meta.prompt_version.startswith("rtl_gen.v3.md@") and "earlier attempt" not in plain.calls[0]["user"]
    told = ScriptedGateway(settings, answer)
    path = "\\".join(["C:", "work", "rtl", "core.sv"])
    lines = ["tests passed: 3 of 12", f"lint: %Error: {path}:4:2: bad {chr(0x2014)} thing", "tests passed: 3 of 12", ""] + [f"x{n}" for n in range(20)]
    out = await rtl_gen.generate_rtl(told, mac4, plan, "area", prior_attempt=lines)
    user = told.calls[0]["user"]
    assert out.meta.prompt_version.startswith("rtl_gen.v4.md@")
    assert "- tests passed: 3 of 12\n- lint: %Error: core.sv:4:2: bad thing\n- x0" in user and user.isascii()
    assert user.count("- tests passed: 3 of 12") == 1 and "work" not in user
    assert len(rtl_gen.prior_lines(lines)) == rtl_gen.PRIOR_MAX_LINES and rtl_gen.prior_lines([]) == ()
    assert all(len(line) <= rtl_gen.PRIOR_MAX_CHARS for line in rtl_gen.prior_lines(["y" * 999]))
