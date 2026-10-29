"""R2: lint tag parsing, guidance by tag, the deterministic declaration fixer, and R5 failure signatures."""

from verity.contracts import TestCase, Verification
from verity.core.rtl_fixer import (
    APPLY_EVERYWHERE,
    failure_signature,
    fix_declarations,
    fixer_applies,
    guidance_block,
    guidance_for,
    lint_issues,
    structural_tags,
)
from verity.roles.repair import _failure_text

# Realistic Verilator output shapes (research doc section 4, RTLFixer error classes).
LOG_PROCASSWIRE = (
    "%Error-PROCASSWIRE: dut.v:9:9: Procedural assignment to wire, perhaps intended var "
    "(IEEE 1800-2017 6.5): 'out'\n"
    "                    : ... note: In instance 'adder'\n"
    "%Error: Exiting due to 1 error(s)\n"
)
LOG_DUPLICATE = "%Error: dut.v:6:20: Duplicate declaration of signal: 'out'\n%Error: Exiting due to 1 error(s)\n"
LOG_GENERATE = "%Error: dut.v:11:5: syntax error, unexpected endgenerate\n%Error: Exiting due to 1 error(s)\n"
LOG_UNDECLARED = "%Error: dut.v:8:9: Can't find definition of variable: 'sum_q'\n"
LOG_WIDTH = "%Warning-WIDTHTRUNC: dut.v:7:12: Operator ASSIGN expects 8 bits on the Assign RHS, but Assign RHS's ADD generates 9 bits.\n"

WIRE_IN_ALWAYS = """module adder (
    input  logic       clk,
    input  logic [7:0] a,
    output wire  [7:0] out
);
    always_ff @(posedge clk) begin
        out <= a;
    end
endmodule
"""

DUP_OUT = """module adder (
    input  logic       clk,
    input  logic [7:0] a,
    output logic [7:0] out
);
    reg [7:0] out;
    always_ff @(posedge clk) out <= a;
endmodule
"""

STRAY_GENERATE = """module adder (
    input  logic       clk,
    input  logic [7:0] a,
    output logic [7:0] out
);
    generate
    always_ff @(posedge clk) out <= a;
    endgenerate
endmodule
"""

TWO_MODULES = """module helper (input logic x, output logic y);
    always_comb y = x;
endmodule

module adder (
    input  logic       clk,
    input  logic [7:0] a,
    output logic [7:0] out
);
    always_ff @(posedge clk) out <= a;
endmodule
"""


def test_lint_issue_tags_and_signals() -> None:
    assert [(i.tag, i.signal) for i in lint_issues(LOG_PROCASSWIRE)] == [("PROCASSWIRE", "out")]
    assert [(i.tag, i.signal) for i in lint_issues(LOG_DUPLICATE)] == [("DUPLICATE", "out")]
    assert [(i.tag, i.signal) for i in lint_issues(LOG_GENERATE)] == [("GENERATE", "")]
    assert [(i.tag, i.signal) for i in lint_issues(LOG_UNDECLARED)] == [("UNDECLARED", "sum_q")]
    assert [i.tag for i in lint_issues(LOG_WIDTH)] == ["WIDTH"]
    assert lint_issues("%Error: something unrelated") == []
    assert lint_issues(None) == []


def test_multidriven_and_blkandnblk_tags() -> None:
    log = (
        "%Warning-MULTIDRIVEN: dut.v:5:9: Signal has multiple driving blocks with different clocking: 'q'\n"
        "%Warning-BLKANDNBLK: dut.v:9:9: Unsupported: Blocked and non-blocking assignments to same variable: 'q'\n"
    )
    assert [(i.tag, i.signal) for i in lint_issues(log)] == [("MULTIDRIVEN", "q"), ("BLKANDNBLK", "q")]


def test_guidance_is_selected_by_tag_and_capped_at_four() -> None:
    lines = guidance_for(LOG_PROCASSWIRE, WIRE_IN_ALWAYS)
    assert len(lines) == 1 and "logic" in lines[0] and "always_ff" in lines[0]
    assert "Duplicate declaration" in guidance_for(LOG_DUPLICATE, DUP_OUT)[0]
    assert "generate" in guidance_for(LOG_GENERATE, STRAY_GENERATE)[0].lower()
    assert "exactly one module" in guidance_for("%Error: nope", TWO_MODULES)[0]
    many = LOG_PROCASSWIRE + LOG_DUPLICATE + LOG_GENERATE + LOG_UNDECLARED + LOG_WIDTH
    assert len(guidance_for(many, TWO_MODULES)) == 4
    assert guidance_for("%Error: unrelated", WIRE_IN_ALWAYS) == []
    assert guidance_block("%Error: unrelated", WIRE_IN_ALWAYS) == ""
    assert guidance_block(LOG_DUPLICATE, DUP_OUT).endswith(APPLY_EVERYWHERE)


