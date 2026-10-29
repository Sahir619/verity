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
