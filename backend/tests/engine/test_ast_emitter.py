"""Tests for Hardware AST and Verilog-2005 Emitter."""

from verity.engine.ast import (
    AlwaysComb,
    AlwaysSeq,
    AssignStmt,
    BinaryOp,
    CaseItem,
    CaseStmt,
    Concat,
    ContinuousAssign,
    Ident,
    IfStmt,
    IndexedSlice,
    LiteralVal,
    Module,
    PortDecl,
    RegDecl,
    Slice,
    Ternary,
    UnaryOp,
    WireDecl,
)


def test_basic_module_emission() -> None:
    mod = Module(
        name="test_mod",
        ports=[
            PortDecl(name="clk", direction="input", width=1),
            PortDecl(name="rst_n", direction="input", width=1),
            PortDecl(name="din", direction="input", width=8),
            PortDecl(name="dout", direction="output", width=8, is_reg=True),
        ],
        comment="Test module for emitter verification",
    )
    mod.items.append(
        AlwaysSeq(
            clock="clk",
            reset="rst_n",
            reset_active_low=True,
            reset_synchronous=True,
            reset_stmts=(AssignStmt(Ident("dout"), LiteralVal("8'd0")),),
            body_stmts=(AssignStmt(Ident("dout"), Ident("din")),),
        )
    )

    from verity.engine.emitter import emit_verilog
    code = emit_verilog(mod)

    assert "module test_mod (" in code
    assert "input clk" in code or "input  clk" in code or "input clk," in code
    assert "output reg [7:0] dout" in code
    assert "always @(posedge clk) begin" in code
    assert "if (!rst_n) begin" in code
    assert "dout <= 8'd0;" in code
    assert "dout <= din;" in code
    assert "endmodule" in code


def test_expressions_and_continuous_assign() -> None:
    mod = Module(name="expr_mod")
    mod.ports = [
        PortDecl(name="a", direction="input", width=8),
        PortDecl(name="b", direction="input", width=8),
        PortDecl(name="sel", direction="input", width=1),
        PortDecl(name="res", direction="output", width=8),
    ]

    # assign res = sel ? (a + b) : {a[3:0], b[7:4]};
    add_expr = BinaryOp("+", Ident("a"), Ident("b"))
    slice_a = Slice(Ident("a"), 3, 0)
    slice_b = Slice(Ident("b"), 7, 4)
    concat_expr = Concat((slice_a, slice_b))
    ternary_expr = Ternary(cond=Ident("sel"), true_expr=add_expr, false_expr=concat_expr)

    mod.items.append(ContinuousAssign(target=Ident("res"), expr=ternary_expr))

    from verity.engine.emitter import emit_verilog
    code = emit_verilog(mod)

    assert "assign res = (sel ? (a + b) : {a[3:0], b[7:4]});" in code


def test_case_statement_in_combinational_block() -> None:
    mod = Module(name="case_mod")
    mod.ports = [
        PortDecl(name="sel", direction="input", width=2),
        PortDecl(name="y", direction="output", width=4, is_reg=True),
    ]
    mod.items.append(
        AlwaysComb(
            stmts=(
                CaseStmt(
                    expr=Ident("sel"),
                    items=(
                        CaseItem(cond=LiteralVal("2'd0"), stmts=(AssignStmt(Ident("y"), LiteralVal("4'd1"), kind="blocking"),)),
                        CaseItem(cond=LiteralVal("2'd1"), stmts=(AssignStmt(Ident("y"), LiteralVal("4'd2"), kind="blocking"),)),
                        CaseItem(cond="default", stmts=(AssignStmt(Ident("y"), LiteralVal("4'd0"), kind="blocking"),)),
                    ),
                ),
            )
        )
    )

    from verity.engine.emitter import emit_verilog
    code = emit_verilog(mod)

    assert "always @(*) begin" in code
    assert "case (sel)" in code
    assert "2'd0: begin" in code
    assert "y = 4'd1;" in code
    assert "default: begin" in code
    assert "endcase" in code