def test_structural_check() -> None:
    assert structural_tags(TWO_MODULES) == ["MULTIMODULE"]
    assert structural_tags(STRAY_GENERATE) == ["GENERATE"]
    assert structural_tags(DUP_OUT) == []
    assert structural_tags("// module a\n// generate\nmodule m; endmodule\n") == []


def test_repair_prompt_evidence_carries_guidance_for_lint_failures_only() -> None:
    lint_fail = Verification(lint="fail", lint_log=LOG_DUPLICATE, passed=0, total=4, failures=[], tool="Verilator", duration_ms=1)
    text = _failure_text(lint_fail, DUP_OUT)
    assert LOG_DUPLICATE.strip() in text and "Known fixes for these errors:" in text and APPLY_EVERYWHERE in text
    sim_fail = Verification(
        lint="pass", passed=1, total=4, tool="sim", duration_ms=1,
        failures=[TestCase(name="t1", inputs="a=1", expected="2", got="3", pass_=False)],
    )
    assert "Known fixes" not in _failure_text(sim_fail, DUP_OUT)


def test_fixer_promotes_wire_driven_in_always_block() -> None:
    result = fix_declarations(WIRE_IN_ALWAYS)
    assert result.changed
    assert "output logic  [7:0] out" in result.text and "wire" not in result.text
    assert "out <= a;" in result.text  # logic untouched


def test_fixer_removes_duplicate_declaration_of_out() -> None:
    result = fix_declarations(DUP_OUT)
    assert result.changed
    assert result.text.count("out") == DUP_OUT.count("out") - 1
    assert "reg [7:0] out" not in result.text and "logic [7:0] out;" not in result.text
    assert "output logic [7:0] out" in result.text


def test_fixer_keeps_a_declaration_with_a_different_width() -> None:
    text = DUP_OUT.replace("reg [7:0] out;", "reg [3:0] out;")
    result = fix_declarations(text)
    assert "logic [3:0] out;" in result.text  # not a safe text-level fix, left for the model


def test_fixer_removes_stray_generate_but_not_genvar_loops() -> None:
    result = fix_declarations(STRAY_GENERATE)
    assert "generate" not in result.text and "always_ff @(posedge clk) out <= a;" in result.text
    looped = STRAY_GENERATE.replace("always_ff", "genvar i; always_ff")
    assert not fix_declarations(looped).changed


def test_fixer_two_modules_is_left_alone_and_guided() -> None:
    assert not fix_declarations(TWO_MODULES).changed
    assert "MULTIMODULE" in structural_tags(TWO_MODULES)


def test_fixer_keeps_continuous_assign_meaning_and_comments() -> None:
    text = "module m (input logic a, output logic y);\n    // a wire here\n    wire [3:0] t = {a, a, a, a};\n    assign y = t[0];\nendmodule\n"
    result = fix_declarations(text)
    assert "logic [3:0] t;\n    assign t = {a, a, a, a};" in result.text
    assert "// a wire here" in result.text
    assert "reg_a" in fix_declarations("module m; logic reg_a; wire w; endmodule\n").text


def test_fixer_is_idempotent_and_noop_on_clean_code() -> None:
    once = fix_declarations(WIRE_IN_ALWAYS + "")
    assert not fix_declarations(once.text).changed
    clean = "module m (input logic a, output logic y);\n    assign y = a;\nendmodule\n"
    result = fix_declarations(clean)
    assert not result.changed and result.text == clean


def test_failure_signatures() -> None:
    assert failure_signature("fail", LOG_PROCASSWIRE, None) == "lint:PROCASSWIRE:out"
    assert failure_signature("fail", LOG_DUPLICATE, None) == "lint:DUPLICATE:out"
    assert failure_signature("fail", "Rejected before simulation:\n- module x is declared more than once", None).startswith("rules:")
    assert failure_signature("fail", "%Error: dut.v:3:1: weird thing 12", None) == "lint:other:weird thing N"
    assert failure_signature("pass", None, "acc_case") == "test:acc_case"
    assert failure_signature("pass", None, None) == "test:unknown"


