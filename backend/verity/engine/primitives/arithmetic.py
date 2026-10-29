"""Parametric arithmetic hardware primitives.

Generates synthesizable arithmetic blocks:
- Vector multi-lane multipliers (signed/unsigned, optional pipeline registers)
- Adder reduction trees (balanced binary tree or cascade)
- Accumulators with enable, clear, and overflow management
- ALUs supporting arithmetic and logic functions
"""

from typing import Sequence

from ..ast import (
    AssignStmt,
    BinaryOp,
    Expr,
    Ident,
    IfStmt,
    IndexedSlice,
    LiteralVal,
    ModuleItem,
    Slice,
    UnaryOp,
    WireDecl,
)


def unpack_lane(vector_name: str, lane_idx: int, lane_width: int) -> IndexedSlice:
    """Indexed slice representing vector_name[lane_idx * width +: width]."""
    return IndexedSlice(
        expr=Ident(vector_name),
        base=str(lane_idx * lane_width),
        width=lane_width,
    )


def build_parallel_multipliers(
    a_name: str,
    b_name: str,
    lane_count: int,
    lane_width: int,
    signed: bool = False,
    product_prefix: str = "p",
) -> tuple[list[ModuleItem], list[Ident]]:
    """Generates lane_count parallel multipliers.

    Returns wire declarations and list of product signal identifiers.
    Product width = 2 * lane_width.
    """
    items: list[ModuleItem] = []
    product_signals: list[Ident] = []
    product_width = 2 * lane_width

    for i in range(lane_count):
        p_name = f"{product_prefix}{i}"
        a_lane = unpack_lane(a_name, i, lane_width)
        b_lane = unpack_lane(b_name, i, lane_width)

        if signed:
            a_expr: Expr = UnaryOp("$signed", a_lane)
            b_expr: Expr = UnaryOp("$signed", b_lane)
        else:
            a_expr = a_lane
            b_expr = b_lane

        mult_expr = BinaryOp("*", a_expr, b_expr)
        items.append(WireDecl(name=p_name, width=product_width, signed=signed, init=mult_expr))
        product_signals.append(Ident(p_name))

    return items, product_signals


def build_balanced_adder_tree(
    inputs: Sequence[Expr],
    input_width: int,
    signed: bool = False,
    prefix: str = "sum_stage",
) -> tuple[list[ModuleItem], Expr]:
    """Generates a balanced binary adder tree summing all inputs.

    Prevents overflow by widening adder stages by 1 bit per tree level.
    Returns generated wire declarations and the final sum expression.
    """
    if not inputs:
        return [], LiteralVal(0, width=input_width, signed=signed)
    if len(inputs) == 1:
        return [], inputs[0]

    items: list[ModuleItem] = []
    current_level = list(inputs)
    current_width = input_width
    stage_idx = 0

    while len(current_level) > 1:
        next_level: list[Expr] = []
        next_width = current_width + 1

        for i in range(0, len(current_level), 2):
            if i + 1 < len(current_level):
                wire_name = f"{prefix}_{stage_idx}_{len(next_level)}"
                left = current_level[i]
                right = current_level[i + 1]
                add_expr = BinaryOp("+", left, right)
                items.append(WireDecl(name=wire_name, width=next_width, signed=signed, init=add_expr))
                next_level.append(Ident(wire_name))
            else:
                # Odd one out passes to next stage
                next_level.append(current_level[i])

        current_level = next_level
        current_width = next_width
        stage_idx += 1

    return items, current_level[0]
