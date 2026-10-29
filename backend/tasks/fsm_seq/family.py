"""fsm_seq family: knobs and renderer. Imported by verity.core.family.render_pack (never copied into a sandbox).

render(params) returns the full text of task.toml, reference.v and params.json for one knob setting.
At the default knobs the output is the same as the checked-in pack files.
pattern is the serial bit string to detect (first bit received first). overlap=false means the bits of a detection
are used up: the next occurrence must start with a later bit.
"""

import json

KNOBS: list[dict] = [
    {"id": "pattern", "kind": "bits", "default": "1011", "min_len": 3, "max_len": 8},
    {"id": "overlap", "kind": "bool", "default": True},
]

_WORDS = {3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight"}


def border_len(pattern: str) -> int:
    """Length of the longest proper prefix of pattern that is also a suffix (0 when the pattern cannot overlap itself)."""
    return next((k for k in range(len(pattern) - 1, 0, -1) if pattern[:k] == pattern[-k:]), 0)


def _q(text: str) -> str:
    return json.dumps(text, ensure_ascii=True)


def _task_toml(pattern: str, overlap: bool) -> str:
    n = len(pattern)
    border = border_len(pattern)
    example = pattern + pattern[border:]
    bit_list = ", ".join(pattern)
    if overlap:
        summary = f"a serial detector for the bit pattern {pattern}, overlaps allowed"
        if border:
            tail = f"Overlapping patterns count, so {example} is detected twice."
        else:
            tail = "Overlapping patterns count, but this pattern cannot overlap itself."
        rule = (
            f"detect is a register: at each rising clock edge it becomes 1 exactly when the last {_WORDS[n]} bits received on din "
            f"(including the bit sampled at this edge) are {bit_list} in arrival order, otherwise 0. It changes only on a clock edge."
        )
    else:
        summary = f"a serial detector for the bit pattern {pattern}, overlaps not allowed"
        tail = (
            "Detections do not overlap: after a detection all bits received so far are forgotten, so the next pattern must start with a later bit."
        )
        if border:
            tail += f" For example {example} is detected once."
        rule = (
            f"detect is a register: at each rising clock edge it becomes 1 exactly when the last {_WORDS[n]} bits received on din "
            f"(including the bit sampled at this edge) are {bit_list} in arrival order and none of those bits was already used by an "
            "earlier detection, otherwise 0. After a detection all earlier bits are forgotten, so detections never overlap. "
            "It changes only on a clock edge."
        )
    spec = (
        "A serial sequence detector. One bit arrives on din each clock edge. detect goes high for one cycle after the edge that "
        f"receives the last bit of the pattern {pattern} (first bit {pattern[0]}, last bit {pattern[-1]}). {tail} "
        "A synchronous active-low reset forgets all bits received so far."
    )
    reset_rule = "rst_n low at the edge clears detect to 0 and forgets all earlier bits. The first bit received after reset is the first bit of any pattern."
    lines = [
        'schema = "verity.task/1"',
        'id = "fsm_seq"',
        f"title = {_q(f'Sequence detector {pattern}')}",
        f"summary = {_q(summary)}",
        f"spec_text = {_q(spec)}",
        'top = "fsm_seq"',
        'family = "fsm"',
        'element_type = "none"',
        "latency_cycles = 1",
        "max_lines = 100",
        "test_count = 12",
        'default_priority = "area"',
        'priorities = ["area", "speed", "power"]',
        "target_clock_ns = 10.0",
        "max_plan_blocks = 4",
        "eval_only = false",
        "coding_rules = [",
        f"  {_q(rule)},",
        f"  {_q(reset_rule)},",
        "]",
        "",
        "[clock]",
        'name = "clk"',
        "",
        "[reset]",
        'name = "rst_n"',
        "active_low = true",
        "synchronous = true",
        "",
        "[sim]",
        "seed = 1",
        "max_cycles_per_case = 256",
        "",
        "[eqy]",
        "enabled = true",
        "depth = 10",
        "",
    ]
    for name, direction in (("clk", "in"), ("rst_n", "in"), ("din", "in"), ("detect", "out")):
        lines += ["[[ports]]", f'name = "{name}"', f'dir = "{direction}"', "width = 1", ""]
    return "\n".join(lines)


def _reference_v(pattern: str, overlap: bool) -> str:
    n = len(pattern)
    mode = "overlaps allowed" if overlap else "overlaps not allowed (a detection uses up its bits)"
    saturate = f"(seen == 4'd{n - 1}) ? seen : seen + 4'd1"
    seen_next = saturate if overlap else f"hit ? 4'd0 : {saturate}"
    return f"""// Human-written reference for fsm_seq (pack self-test, evals and the bench human reference; never shown to a model or a sandbox).
// Detects {pattern}, {mode}. detect is registered (latency 1).
// hist holds the last {n - 1} bits; seen counts how many of them are valid since reset (saturates at {n - 1}).
module fsm_seq (
    input        clk,
    input        rst_n,
    input        din,
    output reg   detect
);
    reg [{n - 2}:0] hist;
    reg [3:0] seen;
    wire [{n - 1}:0] window = {{hist, din}};
    wire hit = (seen == 4'd{n - 1}) && (window == {n}'b{pattern});

    always @(posedge clk) begin
        if (!rst_n) begin
            hist   <= {n - 1}'d0;
            seen   <= 4'd0;
            detect <= 1'b0;
        end else begin
            hist   <= window[{n - 2}:0];
            seen   <= {seen_next};
            detect <= hit;
        end
    end
endmodule
"""


def render(params: dict) -> dict[str, str]:
    pattern, overlap = str(params["pattern"]), bool(params["overlap"])
    return {
        "task.toml": _task_toml(pattern, overlap),
        "reference.v": _reference_v(pattern, overlap),
        "params.json": json.dumps({"pattern": pattern, "overlap": overlap}) + "\n",
    }
