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
