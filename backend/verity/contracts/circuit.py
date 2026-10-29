"""CircuitGraph (backend/ARCHITECTURE 16). Delivered as an `artifact` event at path circuit/<candidateId>.json.

Produced inside the sandbox by /verity/bin/measure (with candidateId ""), then the backend sets candidateId
and validates it with this model before emitting.
"""

from typing import Literal

from pydantic import Field

from .base import WireModel, opt

CircuitNodeKind = Literal["port", "reg", "add", "mul", "mux", "cmp", "logic", "submodule"]
MAX_CIRCUIT_NODES = 60


class CircuitModule(WireModel):
    name: str
    instance_of: str | None = opt()


class CircuitPort(WireModel):
    name: str
    dir: Literal["in", "out"]
    width: int
    signed: bool = False


class CircuitNode(WireModel):
    id: str
    kind: CircuitNodeKind
    label: str
    width: int
    module: str


class CircuitEdge(WireModel):
    source: str
    target: str
    width: int


class CircuitStats(WireModel):
    regs: int = 0
    adders: int = 0
    multipliers: int = 0
    muxes: int = 0


class CircuitGraph(WireModel):
    format: Literal["verity.circuit/1"] = "verity.circuit/1"
    candidate_id: str = ""
    top: str
    modules: list[CircuitModule] = Field(default_factory=list)
    ports: list[CircuitPort] = Field(default_factory=list)
    nodes: list[CircuitNode] = Field(default_factory=list, max_length=MAX_CIRCUIT_NODES)
    edges: list[CircuitEdge] = Field(default_factory=list)
    truncated: bool = False
    stats: CircuitStats = Field(default_factory=CircuitStats)
