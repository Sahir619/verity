"""Planted bugs for the sign-off test-strength check (core/mutants.py): fixed rules, one edit each, RTL rules kept."""

from collections import Counter

from verity.core.mutants import RULE_ORDER, mask, plant
from verity.core.pack import TaskPack

CNT = """module cnt8 (
    input  logic       clk,
    input  logic       rst_n,
    input  logic       en,
    output logic [7:0] q
);
    // q <= 8'd99 in a comment must never be touched
    always_ff @(posedge clk) begin
        if (!rst_n)
            q <= 8'd0;
        else if (en)
            q <= q + 8'd1;
    end
endmodule
"""

MAC_LIKE = """module mac4 (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        start,
    input  logic [31:0] a,
    input  logic [31:0] b,
    output logic [21:0] acc,
    output logic        done
);
    logic [4:0] cnt;
    logic       busy;
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            acc <= 22'd0; cnt <= 5'd0; busy <= 1'b0; done <= 1'b0;
        end else if (start && !busy && !done) begin
            acc <= 22'd0; cnt <= 5'd0; busy <= 1'b1;
        end else if (busy) begin
            acc <= acc + (a[7:0] * b[7:0]) + (a[15:8] * b[15:8]);
            cnt <= cnt + 5'd1;
            if (cnt == 5'd15) begin busy <= 1'b0; done <= 1'b1; end
        end
    end
endmodule
"""

ADD = """module add8 (
    input  logic [7:0] a,
    input  logic [7:0] b,
    input  logic       cin,
    output logic [7:0] sum,
    output logic       cout
);
    assign {cout, sum} = a + b + cin;
endmodule
"""


def _rtl(pack: TaskPack, text: str) -> str:
    return pack.validate_rtl(text).text


def test_mask_keeps_length_and_blanks_comments_and_strings() -> None:
    text = 'a // c <= 1\nb /* x\ny */ "s<s"\n'
    masked = mask(text)
    assert len(masked) == len(text) and masked.count("\n") == text.count("\n")
    assert "<=" not in masked and "s<s" not in masked and masked.startswith("a ") and "b" in masked


def test_counter_gets_every_rule_that_applies(packs) -> None:
    pack = packs["cnt8"]
    text = _rtl(pack, CNT)
    mutants = plant(text, pack)
    rules = {m.rule for m in mutants}
    assert {"drop_reset", "invert_enable", "flip_operator", "const_plus_one"} <= rules
    by_rule = {m.rule: m for m in mutants}
    assert "if (1'b0)" in by_rule["drop_reset"].text
    assert "else if (!(en))" in by_rule["invert_enable"].text
    assert "q - 8'd1" in by_rule["flip_operator"].text
    for m in mutants:
        assert m.text != text
        assert "8'd99" in m.text, "comments are never edited"
        assert sum(1 for a, b in zip(m.text.splitlines(), text.splitlines()) if a != b) == 1, "one line changes"
        pack.validate_rtl(m.text)  # every mutant passes the RTL rules


def test_non_blocking_assignments_are_never_flipped(packs) -> None:
    pack = packs["mac4"]
    text = _rtl(pack, MAC_LIKE)
    mutants = plant(text, pack, limit=40)
    for m in mutants:
        if m.rule in ("flip_compare", "off_by_one") and m.before in ("<=", "<"):
            assert m.before != "<=", "an assignment <= outside parentheses was treated as a comparison"
    flips = [m for m in mutants if m.rule == "flip_compare"]
    assert any(m.before == "==" and m.after == "!=" for m in flips)
    assert any(m.rule == "off_by_one" and m.before == "15" and m.after == "14" for m in mutants)
    assert any(m.rule == "invert_enable" and "start" in m.before for m in mutants)
    assert all("[7:0]" in m.text and "[15:8]" in m.text for m in mutants), "nothing inside [...] changes"


def test_round_robin_spreads_the_rules_and_respects_the_limit(packs) -> None:
    pack = packs["mac4"]
    text = _rtl(pack, MAC_LIKE)
    mutants = plant(text, pack, limit=12)
    assert len(mutants) == 12
    assert [m.id for m in mutants] == [f"m{i:02d}" for i in range(1, 13)]
    counts = Counter(m.rule for m in mutants)
    assert counts["const_plus_one"] <= 4, counts
    assert mutants[0].rule == RULE_ORDER[0]
    assert len({m.text for m in mutants}) == len(mutants), "no duplicates"


def test_planting_is_deterministic(packs) -> None:
    pack = packs["mac4"]
    text = _rtl(pack, MAC_LIKE)
    assert [(m.rule, m.line, m.text) for m in plant(text, pack)] == [(m.rule, m.line, m.text) for m in plant(text, pack)]


def test_a_one_line_datapath_still_gets_planted_bugs(packs) -> None:
    pack = packs["add8"]
    mutants = plant(_rtl(pack, ADD), pack)
    assert mutants and all(m.rule == "flip_operator" for m in mutants)
    assert {m.text.splitlines()[7].strip() for m in mutants} == {
        "assign {cout, sum} = a - b + cin;",
        "assign {cout, sum} = a + b - cin;",
    }


def test_ternary_arms_and_subtraction_are_swapped(packs) -> None:
    pack = packs["cnt8"]
    text = _rtl(pack, CNT.replace("q <= q + 8'd1;", "q <= (q == 8'd7) ? q - 8'd7 : q + 8'd1;"))
    swaps = [m for m in plant(text, pack, limit=40) if m.rule == "swap_operands"]
    assert any("? q + 8'd1 : q - 8'd7" in m.text for m in swaps)


def test_the_reference_designs_all_get_mutants_that_pass_the_rules(packs) -> None:
    for pid in ("mac4", "cnt8", "fsm_seq", "add8"):
        pack = packs[pid]
        mutants = plant(_rtl(pack, pack.reference_rtl() or ""), pack)
        assert mutants, pid
        assert len(mutants) <= 20
