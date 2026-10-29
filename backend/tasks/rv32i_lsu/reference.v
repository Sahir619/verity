// Human reference for rv32i_lsu. Combinational aligner. No clock and no reset.
// Invalid size (funct3[1:0] == 3) fixes sdata at 0.
module rv32i_lsu (
    input  logic [1:0]  addr_lo,
    input  logic [2:0]  funct3,
    input  logic [31:0] rdata, // loaded word
    input  logic [31:0] wdata, // store word
    output logic [31:0] ld,
    output logic [3:0]  wstrb,
    output logic [31:0] sdata, // byte lanes
    output logic        misaligned
);
    logic [1:0] size;
    logic [31:0] shifted;

    always_comb begin
        size = funct3[1:0];
        shifted = rdata >> {addr_lo, 3'b000};
        misaligned = ((size == 2'd1) && addr_lo[0]) || ((size == 2'd2) && (addr_lo != 2'd0)) || (size == 2'd3);

        if (misaligned || (funct3 == 3'b011) || (funct3 == 3'b110) || (funct3 == 3'b111))
            ld = 32'd0;
        else if (size == 2'd0)
            ld = funct3[2] ? {24'd0, shifted[7:0]} : {{24{shifted[7]}}, shifted[7:0]};
        else if (size == 2'd1)
            ld = funct3[2] ? {16'd0, shifted[15:0]} : {{16{shifted[15]}}, shifted[15:0]};
        else
            ld = rdata;

        if (misaligned || funct3[2])
            wstrb = 4'b0000;
        else if (size == 2'd0)
            wstrb = 4'b0001 << addr_lo;
        else if (size == 2'd1)
            wstrb = 4'b0011 << addr_lo;
        else if (size == 2'd2)
            wstrb = 4'b1111;
        else
            wstrb = 4'b0000;

        if (size == 2'd0)
            sdata = {4{wdata[7:0]}};
        else if (size == 2'd1)
            sdata = {2{wdata[15:0]}};
        else if (size == 2'd2)
            sdata = wdata;
        else
            sdata = 32'd0;
    end
endmodule
