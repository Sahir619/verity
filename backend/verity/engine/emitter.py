"""Verilog-2005 code emitter.

Serializes the Hardware AST into clean, strictly synthesizable Verilog-2005 code.
Adheres to Verity coding rules:
- ANSI C port list style.
- Pure synthesizable constructs (no initial blocks, no latches, rising clock only).
- Proper non-blocking (<=) assignments inside sequential blocks.
- Proper blocking (=) assignments inside combinational blocks.
- 4-space indentation and clean alignment.
"""

from .ast import (
    AlwaysComb,
    AlwaysSeq,
    AssignStmt,
    CaseItem,
    CaseStmt,
    ContinuousAssign,
    Expr,
    Ident,
    IfStmt,
    Module,
    ModuleItem,
    PortDecl,
    RawVerilogItem,
    RegDecl,
    Stmt,
    WireDecl,
    _emit_expr,
)


class VerilogEmitter:
    """Serializes a Module AST into formatted Verilog-2005 text."""

    def __init__(self, indent_spaces: int = 4) -> None:
        self.indent_str = " " * indent_spaces

    def emit(self, module: Module) -> str:
        lines: list[str] = []

        if module.comment:
            for c_line in module.comment.strip().splitlines():
                lines.append(f"// {c_line}")

        # Module header and port list
        lines.append(f"module {module.name} (")
        port_lines: list[str] = []
        for p in module.ports:
            dir_str = p.direction
            reg_str = " reg" if p.is_reg else ""
            signed_str = " signed" if p.signed else ""
            range_str = f" {p.range_str}".rstrip() if p.width > 1 else ""
            port_lines.append(f"{self.indent_str}{dir_str}{reg_str}{signed_str}{range_str} {p.name}")

        lines.append(",\n".join(port_lines))
        lines.append(");")
        lines.append("")

        # Separate items by kind for clean readable layout
        wires: list[WireDecl] = []
        regs: list[RegDecl] = []
        assigns: list[ContinuousAssign] = []
        procs: list[ModuleItem] = []

        for item in module.items:
            if isinstance(item, WireDecl):
                wires.append(item)
            elif isinstance(item, RegDecl):
                regs.append(item)
            elif isinstance(item, ContinuousAssign):
                assigns.append(item)
            else:
                procs.append(item)

        # Regs
        if regs:
            for r in regs:
                signed_str = " signed" if r.signed else ""
                range_str = f" [{r.width - 1}:0]" if r.width > 1 else ""
                lines.append(f"{self.indent_str}reg{signed_str}{range_str} {r.name};")
            lines.append("")

        # Wires
        if wires:
            for w in wires:
                signed_str = " signed" if w.signed else ""
                range_str = f" [{w.width - 1}:0]" if w.width > 1 else ""
                init_str = f" = {_emit_expr(w.init)}" if w.init is not None else ""
                lines.append(f"{self.indent_str}wire{signed_str}{range_str} {w.name}{init_str};")
            lines.append("")

        # Continuous assigns
        if assigns:
            for a in assigns:
                lines.append(f"{self.indent_str}assign {_emit_expr(a.target)} = {_emit_expr(a.expr)};")
            lines.append("")

        # Procedural blocks & inline items
        for p in procs:
            if isinstance(p, AlwaysSeq):
                lines.extend(self._emit_always_seq(p))
                lines.append("")
            elif isinstance(p, AlwaysComb):
                lines.extend(self._emit_always_comb(p))
                lines.append("")
            elif isinstance(p, RawVerilogItem):
                lines.append(p.code)
                lines.append("")

        # Strip trailing blank lines
        while lines and not lines[-1].strip():
            lines.pop()

        lines.append("endmodule")
        lines.append("")
        return "\n".join(lines)

    def _emit_always_seq(self, seq: AlwaysSeq) -> list[str]:
        lines: list[str] = []
        # Sensitivity list
        sens = f"posedge {seq.clock}"
        if seq.reset and not seq.reset_synchronous:
            edge = "negedge" if seq.reset_active_low else "posedge"
            sens += f" or {edge} {seq.reset}"

        lines.append(f"{self.indent_str}always @({sens}) begin")

        # Reset block
        if seq.reset:
            rst_cond = f"!{seq.reset}" if seq.reset_active_low else f"{seq.reset}"
            lines.append(f"{self.indent_str * 2}if ({rst_cond}) begin")
            for s in seq.reset_stmts:
                lines.extend(self._emit_stmt(s, level=3))

            if seq.body_stmts:
                lines.append(f"{self.indent_str * 2}end else begin")
                for s in seq.body_stmts:
                    lines.extend(self._emit_stmt(s, level=3))
            lines.append(f"{self.indent_str * 2}end")
        else:
            for s in seq.body_stmts:
                lines.extend(self._emit_stmt(s, level=2))

        lines.append(f"{self.indent_str}end")
        return lines

    def _emit_always_comb(self, comb: AlwaysComb) -> list[str]:
        lines: list[str] = [f"{self.indent_str}always @(*) begin"]
        for s in comb.stmts:
            lines.extend(self._emit_stmt(s, level=2))
        lines.append(f"{self.indent_str}end")
        return lines

    def _emit_stmt(self, stmt: Stmt, level: int) -> list[str]:
        ind = self.indent_str * level
        lines: list[str] = []

        if isinstance(stmt, AssignStmt):
            op = "<=" if stmt.kind == "non_blocking" else "="
            lines.append(f"{ind}{_emit_expr(stmt.target)} {op} {_emit_expr(stmt.expr)};")
        elif isinstance(stmt, IfStmt):
            lines.append(f"{ind}if ({_emit_expr(stmt.cond)}) begin")
            for sub in stmt.then_stmts:
                lines.extend(self._emit_stmt(sub, level + 1))
            if stmt.else_stmts:
                # Check for single nested IfStmt for clean else if
                if len(stmt.else_stmts) == 1 and isinstance(stmt.else_stmts[0], IfStmt):
                    nested_if = stmt.else_stmts[0]
                    lines.append(f"{ind}end else if ({_emit_expr(nested_if.cond)}) begin")
                    for sub in nested_if.then_stmts:
                        lines.extend(self._emit_stmt(sub, level + 1))
                    if nested_if.else_stmts:
                        lines.append(f"{ind}end else begin")
                        for sub in nested_if.else_stmts:
                            lines.extend(self._emit_stmt(sub, level + 1))
                else:
                    lines.append(f"{ind}end else begin")
                    for sub in stmt.else_stmts:
                        lines.extend(self._emit_stmt(sub, level + 1))
            lines.append(f"{ind}end")
        elif isinstance(stmt, CaseStmt):
            lines.append(f"{ind}case ({_emit_expr(stmt.expr)})")
            for item in stmt.items:
                cond_str = item.cond if isinstance(item.cond, str) else _emit_expr(item.cond)
                lines.append(f"{ind}{self.indent_str}{cond_str}: begin")
                for sub in item.stmts:
                    lines.extend(self._emit_stmt(sub, level + 2))
                lines.append(f"{ind}{self.indent_str}end")
            lines.append(f"{ind}endcase")
        return lines


def emit_verilog(module: Module) -> str:
    """Convenience helper to emit synthesizable Verilog code for a Module."""
    return VerilogEmitter().emit(module)
