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
// Human reference for rv32i_pctl. Pipeline control for a 2-stage RV32I.
// Synchronous active-low reset clears both registers and wins over the next state.
module rv32i_pctl (
    input  logic clk, // rising edge
    input  logic rst_n, // active low, synchronous
    input  logic is_load, // decode: load
    input  logic is_store, // decode: store
    input  logic is_branch, // decode: branch
    input  logic is_jal, // decode: jal
    input  logic is_jalr, // decode: jalr
    input  logic trap, // illegal, ecall or ebreak
    input  logic taken, // branch condition
    input  logic misaligned, // address is misaligned
    output logic ex_valid, // execute stage is full
    output logic halted, // trap latched until reset
    output logic advance, // update ir and pc
    output logic redirect, // taken redirect this cycle
    output logic rf_wen, // write enable before dec.rf_we
    output logic dmem_valid, // data request
    output logic dmem_we, // data write
    output logic retire, // retire, including a trap record
    output logic trap_now, // trap visible while in EX
    output logic [1:0] dbg_stall // 0 run, 1 bubble, 3 halt
);
    logic ex_valid_q;
    logic halted_q;
    logic ex_valid_n;
    logic halted_n;
    logic trap_now_w;
    logic redirect_w;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            ex_valid_q <= 1'b0;
            halted_q <= 1'b0;
        end else begin
            ex_valid_q <= ex_valid_n;
            halted_q <= halted_n;
        end
    end

    always_comb begin
        trap_now_w = ex_valid_q & !halted_q & (trap | ((is_load | is_store) & misaligned));
        redirect_w = ex_valid_q & !halted_q & !trap_now_w & (is_jal | is_jalr | (is_branch & taken));
        rf_wen = ex_valid_q & !halted_q & !trap_now_w;
        dmem_valid = ex_valid_q & !halted_q & !trap_now_w & (is_load | is_store);
        dmem_we = dmem_valid & is_store;
        retire = ex_valid_q & !halted_q;
        advance = !halted_q & !trap_now_w;
        dbg_stall = halted_q ? 2'd3 : (ex_valid_q ? 2'd0 : 2'd1);
        ex_valid = ex_valid_q;
        halted = halted_q;
        trap_now = trap_now_w;
        redirect = redirect_w;
        halted_n = halted_q | trap_now_w;
        ex_valid_n = !(halted_q | trap_now_w) & !redirect_w;
    end
endmodule
