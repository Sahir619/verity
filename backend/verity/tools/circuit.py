"""Bounded CircuitGraph from Yosys pre-techmap JSON (backend/ARCHITECTURE 16, CONTRACTS 3.8).

Input: the output of `read_verilog -sv; hierarchy -check -top T; proc; opt; write_json pre.json`, so cells are still
readable ($add, $mul, $mux, $dff, ...).

Output: a wire-format dict (camelCase keys, `candidateId` empty) that validates against
verity.contracts.CircuitGraph. At most 60 nodes in total (ports included). Repeated lanes collapse into one node
labeled "<op> x<n>". If the graph is still too big after merging, the least important nodes are dropped and
`truncated` is set.

This module imports only the standard library at module level so it can be copied into the sandbox image
(for example as /verity/lib/verity_circuit.py) and used from /verity/bin/measure:

    python3 verity_circuit.py /tmp/verity/pre.json <top>      # prints the circuit JSON on stdout
"""

import json
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..contracts import CircuitGraph

MAX_NODES = 60
MAX_PORT_NODES = 30
MAX_MODULES = 30

_ADD_TYPES = frozenset({"$add", "$sub", "$neg", "$alu", "$macc"})
_MUL_TYPES = frozenset({"$mul"})
_MUX_TYPES = frozenset({"$mux", "$pmux", "$bmux", "$demux"})
_CMP_TYPES = frozenset({"$eq", "$ne", "$lt", "$le", "$gt", "$ge", "$eqx", "$nex"})
# Cells that carry no hardware meaning for the viewer.
_SKIP_TYPES = frozenset(
    {"$scopeinfo", "$assert", "$assume", "$cover", "$check", "$print", "$live", "$fair", "$initstate", "$anyconst"}
)
# Used only when a cell has no port_directions entry: these port names are outputs.
_OUTPUT_PORT_NAMES = frozenset({"Y", "Q", "QN", "O", "Z", "X", "CO", "S"})
# Importance when dropping nodes to fit the budget: lower number is kept first.
_KEEP_RANK = {"reg": 0, "mul": 1, "add": 2, "mux": 3, "submodule": 3, "cmp": 4, "logic": 5, "port": -1}


def _strip(name: str) -> str:
    return name[1:] if name.startswith("\\") else name


def _classify(cell_type: str, design_modules: dict[str, Any]) -> str:
    if cell_type in design_modules or not cell_type.startswith("$") or cell_type.startswith("$paramod"):
        return "submodule"
    if cell_type in _ADD_TYPES:
        return "add"
    if cell_type in _MUL_TYPES:
        return "mul"
    if cell_type in _MUX_TYPES:
        return "mux"
    if cell_type in _CMP_TYPES:
        return "cmp"
    if "dff" in cell_type or "latch" in cell_type:
        return "reg"
    return "logic"


def _real_bits(bits: Any) -> list[int]:
    """Net ids only: constants ("0", "1", "x", "z") are strings in Yosys JSON and carry no connectivity."""
    return [b for b in bits if isinstance(b, int)] if isinstance(bits, list) else []


@dataclass(slots=True)
class _Node:
    id: str
    kind: str
    base: str  # op name used for grouping and for merged labels ("add", "mul", "eq", "reg", module type)
    width: int
    single_label: str  # label when the node stands alone
    count: int = 1


def _cell_dirs(cell: dict[str, Any]) -> dict[str, str]:
    dirs = cell.get("port_directions")
    if isinstance(dirs, dict) and dirs:
        return {str(k): str(v) for k, v in dirs.items()}
    return {
        str(port): ("output" if str(port) in _OUTPUT_PORT_NAMES else "input")
        for port in (cell.get("connections") or {})
    }


def _public_names(netnames: dict[str, Any]) -> list[tuple[str, set[int]]]:
    out: list[tuple[str, set[int]]] = []
    for name, info in netnames.items():
        if not isinstance(info, dict) or name.startswith("$") or info.get("hide_name"):
            continue
        bits = set(_real_bits(info.get("bits")))
        if bits:
            out.append((_strip(name), bits))
    return out


def _net_name(q_bits: list[int], names: list[tuple[str, set[int]]]) -> str | None:
    wanted = set(q_bits)
    if not wanted:
        return None
    fits = [(len(bits), name) for name, bits in names if wanted <= bits]
    return min(fits)[1] if fits else None


