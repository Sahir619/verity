// Human-written reference for matmul4 (pack self-test, evals and the bench human reference; never shown to a model or a sandbox).
// Edge 0: start accepted. Edges 1 to 4: row r of A and B captured. Edges 5 to 20: one result element per edge
// (4 multipliers). done rises after edge 24 and stays until reset.
module matmul4 (
    input          clk,
    input          rst_n,
    input          start,
    input  [31:0]  a_row,
    input  [31:0]  b_row,
    output reg [319:0] c,
    output reg         done
);
    reg         busy;
    reg [4:0]   cnt;
    reg [127:0] ma;
    reg [127:0] mb;

    wire [3:0] e   = cnt[3:0] - 4'd4;
    wire [1:0] row = e[3:2];
    wire [1:0] col = e[1:0];

    reg [19:0] dot;
    integer k;
    always @* begin
        dot = 20'd0;
        for (k = 0; k < 4; k = k + 1)
            dot = dot + ma[(row * 4 + k) * 8 +: 8] * mb[(k * 4 + col) * 8 +: 8];
    end

    always @(posedge clk) begin
        if (!rst_n) begin
            c    <= 320'd0;
            done <= 1'b0;
            busy <= 1'b0;
            cnt  <= 5'd0;
            ma   <= 128'd0;
            mb   <= 128'd0;
        end else if (busy) begin
            cnt <= cnt + 5'd1;
            if (cnt < 5'd4) begin
                ma[cnt[1:0] * 32 +: 32] <= a_row;
                mb[cnt[1:0] * 32 +: 32] <= b_row;
            end else if (cnt < 5'd20) begin
                c[e * 20 +: 20] <= dot;
            end
            if (cnt == 5'd23) begin
                busy <= 1'b0;
                done <= 1'b1;
            end
        end else if (start && !done) begin
            busy <= 1'b1;
            cnt  <= 5'd0;
            c    <= 320'd0;
        end
    end
endmodule