def test_fixer_only_applies_to_declaration_tags() -> None:
    assert fixer_applies(LOG_PROCASSWIRE) and fixer_applies(LOG_DUPLICATE) and fixer_applies(LOG_GENERATE)
    assert not fixer_applies(LOG_UNDECLARED) and not fixer_applies(LOG_WIDTH)
    assert not fixer_applies("Rejected before simulation:\n- initial blocks are not allowed")


# ---- latch and nesting (matmul4 gate failures, 2026-10-16) ----
LOG_NESTING = (
    "Interface check failed:\n- dut.v:51: ERROR:Invalid nesting of always blocks and/or initializations."
)
LOG_LATCH_CONTRACT = (
    "Interface check failed:\n"
    "- ERROR: Latch inferred for signal `\\matmul4.$unnamed_block$5.row_i' from always_comb process "
    "`\\matmul4.$proc$dut.v:18$12'."
)
LOG_LATCH_VERILATOR = (
    "%Warning-LATCH: dut.v:18:5: Latch inferred for signal 'matmul4.unnamedblk1.b_row_k' "
    "(not all control paths of combinational always assign a value)\n"
)

NESTED_DECL = """module matmul4 (
    input  logic        clk,
    input  logic        rst_n,
    output logic [31:0] acc
);
    logic [4:0] cycle_ctr;
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            cycle_ctr <= 5'd0;
            acc <= 32'd0;
        end else begin
            logic [7:0] tmp;
            int idx = cycle_ctr - 5;
            tmp = idx[7:0];
            acc <= {24'd0, tmp};
            cycle_ctr <= cycle_ctr + 5'd1;
        end
    end
endmodule
"""


def test_latch_and_nesting_issues_parse_from_contract_bullets() -> None:
    assert [(i.tag, i.signal) for i in lint_issues(LOG_NESTING)] == [("NESTING", "")]
    assert [(i.tag, i.signal) for i in lint_issues(LOG_LATCH_CONTRACT)] == [("LATCH", "row_i")]
    assert [(i.tag, i.signal) for i in lint_issues(LOG_LATCH_VERILATOR)] == [("LATCH", "b_row_k")]


def test_latch_and_nesting_guidance_reaches_the_repair_prompt() -> None:
    lines = guidance_for(LOG_LATCH_CONTRACT, "module m(); endmodule")
    assert len(lines) == 1 and "default" in lines[0] and "first line" in lines[0]
    lines = guidance_for(LOG_NESTING, "module m(); endmodule")
    assert len(lines) == 1 and "module level" in lines[0]
    v = Verification(lint="fail", lint_log=LOG_NESTING, passed=0, total=16, failures=[], tool="Yosys interface check", duration_ms=1)
    text = _failure_text(v, NESTED_DECL)
    assert "Invalid nesting" in text and "Known fixes for these errors:" in text and "module level" in text


def test_failure_signature_names_latch_signal() -> None:
    assert failure_signature("fail", LOG_LATCH_CONTRACT, None) == "lint:LATCH:row_i"
    assert failure_signature("fail", LOG_NESTING, None) == "lint:NESTING:"


def test_fixer_hoists_declarations_out_of_an_always_block() -> None:
    assert fixer_applies(LOG_NESTING)
    result = fix_declarations(NESTED_DECL)
    assert result.changed
    lines = result.text.split("\n")
    always_at = next(i for i, ln in enumerate(lines) if "always_ff" in ln)
    head = "\n".join(lines[:always_at])
    assert "logic [7:0] tmp;" in head and "int idx;" in head
    body = "\n".join(lines[always_at:])
    assert "logic [7:0] tmp;" not in body and "int idx;" not in body
    assert "idx = cycle_ctr - 5;" in body and "int idx" not in body
    assert "tmp = idx[7:0];" in body
    assert fix_declarations(result.text).text == result.text  # idempotent


def test_fixer_hoist_leaves_conflicting_and_module_level_declarations_alone() -> None:
    clean = NESTED_DECL.replace("            logic [7:0] tmp;\n            int idx = cycle_ctr - 5;\n", "")
    assert not fix_declarations(clean).changed
    conflict = NESTED_DECL.replace("    logic [4:0] cycle_ctr;\n", "    logic [4:0] cycle_ctr;\n    logic [15:0] tmp;\n")
    result = fix_declarations(conflict)
    assert "            logic [7:0] tmp;" in result.text  # different width: not touched
    assert "int idx;" in result.text