def _stats(design_modules: dict[str, Any], module_name: str, depth: int = 0) -> dict[str, int]:
    stats = {"regs": 0, "adders": 0, "multipliers": 0, "muxes": 0}
    module = design_modules.get(module_name)
    if not isinstance(module, dict) or depth > 8:
        return stats
    for cell in (module.get("cells") or {}).values():
        ctype = str(cell.get("type", ""))
        if ctype in _SKIP_TYPES:
            continue
        kind = _classify(ctype, design_modules)
        if kind == "reg":
            stats["regs"] += 1
        elif kind == "add":
            stats["adders"] += 1
        elif kind == "mul":
            stats["multipliers"] += 1
        elif kind == "mux":
            stats["muxes"] += 1
        elif kind == "submodule" and ctype in design_modules:
            for key, value in _stats(design_modules, ctype, depth + 1).items():
                stats[key] += value
    return stats


def _merge(
    nodes: dict[str, _Node],
    edges: dict[tuple[str, str], int],
    key_of: Any,
    ports: set[str],
) -> tuple[dict[str, _Node], dict[tuple[str, str], int]]:
    """Merge cell nodes with the same key. Port nodes are never merged. Edge width becomes the max of the merged edges."""
    group_of: dict[str, str] = {}
    merged: dict[str, _Node] = {}
    by_key: dict[Any, str] = {}
    for node_id, node in nodes.items():
        if node_id in ports:
            group_of[node_id] = node_id
            merged[node_id] = node
            continue
        key = key_of(node_id, node)
        gid = by_key.setdefault(key, node_id)
        group_of[node_id] = gid
        if gid == node_id:
            merged[gid] = _Node(node.id, node.kind, node.base, node.width, node.single_label, node.count)
        else:
            target = merged[gid]
            target.count += node.count
            target.width = max(target.width, node.width)
    new_edges: dict[tuple[str, str], int] = {}
    for (src, dst), width in edges.items():
        a, b = group_of[src], group_of[dst]
        if a == b:
            continue
        new_edges[(a, b)] = max(new_edges.get((a, b), 0), width)
    return merged, new_edges


