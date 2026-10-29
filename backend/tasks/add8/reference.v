// Human-written reference for add8 (pack self-test, evals and the bench human reference; never shown to a model or a sandbox).
module add8 (
    input  [7:0] a,
    input  [7:0] b,
    input        cin,
    output [7:0] sum,
    output       cout
);
    wire [8:0] total = {1'b0, a} + {1'b0, b} + {8'd0, cin};
    assign sum  = total[7:0];
    assign cout = total[8];
endmodule
