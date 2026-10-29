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
