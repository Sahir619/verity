// Independent scratch RV32I interpreter for W8 integration tests, never a task pack.
module rv32i_core(
    input logic clk, rst_n, mem_ready,
    input logic [31:0] mem_rdata,
    output logic mem_valid,
    output logic [31:0] mem_addr, mem_wdata,
    output logic [3:0] mem_wstrb,
    output logic halted, rvfi_valid,
    output logic [1:0] dbg_stall,
    output logic [63:0] rvfi_order,
    output logic [31:0] rvfi_insn,
    output logic rvfi_trap, rvfi_halt, rvfi_intr,
    output logic [1:0] rvfi_mode, rvfi_ixl,
    output logic [4:0] rvfi_rs1_addr, rvfi_rs2_addr, rvfi_rd_addr,
    output logic [31:0] rvfi_rs1_rdata, rvfi_rs2_rdata, rvfi_rd_wdata,
    output logic [31:0] rvfi_pc_rdata, rvfi_pc_wdata, rvfi_mem_addr,
    output logic [3:0] rvfi_mem_rmask, rvfi_mem_wmask,
    output logic [31:0] rvfi_mem_rdata, rvfi_mem_wdata
);
    logic [31:0] regs [0:31];
    logic [31:0] pc, insn, next_pc, value, addr, store_data;
    logic [4:0] dest;
    logic [3:0] mask;
    logic [2:0] state;
    logic trapped, load_op;
    logic [31:0] a, b, imm, tmp, shifted;
    logic [6:0] op;
    logic [2:0] f3;
    integer i;

    always_comb begin
        mem_valid = state == 0 || state == 2;
        mem_addr = state == 0 ? pc : {addr[31:2], 2'b00};
        mem_wdata = store_data;
        mem_wstrb = state == 2 && !load_op ? mask : 0;
        halted = state == 4;
        dbg_stall = halted ? 3 : (state == 0 && !mem_ready ? 1 : (state == 2 && !mem_ready ? 2 : 0));
        rvfi_valid = state == 3;
        rvfi_trap = trapped;
        rvfi_halt = trapped;
        rvfi_intr = 0;
        rvfi_mode = 3;
        rvfi_ixl = 1;
        rvfi_insn = insn;
        rvfi_rs1_addr = insn[19:15];
        rvfi_rs2_addr = insn[24:20];
        rvfi_rs1_rdata = regs[insn[19:15]];
        rvfi_rs2_rdata = regs[insn[24:20]];
        rvfi_rd_addr = trapped ? 0 : dest;
        rvfi_rd_wdata = dest == 0 || trapped ? 0 : value;
        rvfi_pc_rdata = pc;
        rvfi_pc_wdata = next_pc;
    end

    always @(posedge clk) begin
        if (!rst_n) begin
            pc <= 0; insn <= 0; next_pc <= 0; state <= 0;
            value <= 0; dest <= 0; addr <= 0; store_data <= 0; mask <= 0;
            trapped <= 0; load_op <= 0; rvfi_order <= 0;
            rvfi_mem_addr <= 0; rvfi_mem_rmask <= 0; rvfi_mem_wmask <= 0;
            rvfi_mem_rdata <= 0; rvfi_mem_wdata <= 0;
            for (i = 0; i < 32; i = i + 1) regs[i] <= 0;
        end else begin
            case (state)
                0: if (mem_ready) begin insn <= mem_rdata; state <= 1; end
                1: begin
                    a = regs[insn[19:15]]; b = regs[insn[24:20]];
                    op = insn[6:0]; f3 = insn[14:12];
                    imm = {{20{insn[31]}}, insn[31:20]};
                    next_pc <= pc + 4; dest <= insn[11:7]; value <= 0;
                    trapped <= 0; state <= 3;
                    rvfi_mem_addr <= 0; rvfi_mem_rmask <= 0; rvfi_mem_wmask <= 0;
                    rvfi_mem_rdata <= 0; rvfi_mem_wdata <= 0;
                    case (op)
                        7'h37: value <= {insn[31:12], 12'b0};
                        7'h17: value <= pc + {insn[31:12], 12'b0};
                        7'h6f: begin
                            value <= pc + 4;
                            next_pc <= pc + {{11{insn[31]}}, insn[31], insn[19:12], insn[20], insn[30:21], 1'b0};
                        end
                        7'h67: begin
                            value <= pc + 4; next_pc <= (a + imm) & 32'hfffffffe;
                            if (f3 != 0) trapped <= 1;
                        end
                        7'h63: begin
                            dest <= 0;
                            tmp = {{19{insn[31]}}, insn[31], insn[7], insn[30:25], insn[11:8], 1'b0};
                            case (f3)
                                0: if (a == b) next_pc <= pc + tmp;
                                1: if (a != b) next_pc <= pc + tmp;
                                4: if ($signed(a) < $signed(b)) next_pc <= pc + tmp;
                                5: if ($signed(a) >= $signed(b)) next_pc <= pc + tmp;
                                6: if (a < b) next_pc <= pc + tmp;
                                7: if (a >= b) next_pc <= pc + tmp;
                                default: trapped <= 1;
                            endcase
                        end
                        7'h03, 7'h23: begin
                            load_op <= op == 7'h03;
                            if (op == 7'h23) begin
                                imm = {{20{insn[31]}}, insn[31:25], insn[11:7]};
                                dest <= 0;
                            end
                            tmp = a + imm; addr <= tmp; state <= 2;
                            rvfi_mem_addr <= {tmp[31:2], 2'b00};
                            case (f3[1:0])
                                0: begin mask <= 1 << tmp[1:0]; store_data <= {4{b[7:0]}}; end
                                1: begin mask <= 3 << tmp[1:0]; store_data <= {2{b[15:0]}}; end
                                2: begin mask <= 15; store_data <= b; end
                                default: begin trapped <= 1; state <= 3; end
                            endcase
                            if ((f3[1:0] == 1 && tmp[0]) || (f3[1:0] == 2 && tmp[1:0] != 0)) begin
                                trapped <= 1; state <= 3;
                            end
                            if ((op == 7'h23 && f3 > 2) || (op == 7'h03 && (f3 == 3 || f3 > 5))) begin
                                trapped <= 1; state <= 3;
                            end
                        end
                        7'h13, 7'h33: begin
                            tmp = op == 7'h13 ? imm : b;
                            case (f3)
                                0: value <= op == 7'h33 && insn[30] ? a - tmp : a + tmp;
                                1: value <= a << tmp[4:0];
                                2: value <= {31'b0, $signed(a) < $signed(tmp)};
                                3: value <= {31'b0, a < tmp};
                                4: value <= a ^ tmp;
                                5: value <= insn[30] ? $unsigned($signed(a) >>> tmp[4:0]) : a >> tmp[4:0];
                                6: value <= a | tmp;
                                7: value <= a & tmp;
                            endcase
                            if (op == 7'h33 && insn[31:25] != 0 &&
                                !(insn[31:25] == 7'h20 && (f3 == 0 || f3 == 5))) trapped <= 1;
                            if (op == 7'h13 && f3 == 1 && insn[31:25] != 0) trapped <= 1;
                            if (op == 7'h13 && f3 == 5 && insn[31:25] != 0 && insn[31:25] != 7'h20) trapped <= 1;
                        end
                        7'h0f: dest <= 0;
                        default: begin trapped <= 1; dest <= 0; end
                    endcase
                end
                2: if (mem_ready) begin
                    if (load_op) begin
                        shifted = mem_rdata >> (addr[1:0] * 8);
                        case (insn[14:12])
                            0: value <= {{24{shifted[7]}}, shifted[7:0]};
                            1: value <= {{16{shifted[15]}}, shifted[15:0]};
                            2: value <= mem_rdata;
                            4: value <= {24'b0, shifted[7:0]};
                            5: value <= {16'b0, shifted[15:0]};
                            default: trapped <= 1;
                        endcase
                        rvfi_mem_rmask <= mask; rvfi_mem_rdata <= mem_rdata;
                    end else begin
                        rvfi_mem_wmask <= mask; rvfi_mem_wdata <= store_data;
                    end
                    state <= 3;
                end
                3: begin
                    rvfi_order <= rvfi_order + 1;
                    if (trapped) state <= 4;
                    else begin
                        if (dest != 0) regs[dest] <= value;
                        pc <= next_pc; state <= 0;
                    end
                end
                default: state <= 4;
            endcase
        end
    end
endmodule
