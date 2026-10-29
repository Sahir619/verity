`default_nettype none

module rv32i_cache_core ( // top
    input  logic clk,
    input  logic rst_n,
    input  logic mem_ready, // port
    input  logic [31:0] mem_rdata, // port
    output logic mem_valid, // port
    output logic [31:0] mem_addr, // port
    output logic [31:0] mem_wdata, // port
    output logic [3:0] mem_wstrb, // port
    output logic halted,
    output logic [1:0] dbg_stall, // port
    output logic rvfi_valid, // port
    output logic [63:0] rvfi_order, // port
    output logic [31:0] rvfi_insn, // port
    output logic rvfi_trap, // port
    output logic rvfi_halt, // port
    output logic rvfi_intr, // port
    output logic [1:0] rvfi_mode, // port
    output logic [1:0] rvfi_ixl, // port
    output logic [4:0] rvfi_rs1_addr, // port
    output logic [4:0] rvfi_rs2_addr, // port
    output logic [31:0] rvfi_rs1_rdata, // port
    output logic [31:0] rvfi_rs2_rdata, // port
    output logic [4:0] rvfi_rd_addr, // port
    output logic [31:0] rvfi_rd_wdata, // port
    output logic [31:0] rvfi_pc_rdata, // port
    output logic [31:0] rvfi_pc_wdata, // port
    output logic [31:0] rvfi_mem_addr, // port
    output logic [3:0] rvfi_mem_rmask, // port
    output logic [3:0] rvfi_mem_wmask, // port
    output logic [31:0] rvfi_mem_rdata, // port
    output logic [31:0] rvfi_mem_wdata // port
);

    logic cpu_ready;
    logic [31:0] cpu_rdata;
    logic cpu_valid;
    logic [31:0] cpu_addr;
    logic [31:0] cpu_wdata;
    logic [3:0] cpu_wstrb;

    rv32i_core u_core (
        .clk(clk),
        .rst_n(rst_n),
        .mem_ready(cpu_ready),
        .mem_rdata(cpu_rdata),
        .mem_valid(cpu_valid),
        .mem_addr(cpu_addr),
        .mem_wdata(cpu_wdata),
        .mem_wstrb(cpu_wstrb),
        .halted(halted),
        .dbg_stall(dbg_stall),
        .rvfi_valid(rvfi_valid),
        .rvfi_order(rvfi_order),
        .rvfi_insn(rvfi_insn),
        .rvfi_trap(rvfi_trap),
        .rvfi_halt(rvfi_halt),
        .rvfi_intr(rvfi_intr),
        .rvfi_mode(rvfi_mode),
        .rvfi_ixl(rvfi_ixl),
        .rvfi_rs1_addr(rvfi_rs1_addr),
        .rvfi_rs2_addr(rvfi_rs2_addr),
        .rvfi_rs1_rdata(rvfi_rs1_rdata),
        .rvfi_rs2_rdata(rvfi_rs2_rdata),
        .rvfi_rd_addr(rvfi_rd_addr),
        .rvfi_rd_wdata(rvfi_rd_wdata),
        .rvfi_pc_rdata(rvfi_pc_rdata),
        .rvfi_pc_wdata(rvfi_pc_wdata),
        .rvfi_mem_addr(rvfi_mem_addr),
        .rvfi_mem_rmask(rvfi_mem_rmask),
        .rvfi_mem_wmask(rvfi_mem_wmask),
        .rvfi_mem_rdata(rvfi_mem_rdata),
        .rvfi_mem_wdata(rvfi_mem_wdata)
    );

    rv32i_cache u_cache (
        .clk(clk),
        .rst_n(rst_n), // reset invalidates the cache
        .cpu_valid(cpu_valid),
        .cpu_addr(cpu_addr),
        .cpu_wdata(cpu_wdata),
        .cpu_wstrb(cpu_wstrb),
        .mem_ready(mem_ready),
        .mem_rdata(mem_rdata),
        .cpu_ready(cpu_ready),
        .cpu_rdata(cpu_rdata),
        .mem_valid(mem_valid),
        .mem_addr(mem_addr),
        .mem_wdata(mem_wdata),
        .mem_wstrb(mem_wstrb)
    );
endmodule

`default_nettype wire
