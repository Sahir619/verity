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
