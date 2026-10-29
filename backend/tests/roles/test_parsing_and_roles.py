"""Model answer parsing and the four roles, against a scripted gateway (no model, no network)."""

import json

import pytest

from verity.contracts import PinInput, Verification, TestCase
from verity.core.errors import RoleOutputError
from verity.core.llm_types import LLMResponse
from verity.core.pack import TaskPack
from verity.roles.optimizer import optimize
from verity.roles.parsing import ParseError, extract_json_object, extract_verilog, parse_rtl_answer, strip_think
from verity.roles.planner import plan
from verity.roles.prompts import PROMPTS_DIR, load_template
from verity.roles.repair import repair
from verity.roles.rtl_gen import generate_rtl
from verity.contracts import PPA

MODULE = "module mac4 (input clk, output reg q);\n  always @(posedge clk) q <= 1'b1;\nendmodule"


class ScriptedGateway:
    """Implements LLMGateway: returns queued texts, records every call."""

    def __init__(self, settings, texts: list[str]) -> None:
        self.settings = settings
        self.texts = list(texts)
        self.calls: list[dict] = []

    async def call(self, role, *, system, user, response_format="text", prompt_version="", tags=None):
        self.calls.append({"role": role, "system": system, "user": user, "format": response_format, "tags": dict(tags or {})})
        text = self.texts.pop(0) if len(self.texts) > 1 else self.texts[0]
        response = LLMResponse(text=text, input_tokens=100, output_tokens=50, model="m", request_id=f"req{len(self.calls)}", latency_ms=5)
        return response, 0.001


# ---------- Verilog extraction ----------
@pytest.mark.parametrize("fence", ["```verilog", "```systemverilog", "```v", "```"])
def test_fence_flavours_are_accepted(fence: str) -> None:
    assert extract_verilog(f"Here you go\n{fence}\n{MODULE}\n```\nDone.", "mac4") == MODULE


def test_the_block_that_declares_the_top_module_wins_and_the_last_one_among_equals() -> None:
    helper = "module helper (input a);\nendmodule"
    text = f"```verilog\n{helper}\n```\nfinal:\n```verilog\n{MODULE}\n```\n"
    assert extract_verilog(text, "mac4") == MODULE
    first, second = MODULE, MODULE.replace("q <= 1'b1", "q <= 1'b0")
    assert extract_verilog(f"```verilog\n{first}\n```\n```verilog\n{second}\n```", "mac4") == second


def test_reasoning_blocks_are_stripped() -> None:
    assert strip_think("<think>```verilog\nbad\n```</think>ok") == "ok"
    assert strip_think("hidden chain of thought</think>answer") == "answer"  # opening tag was in the chat template
    assert strip_think("answer<think>cut off reasoning") == "answer"
    text = f"<think>I could write ```verilog\nmodule wrong; endmodule\n``` but no</think>\n```verilog\n{MODULE}\n```"
    assert extract_verilog(text, "mac4") == MODULE


def test_cut_off_and_missing_answers_raise_parse_errors() -> None:
    assert extract_verilog(f"```verilog\n{MODULE}", "mac4") == MODULE  # unterminated fence is tolerated
    assert extract_verilog(f"intro\n{MODULE}\nthanks", "mac4").endswith("endmodule")  # bare source
    with pytest.raises(ParseError):
        extract_verilog("no code at all", "mac4")
    with pytest.raises(ParseError) as err:
        extract_verilog("```verilog\nmodule mac4 (input clk);\n  wire x;\n```", "mac4")
    assert "endmodule" in str(err.value)


def test_non_ascii_punctuation_is_folded_in_the_rtl() -> None:
    dash = chr(0x2014)
    text = f"```verilog\nmodule mac4 (input clk); // fast {dash} small\nendmodule\n```"
    assert extract_verilog(text, "mac4") == "module mac4 (input clk); // fast - small\nendmodule"


# ---------- reason and diagnosis ----------
def test_reason_and_diagnosis_fields() -> None:
    long = "x" * 500
    text = f"DIAGNOSIS: {long}\nREASON: **Shrunk the adder.**\n\n```verilog\n{MODULE}\n```"
    answer = parse_rtl_answer(text, "mac4", want_diagnosis=True)
    assert answer.reason == "Shrunk the adder."
    assert answer.diagnosis is not None and len(answer.diagnosis) == 300
    assert parse_rtl_answer(text, "mac4").diagnosis is None


