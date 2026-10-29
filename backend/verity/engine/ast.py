"""Hardware Abstract Syntax Tree (Hardware AST) for synthesizable Verilog-2005.

Models digital circuits as typed, structural objects:
- Declarations: Ports, Wires, Regs, Module headers
- Expressions: Identifiers, Bit-slices, Vector slices, Arithmetic/Logic ops, Concatenations, Replications, Ternaries, Literals
- Statements: Blocking (=) and Non-blocking (<=) assignments, If-Else branches, Case statements
- Procedural Blocks: Sequential always @(posedge clk) with clean reset, Combinational always @(*)
- Continuous Assignments: assign wire = expr;
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal, Sequence, Union

PortDir = Literal["input", "output", "inout"]
AssignKind = Literal["non_blocking", "blocking"]


# ======================================================================
# Expressions
# ======================================================================

class Expr(ABC):
    """Base class for all hardware expressions."""
    pass


@dataclass(frozen=True, slots=True)
class Ident(Expr):
    """Identifier (signal, port, or wire name)."""
    name: str


@dataclass(frozen=True, slots=True)
class LiteralVal(Expr):
    """Sized or unsized Verilog numeric literal (e.g. 22'd0, 1'b1, 16'hFFFF, or 0)."""
    val: str | int
    width: int | None = None
    signed: bool = False

    def to_verilog(self) -> str:
        if isinstance(self.val, str):
            return self.val
        if self.width is not None:
            prefix = f"{self.width}'sd" if self.signed else f"{self.width}'d"
            return f"{prefix}{self.val}"
        return str(self.val)


@dataclass(frozen=True, slots=True)
class Slice(Expr):
    """Bit or range slice: signal[high:low] or signal[idx]."""
    expr: Expr
    high: int | str
    low: int | str | None = None

    def to_verilog(self) -> str:
        if self.low is None:
            return f"[{self.high}]"
        return f"[{self.high}:{self.low}]"


@dataclass(frozen=True, slots=True)
class IndexedSlice(Expr):
    """Indexed vector slice: signal[base +: width]."""
    expr: Expr
    base: Expr | str
    width: int

    def to_verilog(self) -> str:
        base_str = self.base if isinstance(self.base, str) else _emit_expr(self.base)
        return f"[{base_str} +: {self.width}]"


@dataclass(frozen=True, slots=True)
class BinaryOp(Expr):
    """Binary operator expression: left op right."""
    op: str  # +, -, *, &, |, ^, <<, >>, ==, !=, <, <=, >, >=, &&, ||
    left: Expr
    right: Expr


@dataclass(frozen=True, slots=True)
class UnaryOp(Expr):
    """Unary operator expression: op expr (~, !, -, $signed, $unsigned)."""
    op: str
    expr: Expr


@dataclass(frozen=True, slots=True)
class Concat(Expr):
    """Concatenation: {a, b, c}."""
    parts: tuple[Expr, ...]


@dataclass(frozen=True, slots=True)
class Replicate(Expr):
    """Replication: {count{expr}}."""
    count: int
    expr: Expr


@dataclass(frozen=True, slots=True)
class Ternary(Expr):
    """Conditional expression: cond ? true_expr : false_expr."""
    cond: Expr
    true_expr: Expr
    false_expr: Expr


def _emit_expr(e: Expr) -> str:
    """Internal helper to convert expressions to string."""
    if isinstance(e, Ident):
        return e.name
    if isinstance(e, LiteralVal):
        return e.to_verilog()
    if isinstance(e, Slice):
        base = _emit_expr(e.expr)
        return f"{base}{e.to_verilog()}"
    if isinstance(e, IndexedSlice):
        base = _emit_expr(e.expr)
        return f"{base}{e.to_verilog()}"
    if isinstance(e, BinaryOp):
        return f"({_emit_expr(e.left)} {e.op} {_emit_expr(e.right)})"
    if isinstance(e, UnaryOp):
        if e.op.startswith("$"):
            return f"{e.op}({_emit_expr(e.expr)})"
        return f"{e.op}({_emit_expr(e.expr)})"
    if isinstance(e, Concat):
        items = ", ".join(_emit_expr(p) for p in e.parts)
        return f"{{{items}}}"
    if isinstance(e, Replicate):
        return f"{{{e.count}{{{_emit_expr(e.expr)}}}}}"
    if isinstance(e, Ternary):
        return f"({_emit_expr(e.cond)} ? {_emit_expr(e.true_expr)} : {_emit_expr(e.false_expr)})"
    raise TypeError(f"Unknown Expr type: {type(e)}")


# ======================================================================
# Statements
# ======================================================================

class Stmt(ABC):
    """Base class for behavioral statements inside procedural blocks."""
    pass


@dataclass(frozen=True, slots=True)
class AssignStmt(Stmt):
    """Assignment statement: target <= expr; (non_blocking) or target = expr; (blocking)."""
    target: Expr
    expr: Expr
    kind: AssignKind = "non_blocking"


@dataclass(frozen=True, slots=True)
class IfStmt(Stmt):
    """If-Else conditional branch."""
    cond: Expr
    then_stmts: tuple[Stmt, ...]
    else_stmts: tuple[Stmt, ...] = ()


@dataclass(frozen=True, slots=True)
class CaseItem:
    """Single branch in a case statement: val: begin ... end."""
    cond: Expr | str  # str "default" or LiteralVal
    stmts: tuple[Stmt, ...]


@dataclass(frozen=True, slots=True)
class CaseStmt(Stmt):
    """Case statement: case (expr) ... endcase."""
    expr: Expr
    items: tuple[CaseItem, ...]


# ======================================================================
# Module Declarations and Top-Level Items
# ======================================================================

@dataclass(frozen=True, slots=True)
class PortDecl:
    """Module port declaration."""
    name: str
    direction: PortDir
    width: int = 1
    signed: bool = False
    is_reg: bool = False

    @property
    def range_str(self) -> str:
        if self.width <= 1:
            return ""
        return f"[{self.width - 1}:0] "


class ModuleItem(ABC):
    """Top-level item inside a module."""
    pass


@dataclass(frozen=True, slots=True)
class WireDecl(ModuleItem):
    """Wire declaration: wire [W-1:0] name; or wire [W-1:0] name = init;"""
    name: str
    width: int = 1
    signed: bool = False
    init: Expr | None = None


@dataclass(frozen=True, slots=True)
class RegDecl(ModuleItem):
    """Reg declaration: reg [W-1:0] name;"""
    name: str
    width: int = 1
    signed: bool = False


@dataclass(frozen=True, slots=True)
class ContinuousAssign(ModuleItem):
    """Continuous assignment: assign target = expr;"""
    target: Expr
    expr: Expr


@dataclass(frozen=True, slots=True)
class AlwaysSeq(ModuleItem):
    """Sequential procedural block: always @(posedge clk)."""
    clock: str = "clk"
    reset: str | None = "rst_n"
    reset_active_low: bool = True
    reset_synchronous: bool = True
    reset_stmts: tuple[Stmt, ...] = ()
    body_stmts: tuple[Stmt, ...] = ()


@dataclass(frozen=True, slots=True)
class AlwaysComb(ModuleItem):
    """Combinational procedural block: always @(*)."""
    stmts: tuple[Stmt, ...] = ()


@dataclass(frozen=True, slots=True)
class RawVerilogItem(ModuleItem):
    """Inline raw Verilog comment or statement."""
    code: str


@dataclass(slots=True)
class Module:
    """Complete synthesizable Verilog module."""
    name: str
    ports: list[PortDecl] = field(default_factory=list)
    items: list[ModuleItem] = field(default_factory=list)
    comment: str = ""
