"""add8 family: knobs and renderer. Imported by verity.core.family.render_pack (never copied into a sandbox).

render(params) returns the full text of task.toml, reference.v and params.json for one knob setting.
At the default knobs the output is the same as the checked-in pack files.
"""

import json

KNOBS: list[dict] = [
    {"id": "width", "kind": "int", "default": 8, "min": 4, "max": 32},
]


def _q(text: str) -> str:
    return json.dumps(text, ensure_ascii=True)


def _a(width: int) -> str:
    return "An" if width in (8, 11, 18) else "A"


def _task_toml(width: int) -> str:
    top = width - 1
    spec = (
        f"{_a(width)} {width}-bit adder with carry in and carry out. sum is the low {width} bits of a + b + cin and cout is "
        f"the carry out of bit {top}. The design is purely combinational, with no clock and no reset."
    )
    rule = "The design is purely combinational: no clock, no registers, no reset. sum and cout must follow a, b and cin immediately."
    lines = [
        'schema = "verity.task/1"',
        'id = "add8"',
        f"title = {_q(f'{width}-bit adder with carry')}",
        f"summary = {_q(f'{_a(width).lower()} {width}-bit combinational adder with carry in and carry out')}",
        f"spec_text = {_q(spec)}",
        'top = "add8"',
        'family = "adder"',
        f'element_type = "{ {8: "uint8", 4: "uint4"}.get(width, "none") }"',
        "latency_cycles = 0",
        "max_lines = 60",
        "test_count = 12",
        'default_priority = "area"',
        'priorities = ["area", "speed", "power"]',
        "target_clock_ns = 10.0",
        "max_plan_blocks = 4",
        "eval_only = false",
        "coding_rules = [",
        f"  {_q(rule)},",
        "]",
        "",
        "[clock]",
        'name = ""',
        "",
        "[reset]",
        'name = ""',
        "",
        "[sim]",
        "seed = 1",
        "max_cycles_per_case = 8",
        "",
        "[eqy]",
        "enabled = true",
        "depth = 4",
        "",
    ]
    for name, direction, w in [("a", "in", width), ("b", "in", width), ("cin", "in", 1), ("sum", "out", width), ("cout", "out", 1)]:
        lines += ["[[ports]]", f'name = "{name}"', f'dir = "{direction}"', f"width = {w}", ""]
    return "\n".join(lines)


def _reference_v(width: int) -> str:
    top = width - 1
    return f"""// Human-written reference for add8 (pack self-test, evals and the bench human reference; never shown to a model or a sandbox).
module add8 (
    input  [{top}:0] a,
    input  [{top}:0] b,
    input        cin,
    output [{top}:0] sum,
    output       cout
);
    wire [{width}:0] total = {{1'b0, a}} + {{1'b0, b}} + {{{width}'d0, cin}};
    assign sum  = total[{top}:0];
    assign cout = total[{width}];
endmodule
"""


def render(params: dict) -> dict[str, str]:
    width = int(params["width"])
    return {
        "task.toml": _task_toml(width),
        "reference.v": _reference_v(width),
        "params.json": json.dumps({"width": width}) + "\n",
    }
