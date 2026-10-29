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