def build_circuit_dict(pre: dict[str, Any], top: str) -> dict[str, Any]:
    """Wire-format circuit dict (see module docstring). Raises ValueError if the top module is missing."""
    # UNVERIFIED: Yosys write_json keys used here (ports[].signed, cells[].port_directions, netnames[].hide_name,
    # constants as "0"/"1"/"x"/"z" strings in bit lists). Check against a real pre.json on test day.
    design_modules: dict[str, Any] = {_strip(k): v for k, v in (pre.get("modules") or {}).items()}
    module = design_modules.get(top)
    if not isinstance(module, dict):
        raise ValueError(f"top module {top!r} not found in Yosys JSON (found: {sorted(design_modules)[:5]})")

    truncated = False

    # ---- ports ----
    port_info: list[dict[str, Any]] = []
    port_nodes: dict[str, _Node] = {}
    drivers: dict[int, str] = {}
    out_port_bits: list[tuple[str, list[int]]] = []
    for pname, pinfo in (module.get("ports") or {}).items():
        direction = {"input": "in", "output": "out"}.get(str(pinfo.get("direction")))
        if direction is None:
            continue
        name = _strip(pname)
        bits = list(pinfo.get("bits") or [])
        width = max(1, len(bits))
        if len(port_info) >= MAX_PORT_NODES:
            truncated = True
            continue
        port_info.append({"name": name, "dir": direction, "width": width, "signed": bool(pinfo.get("signed"))})
        node_id = f"p_{name}"
        port_nodes[node_id] = _Node(node_id, "port", "port", width, name)
        if direction == "in":
            for bit in _real_bits(bits):
                drivers[bit] = node_id
        else:
            out_port_bits.append((node_id, _real_bits(bits)))

    # ---- cells ----
    names = _public_names(module.get("netnames") or {})
    cell_nodes: dict[str, _Node] = {}
    cell_inputs: dict[str, list[int]] = {}
    submodules: list[dict[str, Any]] = []
    seen_modules: set[str] = set()
    index = 0
    for cname, cell in (module.get("cells") or {}).items():
        ctype = str(cell.get("type", ""))
        if ctype in _SKIP_TYPES:
            continue
        index += 1
        kind = _classify(ctype, design_modules)
        dirs = _cell_dirs(cell)
        conns: dict[str, Any] = cell.get("connections") or {}
        out_bits: list[int] = []
        in_bits: list[int] = []
        raw_out_len = 0
        raw_in_len = 0
        q_bits: list[int] = []
        for port, bits in conns.items():
            real = _real_bits(bits)
            if dirs.get(str(port), "input") == "output":
                out_bits.extend(real)
                raw_out_len += len(bits) if isinstance(bits, list) else 0
                if str(port) == "Q":
                    q_bits = real
            else:
                in_bits.extend(real)
                raw_in_len = max(raw_in_len, len(bits) if isinstance(bits, list) else 0)
        # Width: what the cell produces. Comparators produce 1 bit, so show the operand width instead.
        width = raw_in_len if kind == "cmp" else raw_out_len
        width = max(1, width)

        node_id = f"n{index}"
        if kind == "submodule":
            module_type = _strip(ctype)
            inst = _strip(cname)
            base = module_type
            single = inst
            if inst not in seen_modules and len(submodules) < MAX_MODULES:
                seen_modules.add(inst)
                submodules.append({"name": inst, "instanceOf": module_type})
        else:
            op = ctype.lstrip("$")
            base = "reg" if kind == "reg" else op
            single = (_net_name(q_bits, names) or "reg") if kind == "reg" else op
        cell_nodes[node_id] = _Node(node_id, kind, base, width, single)
        cell_inputs[node_id] = in_bits
        for bit in out_bits:
            drivers[bit] = node_id

    nodes: dict[str, _Node] = {**port_nodes, **cell_nodes}

    # ---- edges (bit-level connectivity folded into node pairs) ----
    edge_bits: dict[tuple[str, str], set[int]] = {}
    for node_id, bits in cell_inputs.items():
        for bit in bits:
            src = drivers.get(bit)
            if src is not None and src != node_id:
                edge_bits.setdefault((src, node_id), set()).add(bit)
    for node_id, bits in out_port_bits:
        for bit in bits:
            src = drivers.get(bit)
            if src is not None and src != node_id:
                edge_bits.setdefault((src, node_id), set()).add(bit)
    edges: dict[tuple[str, str], int] = {key: max(1, len(bits)) for key, bits in edge_bits.items()}

    # ---- fit into the node budget ----
    port_ids = set(port_nodes)
    budget = max(0, MAX_NODES - len(port_nodes))
    targets: dict[str, frozenset[str]] = {}
    for (src, dst) in edges:
        targets[src] = targets.get(src, frozenset()) | {dst}

    def _cells(n: dict[str, _Node]) -> int:
        return len(n) - len(port_ids)

    # Always: merge cells of the same kind and width that feed the same targets.
    nodes, edges = _merge(
        nodes, edges, lambda i, n: (n.kind, n.width, n.base, targets.get(i, frozenset())), port_ids
    )
    if _cells(nodes) > budget:
        # Collapse repeated lanes: same op and width, whatever they feed.
        nodes, edges = _merge(nodes, edges, lambda i, n: (n.kind, n.width, n.base), port_ids)
    if _cells(nodes) > budget:
        # Same op, any width.
        nodes, edges = _merge(nodes, edges, lambda i, n: (n.kind, n.base), port_ids)
    if _cells(nodes) > budget:
        truncated = True
        cells = sorted(
            (n for i, n in nodes.items() if i not in port_ids),
            key=lambda n: (_KEEP_RANK.get(n.kind, 9), -n.width, n.id),
        )
        keep = {n.id for n in cells[:budget]} | port_ids
        nodes = {i: n for i, n in nodes.items() if i in keep}
        edges = {(a, b): w for (a, b), w in edges.items() if a in keep and b in keep}

    def _label(node: _Node) -> str:
        return f"{node.base} x{node.count}" if node.count > 1 else node.single_label

    return {
        "format": "verity.circuit/1",
        "candidateId": "",
        "top": top,
        "modules": [{"name": top}, *submodules],
        "ports": port_info,
        "nodes": [
            {"id": n.id, "kind": n.kind, "label": _label(n), "width": n.width, "module": top} for n in nodes.values()
        ],
        "edges": [{"source": a, "target": b, "width": w} for (a, b), w in edges.items()],
        "truncated": truncated,
        "stats": _stats(design_modules, top),
    }


def build_circuit_graph(pre: dict[str, Any], top: str) -> "CircuitGraph":
    """Same as build_circuit_dict, validated into the contract model (raises ValidationError if invalid)."""
    from ..contracts import CircuitGraph

    return CircuitGraph.model_validate(build_circuit_dict(pre, top))


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: circuit.py <pre.json> <top>", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as fh:
        pre = json.load(fh)
    print(json.dumps(build_circuit_dict(pre, argv[2]), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
