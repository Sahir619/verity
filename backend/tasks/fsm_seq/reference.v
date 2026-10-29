// Human-written reference for fsm_seq (pack self-test, evals and the bench human reference; never shown to a model or a sandbox).
// Detects 1011, overlaps allowed. detect is registered (latency 1).
// hist holds the last 3 bits; seen counts how many of them are valid since reset (saturates at 3).
module fsm_seq (
    input        clk,
    input        rst_n,
    input        din,
    output reg   detect
);
    reg [2:0] hist;
    reg [3:0] seen;
    wire [3:0] window = {hist, din};
    wire hit = (seen == 4'd3) && (window == 4'b1011);

    always @(posedge clk) begin
        if (!rst_n) begin
            hist   <= 3'd0;
            seen   <= 4'd0;
            detect <= 1'b0;
        end else begin
            hist   <= window[2:0];
            seen   <= (seen == 4'd3) ? seen : seen + 4'd1;
            detect <= hit;
        end
    end
endmodule
