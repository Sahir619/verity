"""normalize_comment_chars: Unicode hyphens, dashes and quotes in comments become ASCII before the RTL rules run.

Real cause: in a real MAC run two of five children were rejected before simulation because a model typed a
non-breaking hyphen in a comment. Code and string literals are never touched.
"""

import pytest

from verity.core.errors import RtlRejected
from verity.core.pack import TaskPack
from verity.core.rtl_fixer import normalize_comment_chars

NB_HYPHEN = "\u2011"
EN_DASH = "\u2013"
EM_DASH = "\u2014"
MINUS = "\u2212"
LQ, RQ = "\u201c", "\u201d"
APOS = "\u2019"


def test_line_and_block_comments_are_mapped() -> None:
    src = f"module m; // read{NB_HYPHEN}only {EN_DASH} {EM_DASH} {MINUS}1 {LQ}x{RQ} it{APOS}s\n/* a{NB_HYPHEN}b\n c{EM_DASH}d */\nendmodule\n"
    out = normalize_comment_chars(src)
    assert out == "module m; // read-only - - -1 \"x\" it's\n/* a-b\n c-d */\nendmodule\n"
    assert out.isascii()


def test_code_and_string_literals_are_never_touched() -> None:
    src = f'assign s = "k{NB_HYPHEN}x"; // c{NB_HYPHEN}c\nassign y = a {MINUS} b;\n'
    out = normalize_comment_chars(src)
    assert out == f'assign s = "k{NB_HYPHEN}x"; // c-c\nassign y = a {MINUS} b;\n', "only the comment changed"


def test_a_comment_marker_inside_a_string_does_not_start_a_comment() -> None:
    src = f'assign s = "// not a comment {NB_HYPHEN}"; assign t = 1; // real {NB_HYPHEN}\n'
    out = normalize_comment_chars(src)
    assert out == f'assign s = "// not a comment {NB_HYPHEN}"; assign t = 1; // real -\n'


def test_other_non_ascii_in_a_comment_is_left_for_the_rules_to_reject() -> None:
    src = "// pi \u03c0 and a\u2011b\nmodule m; endmodule\n"
    assert normalize_comment_chars(src) == "// pi \u03c0 and a-b\nmodule m; endmodule\n"


def test_ascii_text_is_returned_unchanged_and_an_unterminated_comment_is_handled() -> None:
    plain = "module m; // fine\nendmodule\n"
    assert normalize_comment_chars(plain) is plain
    assert normalize_comment_chars(f"/* open {EM_DASH} never closed") == "/* open - never closed"


def test_a_design_with_a_non_breaking_hyphen_in_a_comment_validates_after_the_pass(mac4: TaskPack) -> None:
    text = (mac4.reference_rtl() or "").rstrip("\n").split("\n")
    text.insert(1, f"    // keep the reset{NB_HYPHEN}value here")
    dirty = "\n".join(text) + "\n"
    with pytest.raises(RtlRejected) as raised:
        mac4.validate_rtl(dirty)
    assert any("non-ASCII" in p for p in raised.value.problems)
    mac4.validate_rtl(normalize_comment_chars(dirty))
