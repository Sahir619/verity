"""ToolRunner.rvfi and the riscv-formal result parser.

W8 owns the rest of this package. Importing this module registers SCRIPT_PATHS["rvfi"] and
SandboxRunner.rvfi without editing those files. The sandbox script is /verity/bin/rvfi.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from ..core.pack import TaskPack
from ..core.results import _Result
from ..core.rtl import Rtl

SCHEMA = "verity.rvfi/1"
CHECK_TIMEOUT_S = 180
JOBS = 2
# insn (37) plus reg, pc_fwd, pc_bwd, unique, causal, ill and cover.
ESTIMATED_CHECKS = 48

RVFI_PORTS: tuple[tuple[str, int], ...] = (
    ("rvfi_valid", 1),
    ("rvfi_order", 64),
    ("rvfi_insn", 32),
    ("rvfi_trap", 1),
    ("rvfi_halt", 1),
    ("rvfi_intr", 1),
    ("rvfi_mode", 2),
    ("rvfi_ixl", 2),
    ("rvfi_rs1_addr", 5),
    ("rvfi_rs2_addr", 5),
    ("rvfi_rs1_rdata", 32),
    ("rvfi_rs2_rdata", 32),
    ("rvfi_rd_addr", 5),
    ("rvfi_rd_wdata", 32),
    ("rvfi_pc_rdata", 32),
    ("rvfi_pc_wdata", 32),
    ("rvfi_mem_addr", 32),
    ("rvfi_mem_rmask", 4),
    ("rvfi_mem_wmask", 4),
    ("rvfi_mem_rdata", 32),
    ("rvfi_mem_wdata", 32),
)

CheckStatus = Literal["pass", "fail", "timeout", "error"]
RvfiStatus = Literal["ok", "fail", "timeout", "error"]


class RvfiCheck(_Result):
    name: str
    status: CheckStatus
    seconds: float = 0
    trace: str = ""
    message: str = ""


class RvfiResult(_Result):
    schema_: Literal["verity.rvfi/1"] = Field(default=SCHEMA, alias="schema")
    status: RvfiStatus
    passed: int = 0
    total: int = 0
    duration_ms: int = 0
    message: str = ""
    checks: list[RvfiCheck] = Field(default_factory=list)
    tool_versions: dict[str, str] = Field(default_factory=dict)


def generate_wrapper() -> str:
    """riscv-formal rvfi_wrapper for rv32i_core. Memory inputs stay unconstrained."""
    decls = ["    input clock,", "    input reset,"]
    for name, width in RVFI_PORTS:
        if width == 1:
            decls.append(f"    output {name},")
        else:
            decls.append(f"    output [{width - 1}:0] {name},")
    decls[-1] = decls[-1][:-1]
    conns = [
        "        .clk(clock),",
        "        .rst_n(rst_n),",
        "        .mem_ready(mem_ready),",
        "        .mem_rdata(mem_rdata),",
        "        .mem_valid(),",
        "        .mem_addr(),",
        "        .mem_wdata(),",
        "        .mem_wstrb(),",
        "        .halted(),",
        "        .dbg_stall(),",
    ]
    for name, _width in RVFI_PORTS:
        conns.append(f"        .{name}({name}),")
    conns[-1] = conns[-1][:-1]
    header = "\n".join(decls)
    body = "\n".join(conns)
    return (
        "`default_nettype none\n"
        "\n"
        "// Local fallback: the generated check file defines this macro when YOSYS is set.\n"
        "// A separate yosys read unit does not have to inherit that definition.\n"
        "`ifndef rvformal_rand_reg\n"
        "`define rvformal_rand_reg rand reg\n"
        "`endif\n"
        "\n"
        "module rvfi_wrapper (\n"
        f"{header}\n"
        ");\n"
        "\n"
        "    wire rst_n = ~reset;\n"
        "    (* keep *) `rvformal_rand_reg mem_ready;\n"
        "    (* keep *) `rvformal_rand_reg [31:0] mem_rdata;\n"
        "\n"
        "    rv32i_core dut (\n"
        f"{body}\n"
        "    );\n"
        "\n"
        "endmodule\n"
        "\n"
        "`default_nettype wire\n"
    )


def parse_checks_cfg(text: str) -> dict[str, list[str]]:
    """Section name to the non-comment lines inside it. Enough to audit checks.cfg."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line)
    return sections


def _rollup(checks: list[RvfiCheck]) -> RvfiStatus:
    statuses = [item.status for item in checks]
    if any(status == "fail" for status in statuses):
        return "fail"
    if any(status == "timeout" for status in statuses):
        return "timeout"
    if not checks or any(status == "error" for status in statuses):
        return "error"
    return "ok"


def parse_rvfi(payload: dict[str, Any]) -> RvfiResult:
    """Parse verity.rvfi/1. A reported ok that hides a failing check is downgraded."""
    result = RvfiResult.model_validate(payload)
    passed = sum(item.status == "pass" for item in result.checks)
    total = len(result.checks)
    honest = _rollup(result.checks)
    if result.status == honest and result.passed == passed and result.total == total:
        return result
    if honest == "ok":
        message = result.message
    else:
        bad = next(item for item in result.checks if item.status != "pass")
        message = result.message or f"{bad.name}: {bad.message or bad.status}"
    return result.model_copy(update={"status": honest, "passed": passed, "total": total, "message": message})


def _install() -> None:
    from .base import SCRIPT_PATHS, SandboxRunner

    SCRIPT_PATHS["rvfi"] = "/verity/bin/rvfi"  # type: ignore[index]
    existing = getattr(SandboxRunner, "rvfi", None)
    if getattr(existing, "_verity_rvfi", False):
        return

    async def rvfi(self: SandboxRunner, rtl: Rtl, pack: TaskPack) -> RvfiResult:
        """Run riscv-formal on one core (top plus blocks.v, appended by ToolRunner)."""
        inner = CHECK_TIMEOUT_S * ((ESTIMATED_CHECKS + JOBS - 1) // JOBS) + 120

        def on_timeout(raw: Any) -> RvfiResult:
            return RvfiResult(
                status="timeout",
                message=f"riscv-formal did not finish within {inner} s",
                duration_ms=raw.duration_ms,
            )

        return await self._run(  # type: ignore[arg-type]
            "rvfi",
            pack,
            {"dut.v": rtl.data},
            inner,
            parse_rvfi,
            on_timeout,
            extra_env={
                "VERITY_RVFI_CHECK_TIMEOUT_S": str(CHECK_TIMEOUT_S),
                "VERITY_RVFI_JOBS": str(JOBS),
            },
        )

    rvfi._verity_rvfi = True  # type: ignore[attr-defined]
    SandboxRunner.rvfi = rvfi  # type: ignore[attr-defined]


_install()
