// Human-written reference for cnt8 (pack self-test, evals and the bench human reference; never shown to a model or a sandbox).
module cnt8 (
    input        clk,
    input        rst_n,
    input        en,
    output reg [7:0] q
);
    always @(posedge clk) begin
        if (!rst_n)
            q <= 8'd0;
        else if (en)
            q <= q + 8'd1;
    end
endmodule
