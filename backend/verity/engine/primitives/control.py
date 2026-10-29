"""Control and state machine hardware primitives.

Generates synthesizable control logic:
- Cycle counters matching exact pack latency_cycles
- FSM controllers (IDLE, RUNNING, DONE) with clean reset and start protocol handling
"""

import math
from typing import Sequence

from ..ast import (
    AssignStmt,
    BinaryOp,
    CaseItem,
    CaseStmt,
    Expr,
    Ident,
    IfStmt,
    LiteralVal,
    ModuleItem,
    RegDecl,
    Stmt,
    WireDecl,
)


def bits_needed(n: int) -> int:
    """Calculates minimum bit width needed to represent unsigned integer n."""
    if n <= 1:
        return 1
    return max(1, math.ceil(math.log2(n + 1)))


def build_cycle_counter(
    target_cycles: int,
    counter_name: str = "cnt",
) -> tuple[RegDecl, int, Stmt, Stmt, Expr]:
    """Generates a cycle counter for counting 0 to target_cycles - 1.

    Returns:
    - RegDecl for the counter
    - counter width
    - reset statement (cnt <= 0)
    - increment statement (cnt <= cnt + 1)
    - terminal condition expression (cnt == target_cycles - 1)
    """
    width = bits_needed(max(1, target_cycles - 1))
    reg = RegDecl(name=counter_name, width=width)
    reset_stmt = AssignStmt(Ident(counter_name), LiteralVal(0, width=width))
    inc_stmt = AssignStmt(Ident(counter_name), BinaryOp("+", Ident(counter_name), LiteralVal(1, width=width)))
    terminal_cond = BinaryOp("==", Ident(counter_name), LiteralVal(target_cycles - 1, width=width))
    return reg, width, reset_stmt, inc_stmt, terminal_cond
