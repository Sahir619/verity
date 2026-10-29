// Human-written reference for mac4 (pack self-test, evals and the bench human reference; never shown to a model or a sandbox).
// Edge 0: start accepted, acc cleared. Edges 1 to 16: acc += sum of the four lane products.
// done rises after edge 16 and stays until reset.
module mac4 (
    input         clk,
    input         rst_n,
    input         start,
    input  [31:0] a,
    input  [31:0] b,
    output reg [21:0] acc,
    output reg        done
);
    reg       busy;
    reg [3:0] cnt;

    wire [15:0] p0 = a[7:0]   * b[7:0];
    wire [15:0] p1 = a[15:8]  * b[15:8];
    wire [15:0] p2 = a[23:16] * b[23:16];
    wire [15:0] p3 = a[31:24] * b[31:24];
    wire [17:0] lane_sum = {2'b00, p0} + {2'b00, p1} + {2'b00, p2} + {2'b00, p3};

    always @(posedge clk) begin
        if (!rst_n) begin
            acc  <= 22'd0;
            done <= 1'b0;
            busy <= 1'b0;
            cnt  <= 4'd0;
        end else if (busy) begin
            acc <= acc + {4'd0, lane_sum};
            cnt <= cnt + 4'd1;
            if (cnt == 4'd15) begin
                busy <= 1'b0;
                done <= 1'b1;
            end
        end else if (start && !done) begin
            acc  <= 22'd0;
            cnt  <= 4'd0;
            busy <= 1'b1;
        end
    end
endmodule
