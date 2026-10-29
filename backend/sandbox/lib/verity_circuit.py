"""CircuitGraph builder (CONTRACTS 3.8): Yosys pre-techmap `write_json` output to a small block diagram graph.

Pure standard library, so it runs inside the sandbox image. Output uses the wire format (camelCase keys) of
verity.contracts.circuit.CircuitGraph with candidateId "" (the backend fills it). No coordinates: the web app
lays the graph out.

Cell classes: $add $sub $neg $alu $macc -> add; $mul -> mul; $mux $pmux $bmux $demux -> mux; the compare family
-> cmp; any $*dff* or $*latch* -> reg; non-$ cell types -> submodule; everything else -> logic. Module ports
become `port` nodes. At most MAX_NODES nodes: same-kind same-width cells with the same targets merge into one
node labeled "<kind> x<n>"; if it is still too big the remainder is dropped and `truncated` is true.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

MAX_NODES = 60
FORMAT = "verity.circuit/1"

_ADD = {"$add", "$sub", "$neg", "$alu", "$macc"}
_MUL = {"$mul"}
_MUX = {"$mux", "$pmux", "$bmux", "$demux"}
_CMP = {"$eq", "$ne", "$lt", "$le", "$gt", "$ge", "$eqx", "$nex", "$logic_not", "$logic_and", "$logic_or"}
# Cell ports that carry the clock: edges from the clock port to every register only add clutter.
_CLOCK_PINS = {"CLK", "C", "G"}
_KIND_ORDER = {"port": 0, "reg": 1, "mul": 2, "add": 3, "mux": 4, "cmp": 5, "submodule": 6, "logic": 7}


def classify(cell_type: str) -> str:
    if not cell_type.startswith("$"):
        return "submodule"
    if cell_type in _ADD:
        return "add"
    if cell_type in _MUL:
        return "mul"
    if cell_type in _MUX:
        return "mux"
    if cell_type in _CMP:
        return "cmp"
    if re.search(r"dff|latch", cell_type):
        return "reg"
    return "logic"


def _is_net(bit: Any) -> bool:
    return isinstance(bit, int)


def _cell_width(cell: dict[str, Any]) -> int:
    params = cell.get("parameters", {})
    for key in ("Y_WIDTH", "WIDTH", "A_WIDTH"):
        value = params.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value and set(value) <= {"0", "1"}:
            return int(value, 2)  # Yosys writes non-32-bit parameters as binary strings
    dirs = cell.get("port_directions", {})
    widths = [
        sum(1 for b in bits if _is_net(b))
        for pin, bits in cell.get("connections", {}).items()
        if dirs.get(pin) == "output"
    ]
    widths = [w for w in widths if w] or [
        sum(1 for b in bits if _is_net(b)) for bits in cell.get("connections", {}).values()
    ]
    return max(widths) if widths else 1


def build_circuit(pre: dict[str, Any], top: str) -> dict[str, Any] | None:
    """Return the circuit dict (wire format) or None when the module is missing."""
    module = pre.get("modules", {}).get(top)
    if module is None:
        return None

    ports: list[dict[str, Any]] = []
    # raw nodes: id -> {kind, width, label, count}
    raw: dict[str, dict[str, Any]] = {}
    drivers: dict[int, set[str]] = defaultdict(set)  # net bit -> node ids that drive it
    loaders: dict[int, set[str]] = defaultdict(set)  # net bit -> node ids that read it

    for name, port in module.get("ports", {}).items():
        direction = port.get("direction")
        bits = port.get("bits", [])
        if direction not in ("input", "output"):
            continue
        signed = bool(port.get("signed"))
        ports.append({"name": name, "dir": "in" if direction == "input" else "out", "width": len(bits), "signed": signed})
        node_id = f"p-{name}"
        raw[node_id] = {"kind": "port", "width": len(bits), "label": name, "count": 1}
        for bit in bits:
            if _is_net(bit):
                (drivers if direction == "input" else loaders)[bit].add(node_id)

    stats = {"regs": 0, "adders": 0, "multipliers": 0, "muxes": 0}
    modules = [{"name": top}]
    for index, (cell_name, cell) in enumerate(module.get("cells", {}).items(), start=1):
        ctype = str(cell.get("type", ""))
        kind = classify(ctype)
        width = _cell_width(cell)
        node_id = f"n{index}"
        label = ctype.lstrip("$") if kind in ("submodule", "logic") else kind
        raw[node_id] = {"kind": kind, "width": width, "label": label, "count": 1}
        if kind == "reg":
            stats["regs"] += 1
        elif kind == "add":
            stats["adders"] += 1
        elif kind == "mul":
            stats["multipliers"] += 1
        elif kind == "mux":
            stats["muxes"] += 1
        elif kind == "submodule":
            modules.append({"name": cell_name.lstrip("\\"), "instanceOf": ctype.lstrip("\\")})
        dirs = cell.get("port_directions", {})
        for pin, bits in cell.get("connections", {}).items():
            if kind == "reg" and pin in _CLOCK_PINS:
                continue
            side = drivers if dirs.get(pin) == "output" else loaders
            for bit in bits:
                if _is_net(bit):
                    side[bit].add(node_id)

    # edges: driver node -> loader node, width = number of distinct bits between the pair
    pair_bits: dict[tuple[str, str], int] = defaultdict(int)
    targets: dict[str, set[str]] = defaultdict(set)
    for bit, srcs in drivers.items():
        for src in srcs:
            for dst in loaders.get(bit, ()):
                if src != dst:
                    pair_bits[(src, dst)] += 1
                    targets[src].add(dst)

    mapping = _merge(raw, targets)
    truncated = False
    kept = _order(raw, mapping)
    if len(kept) > MAX_NODES:
        kept = kept[:MAX_NODES]
        truncated = True
    kept_set = set(kept)

    nodes = [
        {
            "id": nid,
            "kind": raw[nid]["kind"],
            "label": raw[nid]["label"] if raw[nid]["kind"] == "port" else _label(raw[nid]),
            "width": raw[nid]["width"],
            "module": top,
        }
        for nid in kept
    ]
    edge_bits: dict[tuple[str, str], int] = defaultdict(int)
    for (src, dst), count in pair_bits.items():
        a, b = mapping.get(src, src), mapping.get(dst, dst)
        if a in kept_set and b in kept_set and a != b:
            edge_bits[(a, b)] = max(edge_bits.get((a, b), 0), count)
    edges = [{"source": a, "target": b, "width": w} for (a, b), w in sorted(edge_bits.items())]

    return {
        "format": FORMAT,
        "candidateId": "",
        "top": top,
        "modules": modules,
        "ports": ports,
        "nodes": nodes,
        "edges": edges,
        "truncated": truncated,
        "stats": stats,
    }


def _label(node: dict[str, Any]) -> str:
    if node["count"] > 1:
        return f"{node['kind']} x{node['count']}"
    return f"{node['label']} {node['width']}b"


def _merge(raw: dict[str, dict[str, Any]], targets: dict[str, set[str]]) -> dict[str, str]:
    """Group cells (never ports) with the same kind, width and target set. Returns node id -> representative id.

    Merging only happens when the graph would otherwise exceed MAX_NODES, so small designs keep one node per cell.
    """
    mapping = {nid: nid for nid in raw}
    if len(raw) <= MAX_NODES:
        return mapping
    groups: dict[tuple[str, int, frozenset[str]], list[str]] = defaultdict(list)
    for nid, node in raw.items():
        if node["kind"] == "port":
            continue
        groups[(node["kind"], node["width"], frozenset(targets.get(nid, ())))].append(nid)
    for members in groups.values():
        if len(members) > 1:
            rep = members[0]
            raw[rep]["count"] = len(members)
            for other in members[1:]:
                mapping[other] = rep
    return mapping


def _order(raw: dict[str, dict[str, Any]], mapping: dict[str, str]) -> list[str]:
    reps = [nid for nid in raw if mapping[nid] == nid]
    return sorted(reps, key=lambda nid: (_KIND_ORDER.get(raw[nid]["kind"], 9), nid))