def test_a_missing_reason_falls_back_to_the_first_sentence() -> None:
    answer = parse_rtl_answer(f"I removed a register. It was unused.\n```verilog\n{MODULE}\n```", "mac4")
    assert answer.reason == "I removed a register."
    assert parse_rtl_answer(f"```verilog\n{MODULE}\n```", "mac4").reason is None


# ---------- JSON ----------
def test_json_extraction() -> None:
    assert extract_json_object('prefix {"a": 1} suffix') == {"a": 1}
    assert extract_json_object('```json\n{"a": {"b": 2}}\n```') == {"a": {"b": 2}}
    assert extract_json_object('<think>{"no": 1}</think>{"yes": 2}') == {"yes": 2}
    with pytest.raises(ParseError):
        extract_json_object("no json")
    with pytest.raises(ParseError):
        extract_json_object('{"a": ')


# ---------- prompts ----------
@pytest.mark.parametrize("name", ["planner.v1.md", "rtl_gen.v1.md", "repair.v1.md", "optimize.v1.md", "planner.v2.md", "rtl_gen.v2.md", "repair.v2.md", "optimize.v2.md", "repair.v3.md", "optimize.v3.md", "rtl_gen.v3.md", "rtl_gen.v4.md", "repair.v4.md", "optimize.v4.md"])
def test_templates_load_and_render(name: str) -> None:
    template = load_template(name)
    assert template.system and template.user
    assert template.version.startswith(name + "@") and len(template.version.split("@")[1]) == 12
    import re

    names = set(re.findall(r"\{\{([a-z_]+)\}\}", (PROMPTS_DIR / name).read_text(encoding="utf-8")))
    system, user = template.render(**{n: f"<{n}>" for n in names})
    assert "{{" not in system + user
    with pytest.raises(KeyError):
        template.render()  # a missing placeholder is a programming error


def test_substituted_text_is_not_scanned_for_placeholders() -> None:
    template = load_template("repair.v1.md")
    values = {n: "x" for n in ("task_brief", "failure", "top", "latency_cycles")}
    _, user = template.render(**values, current_rtl="// {{failure}} stays literal")
    assert "// {{failure}} stays literal" in user


# ---------- planner ----------
GOOD_PLAN = {
    "summary": "s",
    "blocks": [{"id": "mult", "name": "Multiplier", "reason": "r", "pinOptions": ["Minimize area"], "pins": [], "variants": []}],
    "latencyCycles": 16,
    "estimatedLines": 100,
}


async def test_planner_parses_json_and_retries_once_on_garbage(settings, mac4: TaskPack) -> None:
    gateway = ScriptedGateway(settings, ["I think the plan is good", json.dumps(GOOD_PLAN)])
    result = await plan(gateway, mac4, "area")
    assert result.plan.blocks[0].name == "Multiplier" and result.plan.latency_cycles == 16
    assert len(gateway.calls) == 2 and gateway.calls[0]["format"] == "json"
    assert "could not be used" in gateway.calls[1]["user"]  # the retry tells the model what was wrong
    assert result.meta.input_tokens == 200 and "," in result.meta.request_id  # both calls are accounted for
    assert result.meta.prompt_version.startswith("planner.v2.md@")


async def test_planner_gives_up_after_the_retry(settings, mac4: TaskPack) -> None:
    gateway = ScriptedGateway(settings, ["nope"])
    with pytest.raises(RoleOutputError):
        await plan(gateway, mac4, "area")
    assert len(gateway.calls) == settings.plan.json_retries + 1


async def test_planner_repairs_small_model_mistakes(settings, mac4: TaskPack) -> None:
    sloppy = json.loads(json.dumps(GOOD_PLAN))
    sloppy["risks"] = "just one risk"
    sloppy["interfaces"] = ["a", "b"]
    sloppy["blocks"][0]["variants"] = [{"id": "p", "options": ["none", "2-stage"], "value": "three-stage"}]
    wrapped = {"plan": sloppy}
    result = await plan(ScriptedGateway(settings, ["```json\n" + json.dumps(wrapped) + "\n```"]), mac4, "area")
    assert result.plan.risks == ["just one risk"] and result.plan.interfaces == "a; b"
    variant = result.plan.blocks[0].variants[0]
    assert variant.value == "none" and variant.label == "p"  # invalid value falls back to the first option


