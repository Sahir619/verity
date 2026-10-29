"""Rtl validation (ARCHITECTURE 12.3): banned constructs and size limits are rejected in Python, before any sandbox."""

import pytest

from verity.core.errors import RtlRejected
from verity.core.rtl import Rtl, count_code_lines, find_problems, normalize_rtl_text, strip_comments

GOOD = """module demo (
    input        clk,
    input  [7:0] a,
    output reg [7:0] q
);
    always @(posedge clk) q <= a + 8'd1;
endmodule
"""


def validate(text: str, **kw) -> Rtl:
    return Rtl.validate(text, top="demo", max_lines=kw.pop("max_lines", 100), **kw)


def problems(text: str, **kw) -> list[str]:
    return find_problems(normalize_rtl_text(text), top="demo", max_lines=kw.pop("max_lines", 100), **kw)


def insert(snippet: str) -> str:
    return GOOD.replace("endmodule", snippet + "endmodule")


def test_good_rtl_is_accepted_and_normalized() -> None:
    rtl = validate(GOOD.replace("\n", "\r\n") + "\n\n")
    assert rtl.text == GOOD
    assert rtl.lines == count_code_lines(GOOD) == 7
    assert len(rtl.sha256) == 64
    assert rtl.data == GOOD.encode("ascii")
    assert validate(GOOD).sha256 == rtl.sha256  # stable across line endings


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        ("    initial begin end\n", "initial"),
        ('    always @(*) $display("x");\n', "$display"),
        ("    always @(*) $finish;\n", "$finish"),
        ('`include "other.v"\n', "include"),
        ("    always @(*) force q = 1'b0;\n", "force"),
        ("    always @(*) release q;\n", "release"),
        ('    import "DPI-C" function void f();\n', "DPI"),
        ('`define P(a) $``a\n    always @(*) `P(display)("x");\n', "token pasting"),
    ],
)
def test_banned_constructs_are_rejected(snippet: str, expected: str) -> None:
    found = problems(insert(snippet))
    assert any(expected in p for p in found), found
    with pytest.raises(RtlRejected) as err:
        validate(insert(snippet))
    assert err.value.problems


def test_allowed_system_functions_pass() -> None:
    text = insert("    wire [3:0] w = $clog2(16);\n    wire signed [7:0] s = $signed(a);\n    wire [7:0] u = $unsigned(s);\n")
    assert problems(text) == []


def test_banned_words_inside_comments_do_not_count() -> None:
    text = "// initial block, $display and force are mentioned here\n" + GOOD.replace("endmodule", "/* release */ endmodule")
    assert problems(text) == []
    assert "initial" not in strip_comments(text)


def test_non_ascii_is_rejected() -> None:
    found = problems(GOOD + "// café\n")
    assert any("non-ASCII" in p for p in found)


def test_size_limits() -> None:
    many = GOOD.replace("endmodule", "    wire w;\n" * 200 + "endmodule")
    assert any("non-blank lines" in p for p in problems(many, max_lines=100))
    assert any("bytes" in p for p in problems(GOOD, max_bytes=50))


def test_top_module_checks() -> None:
    assert any("not found" in p for p in find_problems(GOOD.replace("demo", "other"), top="demo", max_lines=100))
    assert any("more than once" in p for p in problems(GOOD + GOOD))
    assert any("no module" in p for p in problems("wire x;\n"))
    assert any("endmodule counts" in p for p in problems(GOOD.replace("endmodule", "")))
