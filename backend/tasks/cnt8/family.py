"""cnt8 family: knobs and renderer. Imported by verity.core.family.render_pack (never copied into a sandbox).

render(params) returns the full text of task.toml, reference.v and params.json for one knob setting.
At the default knobs the output is the same as the checked-in pack files.
enable=false removes the en port and the enable wording: the counter is free-running.
"""

import json

KNOBS: list[dict] = [
    {"id": "width", "kind": "int", "default": 8, "min": 4, "max": 16},
    {"id": "enable", "kind": "bool", "default": True},
]

RESET_MARGIN = 6  # reset_dut holds reset for 3 rising edges (+1 release edge) before the steps; 2 more edges of slack


def wrap_edges(width: int) -> int:
    """Edges the testbench clocks to show a full wrap (one past the top value, one more to show 0 then 1). Rendered into params.json."""
    return (1 << width) + 2


def _q(text: str) -> str:
    return json.dumps(text, ensure_ascii=True)


def _a(width: int) -> str:
    return "An" if width in (8, 11, 18) else "A"


def _task_toml(width: int, enable: bool) -> str:
    top = (1 << width) - 1
    if enable:
        title = f"{width}-bit counter with enable"
        summary = f"{_a(width).lower()} {width}-bit up counter with enable and synchronous reset"
        spec = (
            f"{_a(width)} {width}-bit up counter with enable. On each rising clock edge q increases by 1 when en is high and "
            f"holds when en is low. q wraps from {top} to 0. A synchronous active-low reset clears q to 0 and wins over en."
        )
        rule = (
            f"q is a register: it changes only on a rising clock edge, to q + 1 (modulo {1 << width}) when en is 1 and rst_n "
            "is 1, and it holds otherwise. rst_n low at the edge sets q to 0 regardless of en."
        )
    else:
        title = f"{width}-bit free-running counter"
        summary = f"{_a(width).lower()} {width}-bit free-running up counter with synchronous reset"
        spec = (
            f"{_a(width)} {width}-bit free-running up counter. On each rising clock edge q increases by 1. q wraps from "
            f"{top} to 0. A synchronous active-low reset clears q to 0."
        )
        rule = (
            f"q is a register: it changes only on a rising clock edge, to q + 1 (modulo {1 << width}) when rst_n is 1. "
            "rst_n low at the edge sets q to 0."
        )
    max_cycles = max(512, wrap_edges(width) + RESET_MARGIN)
    lines = [
        'schema = "verity.task/1"',
        'id = "cnt8"',
        f"title = {_q(title)}",
        f"summary = {_q(summary)}",
        f"spec_text = {_q(spec)}",
        'top = "cnt8"',
        'family = "counter"',
        f'element_type = "{ {8: "uint8", 4: "uint4"}.get(width, "none") }"',
        "latency_cycles = 1",
        "max_lines = 60",
        "test_count = 10",
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
        'name = "clk"',
        "",
        "[reset]",
        'name = "rst_n"',
        "active_low = true",
        "synchronous = true",
        "",
        "[sim]",
        "seed = 1",
        f"max_cycles_per_case = {max_cycles}",
        "",
        "[eqy]",
        "enabled = true",
        "depth = 8",
        "",
    ]
    ports = [("clk", "in", 1), ("rst_n", "in", 1)] + ([("en", "in", 1)] if enable else []) + [("q", "out", width)]
    for name, direction, w in ports:
        lines += ["[[ports]]", f'name = "{name}"', f'dir = "{direction}"', f"width = {w}", ""]
    return "\n".join(lines)


def _reference_v(width: int, enable: bool) -> str:
    top = width - 1
    en_port = "    input        en,\n" if enable else ""
    cond = "else if (en)" if enable else "else"
    return f"""// Human-written reference for cnt8 (pack self-test, evals and the bench human reference; never shown to a model or a sandbox).
module cnt8 (
    input        clk,
    input        rst_n,
{en_port}    output reg [{top}:0] q
);
    always @(posedge clk) begin
        if (!rst_n)
            q <= {width}'d0;
        {cond}
            q <= q + {width}'d1;
    end
endmodule
"""


def render(params: dict) -> dict[str, str]:
    width, enable = int(params["width"]), bool(params["enable"])
    return {
        "task.toml": _task_toml(width, enable),
        "reference.v": _reference_v(width, enable),
        "params.json": json.dumps({"width": width, "enable": enable, "wrap_edges": wrap_edges(width)}) + "\n",
    }
