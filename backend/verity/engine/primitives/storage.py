"""Storage and datapath routing hardware primitives.

Generates synthesizable registers and routing:
- Pipeline registers for retiming critical paths
- Multiplexers and bypass gates
"""

from ..ast import (
    AssignStmt,
    BinaryOp,
    Expr,
    Ident,
    IfStmt,
    LiteralVal,
    ModuleItem,
    RegDecl,
    Stmt,
    Ternary,
    WireDecl,
)


def build_pipeline_stage(
    stage_name: str,
    width: int,
    data_in: Expr,
    enable: Expr | None = None,
    signed: bool = False,
) -> tuple[RegDecl, Stmt, Stmt, Ident]:
    """Generates a pipeline register stage.

    Returns:
    - RegDecl for the register
    - Reset statement (reg <= 0)
    - Update statement (reg <= data_in)
    - Output identifier
    """
    reg = RegDecl(name=stage_name, width=width, signed=signed)
    reset_stmt = AssignStmt(Ident(stage_name), LiteralVal(0, width=width, signed=signed))

    if enable is not None:
        update_stmt: Stmt = IfStmt(
            cond=enable,
            then_stmts=(AssignStmt(Ident(stage_name), data_in),),
        )
    else:
        update_stmt = AssignStmt(Ident(stage_name), data_in)

    return reg, reset_stmt, update_stmt, Ident(stage_name)


def build_mux2(
    sel: Expr,
    in0: Expr,
    in1: Expr,
) -> Ternary:
    """Generates a 2-to-1 multiplexer expression: sel ? in1 : in0."""
    return Ternary(cond=sel, true_expr=in1, false_expr=in0)