async def test_replan_prompt_carries_every_steer_and_the_previous_plan(settings, mac4: TaskPack) -> None:
    first = await plan(ScriptedGateway(settings, [json.dumps(GOOD_PLAN)]), mac4, "area")
    gateway = ScriptedGateway(settings, [json.dumps(GOOD_PLAN)])
    steering = [PinInput(t_ms=1, block_id="mult", pin="Minimize area")]
    await plan(gateway, mac4, "area", previous=first.plan, steering=steering)
    user = gateway.calls[0]["user"]
    assert 'Pin "Minimize area" on block mult' in user and "Previous plan" in user
    assert json.loads(gateway.calls[0]["tags"]["steering"])[0]["kind"] == "pin"
    assert gateway.calls[0]["tags"]["attempt"] == "2"


# ---------- rtl roles ----------
async def test_rtl_gen_returns_unvalidated_text_with_reason(settings, mac4: TaskPack) -> None:
    answer = f"REASON: One multiplier per lane.\n\n```verilog\n{MODULE}\n```"
    gateway = ScriptedGateway(settings, [answer])
    first = await plan(ScriptedGateway(settings, [json.dumps(GOOD_PLAN)]), mac4, "area")
    out = await generate_rtl(gateway, mac4, first.plan, "area")
    assert out.text == MODULE and out.reason == "One multiplier per lane." and out.diagnosis is None
    assert gateway.calls[0]["role"] == "rtl_gen" and gateway.calls[0]["tags"]["task_id"] == "mac4"
    assert "module mac4" in gateway.calls[0]["user"]  # the exact header is in the prompt


async def test_rtl_gen_without_a_code_block_is_a_role_error(settings, mac4: TaskPack) -> None:
    first = await plan(ScriptedGateway(settings, [json.dumps(GOOD_PLAN)]), mac4, "area")
    with pytest.raises(RoleOutputError):
        await generate_rtl(ScriptedGateway(settings, ["Sorry, I cannot do that."]), mac4, first.plan, "area")


async def test_repair_prompt_has_the_failures_and_never_the_locked_files(settings, mac4: TaskPack) -> None:
    cases = [TestCase(name=f"case_{i}", inputs=f"in{i}", expected=f"0x{i}", got="0x0", pass_=False, cycle=16) for i in range(8)]
    verification = Verification(lint="pass", passed=8, total=16, failures=cases, tool="sim", duration_ms=1)
    answer = f"DIAGNOSIS: acc is too narrow.\nREASON: widened it.\n```verilog\n{MODULE}\n```"
    gateway = ScriptedGateway(settings, [answer])
    out = await repair(gateway, mac4, MODULE, verification, attempt_n=2)
    assert out.diagnosis == "acc is too narrow." and out.reason == "widened it."
    user = gateway.calls[0]["user"] + gateway.calls[0]["system"]
    assert "case_0" in user and "case_4" in user and "case_5" not in user  # at most 5 failing cases
    for name in ("golden.py", "test_dut.py", "reference.v"):
        for raw in (mac4.root / name).read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if len(line) >= 30:
                assert line not in user, f"{name} leaked into the repair prompt: {line[:50]}"
    assert gateway.calls[0]["tags"]["attempt"] == "2"


async def test_repair_prompt_for_a_lint_failure_carries_the_log(settings, mac4: TaskPack) -> None:
    verification = Verification(lint="fail", lint_log="%Error: syntax error near 'endmodul'", passed=0, total=16, tool="Verilator", duration_ms=1)
    gateway = ScriptedGateway(settings, [f"REASON: fixed\n```verilog\n{MODULE}\n```"])
    out = await repair(gateway, mac4, MODULE, verification, attempt_n=2)
    assert "syntax error near 'endmodul'" in gateway.calls[0]["user"]
    assert out.diagnosis  # a default is supplied when the model gave none


async def test_optimizer_strategies(settings, mac4: TaskPack) -> None:
    ppa = PPA(area_um2=1000.0, clock_period_ns=3.0, pdk="Nangate45", cell_count=100)
    parent = PPA(area_um2=900.0, clock_period_ns=3.3, pdk="Nangate45", cell_count=90)
    for strategy in ("SIMPLIFY", "OPTIMIZE", "EXPLORE"):
        gateway = ScriptedGateway(settings, [f"REASON: tried {strategy}\n```verilog\n{MODULE}\n```"])
        out = await optimize(gateway, mac4, MODULE, parent, ppa, "area", strategy)
        assert out.reason == f"tried {strategy}" and gateway.calls[0]["role"] == "optimizer"
        assert strategy in gateway.calls[0]["user"] and "area -10.0% vs v1" in gateway.calls[0]["user"]
        assert gateway.calls[0]["tags"]["strategy"] == strategy
    with pytest.raises(ValueError):
        await optimize(ScriptedGateway(settings, ["x"]), mac4, MODULE, parent, ppa, "area", "REWRITE")
