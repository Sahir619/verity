// ---- rv32i_imm: reference.v of the locked block pack ----
module rv32i_imm (
    input logic [31:0] instr,
    output logic [31:0] imm
);
    always_comb begin
        case (instr[6:0])
            7'h03, 7'h13, 7'h67: imm = {{20{instr[31]}}, instr[31:20]};
            7'h23: imm = {{20{instr[31]}}, instr[31:25], instr[11:7]};
            7'h63: imm = {{19{instr[31]}}, instr[31], instr[7], instr[30:25], instr[11:8], 1'b0};
            7'h37, 7'h17: imm = {instr[31:12], 12'b0};
            7'h6f: imm = {{11{instr[31]}}, instr[31], instr[19:12], instr[20], instr[30:21], 1'b0};
            default: imm = 32'b0;
        endcase
    end
endmodule

// ---- rv32i_dec: reference.v of the locked block pack ----
module rv32i_dec (
    input logic [31:0] instr,
    output logic [3:0]  alu_op,
    output logic [1:0]  a_sel,
    output logic b_sel,
    output logic [1:0]  wb_sel,
    output logic rf_we,
    output logic is_load,
    output logic is_store,
    output logic is_branch,
    output logic is_jal,
    output logic is_jalr,
    output logic trap
);
    always_comb begin
        alu_op = 4'd0;
        a_sel = 2'd0;
        b_sel = 1'b0;
        wb_sel = 2'd0;
        rf_we = 1'b0;
        is_load = 1'b0;
        is_store = 1'b0;
        is_branch = 1'b0;
        is_jal = 1'b0;
        is_jalr = 1'b0;
        trap = 1'b0;
        case (instr[6:0])
            7'h37: begin a_sel = 2'd2; b_sel = 1'b1; rf_we = 1'b1; end
            7'h17: begin a_sel = 2'd1; b_sel = 1'b1; rf_we = 1'b1; end
            7'h6f: begin a_sel = 2'd1; b_sel = 1'b1; wb_sel = 2'd2; rf_we = 1'b1; is_jal = 1'b1; end
            7'h67: begin
                if (instr[14:12] == 3'b000) begin
                    b_sel = 1'b1; wb_sel = 2'd2; rf_we = 1'b1; is_jalr = 1'b1;
                end else trap = 1'b1;
            end
            7'h63: begin
                case (instr[14:12])
                    3'b000, 3'b001, 3'b100, 3'b101, 3'b110, 3'b111: begin
                        a_sel = 2'd1; b_sel = 1'b1; is_branch = 1'b1;
                    end
                    default: trap = 1'b1;
                endcase
            end
            7'h03: begin
                case (instr[14:12])
                    3'b000, 3'b001, 3'b010, 3'b100, 3'b101: begin
                        b_sel = 1'b1; wb_sel = 2'd1; rf_we = 1'b1; is_load = 1'b1;
                    end
                    default: trap = 1'b1;
                endcase
            end
            7'h23: begin
                case (instr[14:12])
                    3'b000, 3'b001, 3'b010: begin b_sel = 1'b1; is_store = 1'b1; end
                    default: trap = 1'b1;
                endcase
            end
            7'h13, 7'h33: begin
                rf_we = 1'b1;
                b_sel = (instr[6:0] == 7'h13);
                case (instr[14:12])
                    3'b000: alu_op = (instr[6:0] == 7'h33 && instr[31:25] == 7'h20) ? 4'd1 : 4'd0;
                    3'b001: alu_op = 4'd2;
                    3'b010: alu_op = 4'd3;
                    3'b011: alu_op = 4'd4;
                    3'b100: alu_op = 4'd5;
                    3'b101: alu_op = (instr[31:25] == 7'h20) ? 4'd7 : 4'd6;
                    3'b110: alu_op = 4'd8;
                    3'b111: alu_op = 4'd9;
                endcase
                if (instr[6:0] == 7'h33) begin
                    if (instr[31:25] != 7'h00 &&
                        !(instr[31:25] == 7'h20 && (instr[14:12] == 3'b000 || instr[14:12] == 3'b101)))
                        trap = 1'b1;
                end else begin
                    if (instr[14:12] == 3'b001 && instr[31:25] != 7'h00) trap = 1'b1;
                    if (instr[14:12] == 3'b101 && instr[31:25] != 7'h00 && instr[31:25] != 7'h20) trap = 1'b1;
                end
            end
            7'h0f: begin end
            default: trap = 1'b1;
        endcase
        if (trap) begin
            alu_op = 4'd0;
            a_sel = 2'd0;
            b_sel = 1'b0;
            wb_sel = 2'd0;
            rf_we = 1'b0;
            is_load = 1'b0;
            is_store = 1'b0;
            is_branch = 1'b0;
            is_jal = 1'b0;
            is_jalr = 1'b0;
        end
    end
endmodule

// ---- rv32i_alu: reference.v of the locked block pack ----
module rv32i_alu (
    input logic [31:0] a,
    input logic [31:0] b,
    input logic [3:0] op,
    output logic [31:0] y
);
    always_comb begin
        case (op)
            4'd0: y = a + b;
            4'd1: y = a - b;
            4'd2: y = a << b[4:0];
            4'd3: y = {31'b0, ($signed(a) < $signed(b))};
            4'd4: y = {31'b0, (a < b)};
            4'd5: y = a ^ b;
            4'd6: y = a >> b[4:0];
            4'd7: y = $signed(a) >>> b[4:0];
            4'd8: y = a | b;
            4'd9: y = a & b;
            default: y = 32'b0;
        endcase
    end
endmodule

// ---- rv32i_br: reference.v of the locked block pack ----
module rv32i_br (
    input logic [31:0] a,
    input logic [31:0] b,
    input logic [2:0] funct3,
    output logic taken
);
    always_comb begin
        case (funct3)
            3'b000: taken = (a == b);
            3'b001: taken = (a != b);
            3'b100: taken = ($signed(a) < $signed(b));
            3'b101: taken = ($signed(a) >= $signed(b));
            3'b110: taken = (a < b);
            3'b111: taken = (a >= b);
            default: taken = 1'b0;
        endcase
    end
endmodule

// ---- rv32i_rf: reference.v of the locked block pack ----
// Human reference for rv32i_rf. 31 registers, x1 to x31. x0 reads as 0.
// Synchronous active-low reset clears every register and wins over a write.
module rv32i_rf (
    input  logic        clk,
    input  logic        rst_n,
    input  logic [4:0]  ra1,
    input  logic [4:0]  ra2,
    input  logic [4:0]  wa,
    input  logic [31:0] wd,
    input  logic        we,
    output logic [31:0] rd1, // read data, port 1
    output logic [31:0] rd2
);
    logic [31:0] regs [0:31];

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            regs[0]  <= 32'd0;
            regs[1]  <= 32'd0;
            regs[2]  <= 32'd0;
            regs[3]  <= 32'd0;
            regs[4]  <= 32'd0;
            regs[5]  <= 32'd0;
            regs[6]  <= 32'd0;
            regs[7]  <= 32'd0;
            regs[8]  <= 32'd0;
            regs[9]  <= 32'd0;
            regs[10] <= 32'd0;
            regs[11] <= 32'd0;
            regs[12] <= 32'd0;
            regs[13] <= 32'd0;
            regs[14] <= 32'd0;
            regs[15] <= 32'd0;
            regs[16] <= 32'd0;
            regs[17] <= 32'd0;
            regs[18] <= 32'd0;
            regs[19] <= 32'd0;
            regs[20] <= 32'd0;
            regs[21] <= 32'd0;
            regs[22] <= 32'd0;
            regs[23] <= 32'd0;
            regs[24] <= 32'd0;
            regs[25] <= 32'd0;
            regs[26] <= 32'd0;
            regs[27] <= 32'd0;
            regs[28] <= 32'd0;
            regs[29] <= 32'd0;
            regs[30] <= 32'd0;
            regs[31] <= 32'd0;
        end else if (we && (wa != 5'd0)) begin
            regs[wa] <= wd;
        end
    end

    always_comb begin
        rd1 = (ra1 == 5'd0) ? 32'd0 : regs[ra1];
        rd2 = (ra2 == 5'd0) ? 32'd0 : regs[ra2];
    end
endmodule

// ---- rv32i_lsu: reference.v of the locked block pack ----
// Human reference for rv32i_lsu. Combinational aligner. No clock and no reset.
// Invalid size (funct3[1:0] == 3) fixes sdata at 0.
module rv32i_lsu (
    input  logic [1:0]  addr_lo,
    input  logic [2:0]  funct3,
    input  logic [31:0] rdata, // loaded word
    input  logic [31:0] wdata, // store word
    output logic [31:0] ld,
    output logic [3:0]  wstrb,
    output logic [31:0] sdata, // byte lanes
    output logic        misaligned
);
    logic [1:0] size;
    logic [31:0] shifted;

    always_comb begin
        size = funct3[1:0];
        shifted = rdata >> {addr_lo, 3'b000};
        misaligned = ((size == 2'd1) && addr_lo[0]) || ((size == 2'd2) && (addr_lo != 2'd0)) || (size == 2'd3);

        if (misaligned || (funct3 == 3'b011) || (funct3 == 3'b110) || (funct3 == 3'b111))
            ld = 32'd0;
        else if (size == 2'd0)
            ld = funct3[2] ? {24'd0, shifted[7:0]} : {{24{shifted[7]}}, shifted[7:0]};
        else if (size == 2'd1)
            ld = funct3[2] ? {16'd0, shifted[15:0]} : {{16{shifted[15]}}, shifted[15:0]};
        else
            ld = rdata;

        if (misaligned || funct3[2])
            wstrb = 4'b0000;
        else if (size == 2'd0)
            wstrb = 4'b0001 << addr_lo;
        else if (size == 2'd1)
            wstrb = 4'b0011 << addr_lo;
        else if (size == 2'd2)
            wstrb = 4'b1111;
        else
            wstrb = 4'b0000;

        if (size == 2'd0)
            sdata = {4{wdata[7:0]}};
        else if (size == 2'd1)
            sdata = {2{wdata[15:0]}};
        else if (size == 2'd2)
            sdata = wdata;
        else
            sdata = 32'd0;
    end
endmodule

// ---- rv32i_ctl: reference.v of the locked block pack ----
// Human reference for rv32i_ctl. Multicycle control FSM.
// Synchronous active-low reset enters FETCH. Codes 6 and 7 behave as HALT.
module rv32i_ctl (
    input  logic       clk,
    input  logic       rst_n,
    input  logic       mem_ready,
    input  logic       is_load,
    input  logic       is_store,
    input  logic       is_branch,
    input  logic       is_jal,
    input  logic       is_jalr,
    input  logic       trap,
    input  logic       taken,
    input  logic       misaligned,
    output logic [2:0] state, // present code
    output logic       mem_valid,
    output logic       mem_we,
    output logic       ir_we,
    output logic       rf_wen,
    output logic       pc_we,
    output logic       pc_sel,
    output logic       halted
);
    logic [2:0] state_q;
    logic [2:0] state_n;

    always_ff @(posedge clk) begin
        if (!rst_n)
            state_q <= 3'd0;
        else
            state_q <= state_n;
    end

    always_comb begin
        mem_valid = 1'b0;
        mem_we = 1'b0;
        ir_we = 1'b0;
        rf_wen = 1'b0;
        pc_we = 1'b0;
        pc_sel = 1'b0;
        halted = 1'b0;
        state_n = 3'd5;
        case (state_q)
            3'd0: begin
                mem_valid = 1'b1;
                ir_we = mem_ready;
                state_n = mem_ready ? 3'd1 : 3'd0;
            end
            3'd1: begin
                state_n = trap ? 3'd5 : 3'd2;
            end
            3'd2: begin
                if (is_load || is_store)
                    state_n = misaligned ? 3'd5 : 3'd3;
                else
                    state_n = 3'd4;
            end
            3'd3: begin
                mem_valid = 1'b1;
                mem_we = is_store;
                state_n = mem_ready ? 3'd4 : 3'd3;
            end
            3'd4: begin
                rf_wen = 1'b1;
                pc_we = 1'b1;
                pc_sel = is_jal | is_jalr | (is_branch & taken);
                state_n = 3'd0;
            end
            3'd5, 3'd6, 3'd7: begin
                halted = 1'b1;
                state_n = 3'd5;
            end
            default: begin
                halted = 1'b1;
                state_n = 3'd5;
            end
        endcase
        state = state_q;
    end
endmodule

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

`default_nettype none

module rv32i_cache (
    input logic clk, // port
    input logic rst_n, // port
    input logic cpu_valid, // port
    input logic [31:0] cpu_addr, // port
    input logic [31:0] cpu_wdata, // port
    input logic [3:0] cpu_wstrb, // port
    input logic mem_ready, // port
    input logic [31:0] mem_rdata, // port
    output logic cpu_ready, // port
    output logic [31:0] cpu_rdata, // port
    output logic mem_valid, // port
    output logic [31:0] mem_addr, // port
    output logic [31:0] mem_wdata, // port
    output logic [3:0] mem_wstrb // port
);
    logic [15:0] valid;
    logic [25:0] tag [0:15];
    logic [31:0] data [0:15];
    logic [3:0] index;
    logic cacheable;
    logic hit;

    assign index = cpu_addr[5:2];
    assign cacheable = !cpu_addr[31];
    assign hit = cpu_valid & cacheable & (cpu_wstrb == 4'd0)
                 & valid[index] & (tag[index] == cpu_addr[31:6]);
    assign cpu_ready = hit | mem_ready;
    assign cpu_rdata = hit ? data[index] : mem_rdata;
    assign mem_valid = cpu_valid & !hit;
    assign mem_addr = cpu_addr;
    assign mem_wdata = cpu_wdata;
    assign mem_wstrb = cpu_wstrb;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            valid <= 16'd0;
        end else if (mem_valid && mem_ready && cacheable) begin
            if (cpu_wstrb == 4'd0) begin
                valid[index] <= 1'b1;
                tag[index] <= cpu_addr[31:6];
                data[index] <= mem_rdata;
            end else if (valid[index] && (tag[index] == cpu_addr[31:6])) begin
                valid[index] <= 1'b0;
            end
        end
    end
endmodule

`default_nettype wire
