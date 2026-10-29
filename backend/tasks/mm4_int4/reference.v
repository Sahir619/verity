// Human-written reference for mm4_int4 (pack self-test, evals and the bench human reference; never shown to a model or a sandbox).
// Same protocol as matmul4 with signed 4-bit lanes and signed 10-bit results.
// Edge 0: start accepted. Edges 1 to 4: row r of A and B captured. Edges 5 to 20: one result element per edge.
// done rises after edge 24 and stays until reset.
module mm4_int4 (
    input          clk,
    input          rst_n,
    input          start,
    input  [15:0]  a_row,
    input  [15:0]  b_row,
    output reg [159:0] c,
    output reg         done
);
    reg        busy;
    reg [4:0]  cnt;
    reg [63:0] ma;
    reg [63:0] mb;

    wire [3:0] e   = cnt[3:0] - 4'd4;
    wire [1:0] row = e[3:2];
    wire [1:0] col = e[1:0];

    reg signed [9:0] dot;
    reg signed [3:0] av;
    reg signed [3:0] bv;
    integer k;
    always @* begin
        dot = 10'sd0;
        for (k = 0; k < 4; k = k + 1) begin
            av  = ma[(row * 4 + k) * 4 +: 4];
            bv  = mb[(k * 4 + col) * 4 +: 4];
            dot = dot + av * bv;
        end
    end

    always @(posedge clk) begin
        if (!rst_n) begin
            c    <= 160'd0;
            done <= 1'b0;
            busy <= 1'b0;
            cnt  <= 5'd0;
            ma   <= 64'd0;
            mb   <= 64'd0;
        end else if (busy) begin
            cnt <= cnt + 5'd1;
            if (cnt < 5'd4) begin
                ma[cnt[1:0] * 16 +: 16] <= a_row;
                mb[cnt[1:0] * 16 +: 16] <= b_row;
            end else if (cnt < 5'd20) begin
                c[e * 10 +: 10] <= dot;
            end
            if (cnt == 5'd23) begin
                busy <= 1'b0;
                done <= 1'b1;
            end
        end else if (start && !done) begin
            busy <= 1'b1;
            cnt  <= 5'd0;
            c    <= 160'd0;
        end
    end
endmodule
