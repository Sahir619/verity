"""mac4 family: knobs and renderer. Imported by verity.core.family.render_pack (never copied into a sandbox).

render(params) returns the full text of task.toml, reference.v and params.json for one knob setting.
At the default knobs the output is behaviourally the same as the checked-in pack files.
"""

import json

KNOBS: list[dict] = [
    {"id": "lanes", "kind": "int", "default": 4, "min": 1, "max": 8},
    {"id": "width", "kind": "int", "default": 8, "min": 4, "max": 8},
    {"id": "cycles", "kind": "int", "default": 16, "min": 4, "max": 32},
]


def _clog2(n: int) -> int:
    return (n - 1).bit_length()


def _sum_expr(lanes: int) -> str:
    if lanes <= 4:
        return " + ".join(f"a[{i}]*b[{i}]" for i in range(lanes))
    return f"a[0]*b[0] + a[1]*b[1] + ... + a[{lanes - 1}]*b[{lanes - 1}]"


def _q(text: str) -> str:
    return json.dumps(text, ensure_ascii=True)


def _task_toml(lanes: int, width: int, cycles: int) -> str:
    acc = 2 * width + _clog2(lanes * cycles)
    plural = "lane" if lanes == 1 else "lanes"
    spec = (
        f"A {lanes}-lane multiply-accumulate unit. Each cycle it multiplies {lanes} {'pair' if lanes == 1 else 'pairs'} "
        f"of unsigned {width}-bit inputs and adds the {'product' if lanes == 1 else 'products'} to a running sum. "
        f"After {cycles} cycles it raises done and holds the {acc}-bit result until reset. Smallest area matters most."
    )
    rules = [
        "Protocol: pulse start high for one clock edge while idle. That edge is edge 0: it clears acc and starts the run, "
        "and the a and b inputs on edge 0 are ignored.",
        f"Edges 1 to {cycles} each add {_sum_expr(lanes)} (the unsigned values on a and b at that edge, lanes as defined "
        "by the header) to acc.",
        f"done must be 0 until edge {cycles} and 1 right after edge {cycles}. After edge {cycles}, acc holds the final "
        f"{acc}-bit sum and done stays 1 until reset; start is ignored while a run is in progress or done is 1.",
        "After reset, acc and done are 0.",
    ]
    lines = [
        'schema = "verity.task/1"',
        'id = "mac4"',
        f"title = {_q(f'{lanes}-lane MAC array, {width}-bit')}",
        f"summary = {_q(f'{lanes} {plural} of {width}-bit multiply-accumulate over {cycles} cycles')}",
        f"spec_text = {_q(spec)}",
        'top = "mac4"',
        'family = "mac"',
        f'element_type = "{ {8: "uint8", 4: "uint4"}.get(width, "none") }"',
        f"latency_cycles = {cycles}",
        "max_lines = 300",
        "test_count = 16",
        'default_priority = "area"',
        'priorities = ["area", "speed", "power"]',
        "target_clock_ns = 10.0",
        "max_plan_blocks = 8",
        "eval_only = false",
        "coding_rules = [",
        *[f"  {_q(rule)}," for rule in rules],
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
        f"max_cycles_per_case = {max(64, 4 * cycles)}",
        "",
        "[eqy]",
        "enabled = true",
        f"depth = {cycles + 8}",
        "",
    ]
    ports = [
        ("clk", "in", 1, 1),
        ("rst_n", "in", 1, 1),
        ("start", "in", 1, 1),
        ("a", "in", width, lanes),
        ("b", "in", width, lanes),
        ("acc", "out", acc, 1),
        ("done", "out", 1, 1),
    ]
    for name, direction, w, count in ports:
        lines += ["[[ports]]", f'name = "{name}"', f'dir = "{direction}"', f"width = {w}"]
        if count > 1:
            lines.append(f"count = {count}")
        lines.append("")
    return "\n".join(lines)


def _reference_v(lanes: int, width: int, cycles: int) -> str:
    acc = 2 * width + _clog2(lanes * cycles)
    cw = max(1, _clog2(cycles))
    bus = lanes * width
    return f"""// Human-written reference for mac4 (pack self-test, evals and the bench human reference; never shown to a model or a sandbox).
// Rendered for lanes={lanes}, width={width}, cycles={cycles}.
// Edge 0: start accepted, acc cleared. Edges 1 to {cycles}: acc += sum of the lane products.
// done rises after edge {cycles} and stays until reset.
module mac4 (
    input         clk,
    input         rst_n,
    input         start,
    input  [{bus - 1}:0] a,
    input  [{bus - 1}:0] b,
    output reg [{acc - 1}:0] acc,
    output reg        done
);
    localparam LANES = {lanes};
    localparam W = {width};
    localparam CYCLES = {cycles};
    localparam ACC = {acc};

    reg            busy;
    reg [{cw - 1}:0] cnt;
    reg [ACC-1:0]  lane_sum;
    integer        i;

    always @* begin
        lane_sum = {{ACC{{1'b0}}}};
        for (i = 0; i < LANES; i = i + 1)
            lane_sum = lane_sum + a[i*W +: W] * b[i*W +: W];
    end

    always @(posedge clk) begin
        if (!rst_n) begin
            acc  <= {{ACC{{1'b0}}}};
            done <= 1'b0;
            busy <= 1'b0;
            cnt  <= {cw}'d0;
        end else if (busy) begin
            acc <= acc + lane_sum;
            cnt <= cnt + {cw}'d1;
            if (cnt == CYCLES - 1) begin
                busy <= 1'b0;
                done <= 1'b1;
            end
        end else if (start && !done) begin
            acc  <= {{ACC{{1'b0}}}};
            cnt  <= {cw}'d0;
            busy <= 1'b1;
        end
    end
endmodule
"""


def render(params: dict) -> dict[str, str]:
    lanes, width, cycles = int(params["lanes"]), int(params["width"]), int(params["cycles"])
    return {
        "task.toml": _task_toml(lanes, width, cycles),
        "reference.v": _reference_v(lanes, width, cycles),
        "params.json": json.dumps({"lanes": lanes, "width": width, "cycles": cycles}) + "\n",
    }
