// Human-written reference for alu8 (pack self-test, evals and the bench human reference; never shown to a model or a sandbox).
// Registered outputs: y, zero and carry load on the clock edge (latency 1). Synchronous active-low reset.
module alu8 (
    input        clk,
    input        rst_n,
    input  [7:0] a,
    input  [7:0] b,
    input  [2:0] op,
    output reg [7:0] y,
    output reg       zero,
    output reg       carry
);
    wire [2:0]  sh      = b[2:0];
    wire [8:0]  sum     = {1'b0, a} + {1'b0, b};
    wire [8:0]  dif     = {1'b0, a} - {1'b0, b};
    wire [15:0] shl_ext = {8'd0, a} << sh;       // bit 8 is the last bit shifted out
    wire [8:0]  shr_ext = {a, 1'b0} >> sh;       // bit 0 is the last bit shifted out

    reg [7:0] y_n;
    reg       c_n;
    reg       z_n;

    always @* begin
        y_n = 8'd0;
        c_n = 1'b0;
        case (op)
            3'd0: begin y_n = sum[7:0]; c_n = sum[8]; end
            3'd1: begin y_n = dif[7:0]; c_n = dif[8]; end
            3'd2: y_n = a & b;
            3'd3: y_n = a | b;
            3'd4: y_n = a ^ b;
            3'd5: begin y_n = shl_ext[7:0]; c_n = shl_ext[8]; end
            3'd6: begin y_n = shr_ext[8:1]; c_n = shr_ext[0]; end
            default: begin y_n = 8'd0; c_n = dif[8]; end
        endcase
        z_n = (op == 3'd7) ? (a == b) : (y_n == 8'd0);
    end

    always @(posedge clk) begin
        if (!rst_n) begin
            y     <= 8'd0;
            zero  <= 1'b0;
            carry <= 1'b0;
        end else begin
            y     <= y_n;
            zero  <= z_n;
            carry <= c_n;
        end
    end
endmodule
