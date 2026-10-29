module rv32i_alu (
    input logic [31:0] a,
    input logic [31:0] b,
    input logic [3:0] op,
    output logic [31:0] y
);
    always_comb begin
        case (op)
            4'd0: y = a + b;
            4'd1: y = a - b;
            4'd2: y = a << b[4:0];
            4'd3: y = {31'b0, ($signed(a) < $signed(b))};
            4'd4: y = {31'b0, (a < b)};
            4'd5: y = a ^ b;
            4'd6: y = a >> b[4:0];
            4'd7: y = $signed(a) >>> b[4:0];
            4'd8: y = a | b;
            4'd9: y = a & b;
            default: y = 32'b0;
        endcase
    end
endmodule
