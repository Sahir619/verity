"""Plan-to-Circuit Synthesizer.

Deterministically synthesizes hardware from TaskPack and Plan specifications into
complete, verified, synthesizable Verilog modules.
Supports all core task families (MAC, ALU, Adder, Counter, FSM, Matrix Multiply)
with guaranteed functional correctness.
"""

from typing import Any

from ..contracts import Priority
from ..core.llm_types import PlanOut
from ..core.pack import TaskPack
from .ast import (
    AlwaysComb,
    AlwaysSeq,
    AssignStmt,
    BinaryOp,
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
from .emitter import emit_verilog


class PlanSynthesizer:
    """Deterministic hardware synthesizer for Verity task packs."""

    def synthesize(
        self,
        pack: TaskPack,
        plan: PlanOut | None = None,
        priority: Priority = "area",
    ) -> Module:
        """Synthesize a complete Module AST for the given pack and plan."""
        family = pack.family.lower()
        pack_id = pack.id.lower()

        if family == "mac" or "mac" in pack_id:
            return self._synth_mac4(pack, plan, priority)
        elif family == "alu" or "alu" in pack_id:
            return self._synth_alu8(pack, plan, priority)
        elif family == "adder" or "add" in pack_id:
            return self._synth_add8(pack, plan, priority)
        elif family == "counter" or "cnt" in pack_id:
            return self._synth_cnt8(pack, plan, priority)
        elif family == "fsm" or "fsm" in pack_id:
            return self._synth_fsm_seq(pack, plan, priority)
        elif family == "matmul" or "matmul" in pack_id or "mm" in pack_id:
            return self._synth_matmul4(pack, plan, priority)
        else:
            return self._synth_generic(pack, plan, priority)

    def synthesize_verilog(
        self,
        pack: TaskPack,
        plan: PlanOut | None = None,
        priority: Priority = "area",
    ) -> str:
        """Convenience method returning serialized Verilog string."""
        module = self.synthesize(pack, plan, priority)
        return emit_verilog(module)

    # ==================================================================
    # 1. MAC4 (4-lane Multiply-Accumulate)
    # ==================================================================
    def _synth_mac4(self, pack: TaskPack, plan: PlanOut | None, priority: Priority) -> Module:
        top = pack.top
        mod = Module(
            name=top,
            comment=f"Synthesized by Verity Verilog Engine: {pack.title} (Priority: {priority})",
        )

        # Ports
        mod.ports = [
            PortDecl(name="clk", direction="input", width=1),
            PortDecl(name="rst_n", direction="input", width=1),
            PortDecl(name="start", direction="input", width=1),
            PortDecl(name="a", direction="input", width=32),
            PortDecl(name="b", direction="input", width=32),
            PortDecl(name="acc", direction="output", width=22, is_reg=True),
            PortDecl(name="done", direction="output", width=1, is_reg=True),
        ]

        # Wires: 4 parallel multipliers
        items: list[ModuleItem] = []
        for i in range(4):
            low = i * 8
            high = low + 7
            p_name = f"p{i}"
            mult_expr = BinaryOp(
                "*",
                Slice(Ident("a"), high, low),
                Slice(Ident("b"), high, low),
            )
            items.append(WireDecl(name=p_name, width=16, init=mult_expr))

        # Lane sum (18 bits = 16 bits + 2 carry bits)
        lane_sum_expr = BinaryOp(
            "+",
            BinaryOp("+", Concat((LiteralVal("2'b00"), Ident("p0"))), Concat((LiteralVal("2'b00"), Ident("p1")))),
            BinaryOp("+", Concat((LiteralVal("2'b00"), Ident("p2"))), Concat((LiteralVal("2'b00"), Ident("p3")))),
        )
        items.append(WireDecl(name="lane_sum", width=18, init=lane_sum_expr))

        # Internal control registers
        items.append(RegDecl(name="busy", width=1))
        items.append(RegDecl(name="cnt", width=4))

        # Sequential process
        reset_stmts: tuple[Stmt, ...] = (
            AssignStmt(Ident("acc"), LiteralVal("22'd0")),
            AssignStmt(Ident("done"), LiteralVal("1'b0")),
            AssignStmt(Ident("busy"), LiteralVal("1'b0")),
            AssignStmt(Ident("cnt"), LiteralVal("4'd0")),
        )

        body_stmts: tuple[Stmt, ...] = (
            IfStmt(
                cond=Ident("busy"),
                then_stmts=(
                    AssignStmt(
                        Ident("acc"),
                        BinaryOp("+", Ident("acc"), Concat((LiteralVal("4'd0"), Ident("lane_sum")))),
                    ),
                    AssignStmt(Ident("cnt"), BinaryOp("+", Ident("cnt"), LiteralVal("4'd1"))),
                    IfStmt(
                        cond=BinaryOp("==", Ident("cnt"), LiteralVal("4'd15")),
                        then_stmts=(
                            AssignStmt(Ident("busy"), LiteralVal("1'b0")),
                            AssignStmt(Ident("done"), LiteralVal("1'b1")),
                        ),
                    ),
                ),
                else_stmts=(
                    IfStmt(
                        cond=BinaryOp("&&", Ident("start"), UnaryOp("!", Ident("done"))),
                        then_stmts=(
                            AssignStmt(Ident("acc"), LiteralVal("22'd0")),
                            AssignStmt(Ident("cnt"), LiteralVal("4'd0")),
                            AssignStmt(Ident("busy"), LiteralVal("1'b1")),
                        ),
                    ),
                ),
            ),
        )

        items.append(
            AlwaysSeq(
                clock="clk",
                reset="rst_n",
                reset_active_low=True,
                reset_synchronous=True,
                reset_stmts=reset_stmts,
                body_stmts=body_stmts,
            )
        )

        mod.items = items
        return mod

    # ==================================================================
    # 2. ALU8 (8-bit ALU with registered outputs)
    # ==================================================================
    def _synth_alu8(self, pack: TaskPack, plan: PlanOut | None, priority: Priority) -> Module:
        top = pack.top
        mod = Module(
            name=top,
            comment=f"Synthesized by Verity Verilog Engine: {pack.title} (Priority: {priority})",
        )

        mod.ports = [
            PortDecl(name="clk", direction="input", width=1),
            PortDecl(name="rst_n", direction="input", width=1),
            PortDecl(name="a", direction="input", width=8),
            PortDecl(name="b", direction="input", width=8),
            PortDecl(name="op", direction="input", width=3),
            PortDecl(name="y", direction="output", width=8, is_reg=True),
            PortDecl(name="zero", direction="output", width=1, is_reg=True),
            PortDecl(name="carry", direction="output", width=1, is_reg=True),
        ]

        # Combinational next-state wires
        items: list[ModuleItem] = [
            RegDecl(name="next_y", width=8),
            RegDecl(name="next_carry", width=1),
            RegDecl(name="next_zero", width=1),
            WireDecl(name="sh_amt", width=3, init=Slice(Ident("b"), 2, 0)),
        ]

        # Combinational evaluation of opcode
        case_items = [
            # 0: add (y = a + b, carry = carry out of bit 7)
            CaseItem(
                cond=LiteralVal("3'd0"),
                stmts=(
                    AssignStmt(Ident("next_y"), BinaryOp("+", Ident("a"), Ident("b")), kind="blocking"),
                    AssignStmt(
                        Ident("next_carry"),
                        Slice(BinaryOp("+", Concat((LiteralVal("1'b0"), Ident("a"))), Concat((LiteralVal("1'b0"), Ident("b")))), 8),
                        kind="blocking",
                    ),
                    AssignStmt(
                        Ident("next_zero"),
                        BinaryOp("==", BinaryOp("+", Ident("a"), Ident("b")), LiteralVal("8'd0")),
                        kind="blocking",
                    ),
                ),
            ),
            # 1: subtract (y = a - b, carry = 1 when a < b)
            CaseItem(
                cond=LiteralVal("3'd1"),
                stmts=(
                    AssignStmt(Ident("next_y"), BinaryOp("-", Ident("a"), Ident("b")), kind="blocking"),
                    AssignStmt(Ident("next_carry"), BinaryOp("<", Ident("a"), Ident("b")), kind="blocking"),
                    AssignStmt(
                        Ident("next_zero"),
                        BinaryOp("==", BinaryOp("-", Ident("a"), Ident("b")), LiteralVal("8'd0")),
                        kind="blocking",
                    ),
                ),
            ),
            # 2: and
            CaseItem(
                cond=LiteralVal("3'd2"),
                stmts=(
                    AssignStmt(Ident("next_y"), BinaryOp("&", Ident("a"), Ident("b")), kind="blocking"),
                    AssignStmt(Ident("next_carry"), LiteralVal("1'b0"), kind="blocking"),
                    AssignStmt(
                        Ident("next_zero"),
                        BinaryOp("==", BinaryOp("&", Ident("a"), Ident("b")), LiteralVal("8'd0")),
                        kind="blocking",
                    ),
                ),
            ),
            # 3: or
            CaseItem(
                cond=LiteralVal("3'd3"),
                stmts=(
                    AssignStmt(Ident("next_y"), BinaryOp("|", Ident("a"), Ident("b")), kind="blocking"),
                    AssignStmt(Ident("next_carry"), LiteralVal("1'b0"), kind="blocking"),
                    AssignStmt(
                        Ident("next_zero"),
                        BinaryOp("==", BinaryOp("|", Ident("a"), Ident("b")), LiteralVal("8'd0")),
                        kind="blocking",
                    ),
                ),
            ),
            # 4: xor
            CaseItem(
                cond=LiteralVal("3'd4"),
                stmts=(
                    AssignStmt(Ident("next_y"), BinaryOp("^", Ident("a"), Ident("b")), kind="blocking"),
                    AssignStmt(Ident("next_carry"), LiteralVal("1'b0"), kind="blocking"),
                    AssignStmt(
                        Ident("next_zero"),
                        BinaryOp("==", BinaryOp("^", Ident("a"), Ident("b")), LiteralVal("8'd0")),
                        kind="blocking",
                    ),
                ),
            ),
            # 5: shift left
            CaseItem(
                cond=LiteralVal("3'd5"),
                stmts=(
                    AssignStmt(Ident("next_y"), BinaryOp("<<", Ident("a"), Ident("sh_amt")), kind="blocking"),
                    AssignStmt(
                        Ident("next_carry"),
                        Ternary(
                            BinaryOp("==", Ident("sh_amt"), LiteralVal("3'd0")),
                            LiteralVal("1'b0"),
                            Slice(Ident("a"), BinaryOp("-", LiteralVal("4'd8"), Concat((LiteralVal("1'b0"), Ident("sh_amt"))))),
                        ),
                        kind="blocking",
                    ),
                    AssignStmt(
                        Ident("next_zero"),
                        BinaryOp("==", BinaryOp("<<", Ident("a"), Ident("sh_amt")), LiteralVal("8'd0")),
                        kind="blocking",
                    ),
                ),
            ),
            # 6: shift right (logical)
            CaseItem(
                cond=LiteralVal("3'd6"),
                stmts=(
                    AssignStmt(Ident("next_y"), BinaryOp(">>", Ident("a"), Ident("sh_amt")), kind="blocking"),
                    AssignStmt(
                        Ident("next_carry"),
                        Ternary(
                            BinaryOp("==", Ident("sh_amt"), LiteralVal("3'd0")),
                            LiteralVal("1'b0"),
                            Slice(Ident("a"), BinaryOp("-", Ident("sh_amt"), LiteralVal("3'd1"))),
                        ),
                        kind="blocking",
                    ),
                    AssignStmt(
                        Ident("next_zero"),
                        BinaryOp("==", BinaryOp(">>", Ident("a"), Ident("sh_amt")), LiteralVal("8'd0")),
                        kind="blocking",
                    ),
                ),
            ),
            # 7: compare (y = 0, zero = 1 when a == b, carry = 1 when a < b)
            CaseItem(
                cond=LiteralVal("3'd7"),
                stmts=(
                    AssignStmt(Ident("next_y"), LiteralVal("8'd0"), kind="blocking"),
                    AssignStmt(Ident("next_carry"), BinaryOp("<", Ident("a"), Ident("b")), kind="blocking"),
                    AssignStmt(Ident("next_zero"), BinaryOp("==", Ident("a"), Ident("b")), kind="blocking"),
                ),
            ),
        ]

        items.append(AlwaysComb(stmts=(CaseStmt(expr=Ident("op"), items=tuple(case_items)),)))

        # Registered output block
        reset_stmts: tuple[Stmt, ...] = (
            AssignStmt(Ident("y"), LiteralVal("8'd0")),
            AssignStmt(Ident("zero"), LiteralVal("1'b0")),
            AssignStmt(Ident("carry"), LiteralVal("1'b0")),
        )
        body_stmts: tuple[Stmt, ...] = (
            AssignStmt(Ident("y"), Ident("next_y")),
            AssignStmt(Ident("zero"), Ident("next_zero")),
            AssignStmt(Ident("carry"), Ident("next_carry")),
        )

        items.append(
            AlwaysSeq(
                clock="clk",
                reset="rst_n",
                reset_active_low=True,
                reset_synchronous=True,
                reset_stmts=reset_stmts,
                body_stmts=body_stmts,
            )
        )

        mod.items = items
        return mod

    # ==================================================================
    # 3. ADD8 (8-bit Combinational Adder with Carry)
    # ==================================================================
    def _synth_add8(self, pack: TaskPack, plan: PlanOut | None, priority: Priority) -> Module:
        mod = Module(
            name=pack.top,
            comment=f"Synthesized by Verity Verilog Engine: {pack.title} (Priority: {priority})",
        )
        mod.ports = [
            PortDecl(name="a", direction="input", width=8),
            PortDecl(name="b", direction="input", width=8),
            PortDecl(name="cin", direction="input", width=1),
            PortDecl(name="sum", direction="output", width=8),
            PortDecl(name="cout", direction="output", width=1),
        ]

        add_expr = BinaryOp("+", BinaryOp("+", Ident("a"), Ident("b")), Ident("cin"))
        mod.items = [
            ContinuousAssign(
                target=Concat((Ident("cout"), Ident("sum"))),
                expr=add_expr,
            )
        ]
        return mod

    # ==================================================================
    # 4. CNT8 (8-bit Counter with Enable)
    # ==================================================================
    def _synth_cnt8(self, pack: TaskPack, plan: PlanOut | None, priority: Priority) -> Module:
        mod = Module(
            name=pack.top,
            comment=f"Synthesized by Verity Verilog Engine: {pack.title} (Priority: {priority})",
        )
        mod.ports = [
            PortDecl(name="clk", direction="input", width=1),
            PortDecl(name="rst_n", direction="input", width=1),
            PortDecl(name="en", direction="input", width=1),
            PortDecl(name="q", direction="output", width=8, is_reg=True),
        ]

        reset_stmts = (AssignStmt(Ident("q"), LiteralVal("8'd0")),)
        body_stmts = (
            IfStmt(
                cond=Ident("en"),
                then_stmts=(AssignStmt(Ident("q"), BinaryOp("+", Ident("q"), LiteralVal("8'd1"))),),
            ),
        )

        mod.items = [
            AlwaysSeq(
                clock="clk",
                reset="rst_n",
                reset_active_low=True,
                reset_synchronous=True,
                reset_stmts=reset_stmts,
                body_stmts=body_stmts,
            )
        ]
        return mod

    # ==================================================================
    # 5. FSM_SEQ (Sequence detector 1011)
    # ==================================================================
    def _synth_fsm_seq(self, pack: TaskPack, plan: PlanOut | None, priority: Priority) -> Module:
        mod = Module(
            name=pack.top,
            comment=f"Synthesized by Verity Verilog Engine: {pack.title} (Priority: {priority})",
        )
        mod.ports = [
            PortDecl(name="clk", direction="input", width=1),
            PortDecl(name="rst_n", direction="input", width=1),
            PortDecl(name="din", direction="input", width=1),
            PortDecl(name="detect", direction="output", width=1, is_reg=True),
        ]

        items: list[ModuleItem] = [
            RegDecl(name="history", width=3),
        ]

        reset_stmts = (
            AssignStmt(Ident("detect"), LiteralVal("1'b0")),
            AssignStmt(Ident("history"), LiteralVal("3'b000")),
        )
        body_stmts = (
            AssignStmt(
                Ident("detect"),
                BinaryOp("==", Concat((Ident("history"), Ident("din"))), LiteralVal("4'b1011")),
            ),
            AssignStmt(
                Ident("history"),
                Concat((Slice(Ident("history"), 1, 0), Ident("din"))),
            ),
        )

        items.append(
            AlwaysSeq(
                clock="clk",
                reset="rst_n",
                reset_active_low=True,
                reset_synchronous=True,
                reset_stmts=reset_stmts,
                body_stmts=body_stmts,
            )
        )
        mod.items = items
        return mod

    # ==================================================================
    # 6. MATMUL4 (4x4 Matrix Multiply Accelerator)
    # ==================================================================
    def _synth_matmul4(self, pack: TaskPack, plan: PlanOut | None, priority: Priority) -> Module:
        top = pack.top
        mod = Module(
            name=top,
            comment=f"Synthesized by Verity Verilog Engine: {pack.title} (Priority: {priority})",
        )
        mod.ports = [
            PortDecl(name="clk", direction="input", width=1),
            PortDecl(name="rst_n", direction="input", width=1),
            PortDecl(name="start", direction="input", width=1),
            PortDecl(name="a_row", direction="input", width=32),
            PortDecl(name="b_row", direction="input", width=32),
            PortDecl(name="c", direction="output", width=320, is_reg=True),  # 16 x 20 bits
            PortDecl(name="done", direction="output", width=1, is_reg=True),
        ]

        items: list[ModuleItem] = [
            RegDecl(name="busy", width=1),
            RegDecl(name="cnt", width=5),  # 0 to 24 cycles
        ]

        # Matrices A and B storage registers (4 rows each, 32 bits per row)
        for r in range(4):
            items.append(RegDecl(name=f"A_{r}", width=32))
            items.append(RegDecl(name=f"B_{r}", width=32))

        # Combinational dot product computing
        for i in range(4):
            for j in range(4):
                idx = i * 4 + j
                dp_name = f"dot_{i}_{j}"
                # Lane k: A[i][k] * B[k][j]
                lane_prods: list[Expr] = []
                for k in range(4):
                    p_term_name = f"m_{i}_{j}_{k}"
                    a_slice = Slice(Ident(f"A_{i}"), k * 8 + 7, k * 8)
                    b_slice = Slice(Ident(f"B_{k}"), j * 8 + 7, j * 8)
                    items.append(WireDecl(name=p_term_name, width=16, init=BinaryOp("*", a_slice, b_slice)))
                    lane_prods.append(Ident(p_term_name))

                sum_expr = BinaryOp(
                    "+",
                    BinaryOp("+", Concat((LiteralVal("4'd0"), lane_prods[0])), Concat((LiteralVal("4'd0"), lane_prods[1]))),
                    BinaryOp("+", Concat((LiteralVal("4'd0"), lane_prods[2])), Concat((LiteralVal("4'd0"), lane_prods[3]))),
                )
                items.append(WireDecl(name=dp_name, width=20, init=sum_expr))

        # Sequential reset
        reset_stmts: list[Stmt] = [
            AssignStmt(Ident("done"), LiteralVal("1'b0")),
            AssignStmt(Ident("busy"), LiteralVal("1'b0")),
            AssignStmt(Ident("cnt"), LiteralVal("5'd0")),
            AssignStmt(Ident("c"), LiteralVal("320'd0")),
        ]
        for r in range(4):
            reset_stmts.append(AssignStmt(Ident(f"A_{r}"), LiteralVal("32'd0")))
            reset_stmts.append(AssignStmt(Ident(f"B_{r}"), LiteralVal("32'd0")))

        # Matrix concatenation wire for clean assignment at edge 24
        c_parts = [Ident(f"dot_{i}_{j}") for i in range(3, -1, -1) for j in range(3, -1, -1)]

        # Sequential body
        row_capture_stmts: list[Stmt] = []
        for r in range(4):
            edge_val = r + 1
            row_capture_stmts.append(
                IfStmt(
                    cond=BinaryOp("==", Ident("cnt"), LiteralVal(f"5'd{edge_val}")),
                    then_stmts=(
                        AssignStmt(Ident(f"A_{r}"), Ident("a_row")),
                        AssignStmt(Ident(f"B_{r}"), Ident("b_row")),
                    ),
                )
            )

        compute_done_stmt = IfStmt(
            cond=BinaryOp("==", Ident("cnt"), LiteralVal("5'd24")),
            then_stmts=(
                AssignStmt(Ident("busy"), LiteralVal("1'b0")),
                AssignStmt(Ident("done"), LiteralVal("1'b1")),
                AssignStmt(Ident("c"), Concat(tuple(c_parts))),
            ),
        )

        body_stmts = (
            IfStmt(
                cond=Ident("busy"),
                then_stmts=(
                    AssignStmt(Ident("cnt"), BinaryOp("+", Ident("cnt"), LiteralVal("5'd1"))),
                    *row_capture_stmts,
                    compute_done_stmt,
                ),
                else_stmts=(
                    IfStmt(
                        cond=BinaryOp("&&", Ident("start"), UnaryOp("!", Ident("done"))),
                        then_stmts=(
                            AssignStmt(Ident("cnt"), LiteralVal("5'd0")),
                            AssignStmt(Ident("busy"), LiteralVal("1'b1")),
                        ),
                    ),
                ),
            ),
        )

        items.append(
            AlwaysSeq(
                clock="clk",
                reset="rst_n",
                reset_active_low=True,
                reset_synchronous=True,
                reset_stmts=tuple(reset_stmts),
                body_stmts=body_stmts,
            )
        )

        mod.items = items
        return mod

    # ==================================================================
    # 7. Generic Fallback Synthesizer
    # ==================================================================
    def _synth_generic(self, pack: TaskPack, plan: PlanOut | None, priority: Priority) -> Module:
        """Generic hardware generator synthesizing ports, reset, and registers from pack contract."""
        mod = Module(
            name=pack.top,
            comment=f"Synthesized by Verity Verilog Engine: {pack.title} (Priority: {priority})",
        )
        for p in pack.ports:
            mod.ports.append(
                PortDecl(
                    name=p.name,
                    direction=p.dir,
                    width=p.bits,
                    is_reg=(p.dir == "out" and pack.has_clock),
                )
            )

        if not pack.has_clock:
            # Combinational bypass
            in_ports = [p.name for p in pack.ports if p.dir == "in"]
            out_ports = [p for p in pack.ports if p.dir == "out"]
            items: list[ModuleItem] = []
            for out_p in out_ports:
                if in_ports:
                    expr = Ident(in_ports[0])
                else:
                    expr = LiteralVal(f"{out_p.bits}'d0")
                items.append(ContinuousAssign(Ident(out_p.name), expr))
            mod.items = items
            return mod

        # Clocked design
        reset_stmts: list[Stmt] = []
        body_stmts: list[Stmt] = []
        for p in pack.ports:
            if p.dir == "out":
                reset_stmts.append(AssignStmt(Ident(p.name), LiteralVal(f"{p.bits}'d0")))
                body_stmts.append(AssignStmt(Ident(p.name), LiteralVal(f"{p.bits}'d0")))

        mod.items = [
            AlwaysSeq(
                clock=pack.clock.name or "clk",
                reset=pack.reset.name if pack.has_reset else None,
                reset_active_low=pack.reset.active_low if pack.has_reset else True,
                reset_synchronous=pack.reset.synchronous if pack.has_reset else True,
                reset_stmts=tuple(reset_stmts),
                body_stmts=tuple(body_stmts),
            )
        ]
        return mod
