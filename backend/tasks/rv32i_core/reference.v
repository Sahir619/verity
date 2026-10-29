`default_nettype none

module rv32i_core (
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

    logic [31:0] pc;
    logic [31:0] ir;
    logic [31:0] alu_out;
    logic [31:0] mdr;
    logic [31:0] pc_plus4;
    logic [31:0] pc_next;

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

    logic [2:0] state;
    logic ctl_mem_we;
    logic ir_we;
    logic rf_wen;
    logic pc_we;
    logic pc_sel;

    logic rf_write;
    logic [31:0] wb_data;
    logic retire_wb;
    logic retire_trap;
    logic [3:0] load_mask;

    assign pc_plus4 = pc + 32'd4;
    // In DECODE alu.y is the jump or branch target. RV32I without C traps when it is not a multiple of 4.
    assign target_misaligned = (is_jal || is_jalr || (is_branch && taken)) && alu_y[1];
    assign trap_in = trap || target_misaligned;
    assign pc_next = pc_sel ? (is_jalr ? (alu_out & ~32'd1) : alu_out) : pc_plus4;

    always_comb begin
        case (a_sel)
            2'd0: alu_a = rf_rd1;
            2'd1: alu_a = pc;
            default: alu_a = 32'd0;
        endcase
        if (b_sel)
            alu_b = imm;
        else
            alu_b = rf_rd2;
    end

    always_comb begin
        case (wb_sel)
            2'd0: wb_data = alu_out;
            2'd1: wb_data = lsu_ld;
            2'd2: wb_data = pc_plus4;
            default: wb_data = 32'd0;
        endcase
    end

    assign addr_lo = (state == 3'd2) ? alu_y[1:0] : alu_out[1:0];
    assign rf_write = rf_wen & dec_rf_we;
    assign mem_addr = (state == 3'd0) ? pc : {alu_out[31:2], 2'b00};
    assign mem_wdata = lsu_sdata;
    assign mem_wstrb = ctl_mem_we ? lsu_wstrb : 4'b0000;

    // WB retires a finished instruction. A trap retires once on the decision cycle, then the FSM enters HALT.
    assign retire_wb = (state == 3'd4);
    assign retire_trap = ((state == 3'd1) && trap_in) || ((state == 3'd2) && (is_load || is_store) && misaligned);
    assign rvfi_valid = retire_wb || retire_trap;
    assign rvfi_insn = ir;
    assign rvfi_trap = retire_trap;
    assign rvfi_halt = retire_trap;
    assign rvfi_intr = 1'b0;
    assign rvfi_mode = 2'd3;
    assign rvfi_ixl = 2'd1;
    assign rvfi_rs1_addr = ir[19:15];
    assign rvfi_rs2_addr = ir[24:20];
    assign rvfi_rs1_rdata = rf_rd1;
    assign rvfi_rs2_rdata = rf_rd2;
    assign rvfi_rd_addr = retire_trap ? 5'd0 : ((rf_write && (ir[11:7] != 5'd0)) ? ir[11:7] : 5'd0);
    assign rvfi_rd_wdata = (rvfi_rd_addr != 5'd0) ? wb_data : 32'd0;
    assign rvfi_pc_rdata = pc;
    assign rvfi_pc_wdata = retire_trap ? pc : pc_next;

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

    assign rvfi_mem_addr = {alu_out[31:2], 2'b00};
    assign rvfi_mem_rmask = (retire_wb && is_load) ? load_mask : 4'b0000;
    assign rvfi_mem_wmask = (retire_wb && is_store) ? lsu_wstrb : 4'b0000;
    assign rvfi_mem_rdata = mdr;
    assign rvfi_mem_wdata = lsu_sdata;

    always_comb begin
        if (halted)
            dbg_stall = 2'd3;
        else if ((state == 3'd0) && !mem_ready)
            dbg_stall = 2'd1;
        else if ((state == 3'd3) && !mem_ready)
            dbg_stall = 2'd2;
        else
            dbg_stall = 2'd0;
    end

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
        .rdata(mdr),
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

    rv32i_ctl u_ctl (
        .clk(clk),
        .rst_n(rst_n),
        .mem_ready(mem_ready),
        .is_load(is_load),
        .is_store(is_store),
        .is_branch(is_branch),
        .is_jal(is_jal),
        .is_jalr(is_jalr),
        .trap(trap_in),
        .taken(taken),
        .misaligned(misaligned),
        .state(state),
        .mem_valid(mem_valid),
        .mem_we(ctl_mem_we),
        .ir_we(ir_we),
        .rf_wen(rf_wen),
        .pc_we(pc_we),
        .pc_sel(pc_sel),
        .halted(halted)
    );

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            pc <= 32'd0;
            ir <= 32'd0;
            alu_out <= 32'd0;
            mdr <= 32'd0;
            rvfi_order <= 64'd0;
        end else begin
            if (state == 3'd2)
                alu_out <= alu_y;
            if ((state == 3'd3) && mem_ready)
                mdr <= mem_rdata;
            if (ir_we)
                ir <= mem_rdata;
            if (pc_we)
                pc <= pc_next;
            if (rvfi_valid)
                rvfi_order <= rvfi_order + 64'd1;
        end
    end

endmodule

`default_nettype wire
