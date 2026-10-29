"""Verity Verilog & Chip Generation Engine.

Deterministic, AST-based hardware synthesizer and circuit generator:
- ast: Hardware Abstract Syntax Tree for synthesizable Verilog-2005
- emitter: Verilog-2005 serializer adhering to locked coding rules
- primitives: Arithmetic, control, state machine, and datapath building blocks
- synthesizer: Plan-to-circuit synthesizer mapping task packs and plans to verified RTL
- optimizer: Deterministic structural optimizer for area and speed
"""

from .ast import (
    AlwaysComb,
    AlwaysSeq,
    AssignStmt,
    CaseItem,
    CaseStmt,
    Concat,
    ContinuousAssign,
    Expr,
    Ident,
    IfStmt,
    IndexedSlice,
    LiteralVal,
    Module,
    ModuleItem,
    PortDecl,
    RegDecl,
    Slice,
    Stmt,
    Ternary,
    UnaryOp,
    WireDecl,
)
from .emitter import VerilogEmitter, emit_verilog
from .optimizer import CircuitOptimizer, OptimizationResult
from .synthesizer import PlanSynthesizer

__all__ = [
    "Module",
    "PortDecl",
    "WireDecl",
    "RegDecl",
    "AlwaysSeq",
    "AlwaysComb",
    "ContinuousAssign",
    "AssignStmt",
    "IfStmt",
    "CaseStmt",
    "CaseItem",
    "Expr",
    "Ident",
    "LiteralVal",
    "Slice",
    "IndexedSlice",
    "Concat",
    "Ternary",
    "UnaryOp",
    "Stmt",
    "ModuleItem",
    "VerilogEmitter",
    "emit_verilog",
    "PlanSynthesizer",
    "CircuitOptimizer",
    "OptimizationResult",
]
