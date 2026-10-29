`default_nettype none

// 2-stage RV32I. IF holds pc. EX holds ir, ex_pc and the valid bit inside rv32i_pctl.
// Memory answers in the same cycle: addresses after the rising edge, data before the next one.
module rv32i_pipe2_core ( // top
    input  logic clk, // port
    input  logic rst_n, // port
    input  logic [31:0] imem_rdata, // port
    input  logic [31:0] dmem_rdata, // port
    output logic [31:0] imem_addr, // port
    output logic dmem_valid, // port
    output logic [31:0] dmem_addr, // port
    output logic [31:0] dmem_wdata, // port
    output logic [3:0] dmem_wstrb, // port
    output logic halted, // port
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

    logic [31:0] pc;
    logic [31:0] ir;
    logic [31:0] ex_pc;
    logic [31:0] pc_plus4;
    logic [31:0] link_value;
    logic [31:0] pc_target;
    logic [31:0] pc_seq;

    logic [31:0] imm;
    logic [3:0] alu_op;
    logic [1:0] a_sel;
    logic b_sel;
    logic [1:0] wb_sel;
    logic dec_rf_we;
    logic is_load;
    logic is_store;
    logic is_branch;
    logic is_jal;
    logic is_jalr;
    logic trap;
    logic target_misaligned;
    logic trap_in;

    logic [31:0] rf_rd1;
    logic [31:0] rf_rd2;
    logic [31:0] alu_a;
    logic [31:0] alu_b;
    logic [31:0] alu_y;
    logic taken;

    logic [1:0] addr_lo;
    logic [31:0] lsu_ld;
    logic [3:0] lsu_wstrb;
    logic [31:0] lsu_sdata;
    logic misaligned;

    logic ex_valid;
    logic advance;
    logic redirect;
    logic rf_wen;
    logic dmem_we;
    logic retire;
    logic trap_now;

    logic rf_write;
    logic [31:0] wb_data;
    logic [3:0] load_mask;

    assign imem_addr = pc;
    assign pc_plus4 = pc + 32'd4;
    assign link_value = ex_pc + 32'd4;
    assign pc_target = is_jalr ? (alu_y & ~32'd1) : alu_y;
    assign pc_seq = redirect ? pc_target : pc_plus4;
    // alu.y is the jump or branch target. RV32I without C traps when it is not a multiple of 4.
    assign target_misaligned = (is_jal || is_jalr || (is_branch && taken)) && alu_y[1];
    assign trap_in = trap || target_misaligned;
    assign addr_lo = alu_y[1:0];

    always_comb begin
        case (a_sel)
            2'd0: alu_a = rf_rd1;
            2'd1: alu_a = ex_pc;
            default: alu_a = 32'd0;
        endcase
        if (b_sel)
            alu_b = imm;
        else
            alu_b = rf_rd2;
    end

    always_comb begin
        case (wb_sel)
            2'd0: wb_data = alu_y;
            2'd1: wb_data = lsu_ld;
            2'd2: wb_data = link_value;
            default: wb_data = 32'd0;
        endcase
    end

    assign rf_write = rf_wen & dec_rf_we;
    assign dmem_addr = {alu_y[31:2], 2'b00};
    assign dmem_wdata = lsu_sdata;
    assign dmem_wstrb = dmem_we ? lsu_wstrb : 4'b0000;

    assign rvfi_valid = retire;
    assign rvfi_insn = ir;
    assign rvfi_trap = trap_now;
    assign rvfi_halt = trap_now;
    assign rvfi_intr = 1'b0;
    assign rvfi_mode = 2'd3;
    assign rvfi_ixl = 2'd1;
    assign rvfi_rs1_addr = ir[19:15];
    assign rvfi_rs2_addr = ir[24:20];
    assign rvfi_rs1_rdata = rf_rd1;
    assign rvfi_rs2_rdata = rf_rd2;
    assign rvfi_rd_addr = trap_now ? 5'd0 : ((rf_write && (ir[11:7] != 5'd0)) ? ir[11:7] : 5'd0);
    assign rvfi_rd_wdata = (rvfi_rd_addr != 5'd0) ? wb_data : 32'd0;
    assign rvfi_pc_rdata = ex_pc;
    assign rvfi_pc_wdata = trap_now ? ex_pc : (redirect ? pc_target : link_value);

    always_comb begin
        if (ir[13:12] == 2'd0)
            load_mask = 4'b0001 << addr_lo;
        else if (ir[13:12] == 2'd1)
            load_mask = 4'b0011 << addr_lo;
        else if (ir[13:12] == 2'd2)
            load_mask = 4'b1111;
        else
            load_mask = 4'b0000;
    end

    assign rvfi_mem_addr = {alu_y[31:2], 2'b00};
    assign rvfi_mem_rmask = (retire && !trap_now && is_load) ? load_mask : 4'b0000;
    assign rvfi_mem_wmask = (retire && !trap_now && is_store) ? lsu_wstrb : 4'b0000;
    assign rvfi_mem_rdata = dmem_rdata;
    assign rvfi_mem_wdata = lsu_sdata;

    rv32i_imm u_imm (
        .instr(ir),
        .imm(imm)
    );

    rv32i_dec u_dec (
        .instr(ir),
        .alu_op(alu_op),
        .a_sel(a_sel),
        .b_sel(b_sel),
        .wb_sel(wb_sel),
        .rf_we(dec_rf_we),
        .is_load(is_load),
        .is_store(is_store),
        .is_branch(is_branch),
        .is_jal(is_jal),
        .is_jalr(is_jalr),
        .trap(trap)
    );

    rv32i_alu u_alu (
        .a(alu_a),
        .b(alu_b),
        .op(alu_op),
        .y(alu_y)
    );

    rv32i_br u_br (
        .a(rf_rd1),
        .b(rf_rd2),
        .funct3(ir[14:12]),
        .taken(taken)
    );

    rv32i_lsu u_lsu (
        .addr_lo(addr_lo),
        .funct3(ir[14:12]),
        .rdata(dmem_rdata),
        .wdata(rf_rd2),
        .ld(lsu_ld),
        .wstrb(lsu_wstrb),
        .sdata(lsu_sdata),
        .misaligned(misaligned)
    );

    rv32i_rf u_rf (
        .clk(clk),
        .rst_n(rst_n),
        .ra1(ir[19:15]),
        .ra2(ir[24:20]),
        .wa(ir[11:7]),
        .wd(wb_data),
        .we(rf_write),
        .rd1(rf_rd1),
        .rd2(rf_rd2)
    );

    rv32i_pctl u_pctl (
        .clk(clk),
        .rst_n(rst_n),
        .is_load(is_load),
        .is_store(is_store),
        .is_branch(is_branch),
        .is_jal(is_jal),
        .is_jalr(is_jalr),
        .trap(trap_in),
        .taken(taken),
        .misaligned(misaligned),
        .ex_valid(ex_valid),
        .halted(halted),
        .advance(advance),
        .redirect(redirect),
        .rf_wen(rf_wen),
        .dmem_valid(dmem_valid),
        .dmem_we(dmem_we),
        .retire(retire),
        .trap_now(trap_now),
        .dbg_stall(dbg_stall)
    );

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            pc <= 32'd0;
            ir <= 32'd0;
            ex_pc <= 32'd0;
            rvfi_order <= 64'd0;
        end else begin
            if (advance) begin
                ir <= imem_rdata;
                ex_pc <= pc;
                pc <= pc_seq;
            end
            if (rvfi_valid)
                rvfi_order <= rvfi_order + 64'd1;
        end
    end

endmodule

`default_nettype wire
