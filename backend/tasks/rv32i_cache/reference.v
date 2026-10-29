`default_nettype none

module rv32i_cache (
    input logic clk, // port
    input logic rst_n, // port
    input logic cpu_valid, // port
    input logic [31:0] cpu_addr, // port
    input logic [31:0] cpu_wdata, // port
    input logic [3:0] cpu_wstrb, // port
    input logic mem_ready, // port
    input logic [31:0] mem_rdata, // port
    output logic cpu_ready, // port
    output logic [31:0] cpu_rdata, // port
    output logic mem_valid, // port
    output logic [31:0] mem_addr, // port
    output logic [31:0] mem_wdata, // port
    output logic [3:0] mem_wstrb // port
);
    logic [15:0] valid;
    logic [25:0] tag [0:15];
    logic [31:0] data [0:15];
    logic [3:0] index;
    logic cacheable;
    logic hit;

    assign index = cpu_addr[5:2];
    assign cacheable = !cpu_addr[31];
    assign hit = cpu_valid & cacheable & (cpu_wstrb == 4'd0)
                 & valid[index] & (tag[index] == cpu_addr[31:6]);
    assign cpu_ready = hit | mem_ready;
    assign cpu_rdata = hit ? data[index] : mem_rdata;
    assign mem_valid = cpu_valid & !hit;
    assign mem_addr = cpu_addr;
    assign mem_wdata = cpu_wdata;
    assign mem_wstrb = cpu_wstrb;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            valid <= 16'd0;
        end else if (mem_valid && mem_ready && cacheable) begin
            if (cpu_wstrb == 4'd0) begin
                valid[index] <= 1'b1;
                tag[index] <= cpu_addr[31:6];
                data[index] <= mem_rdata;
            end else if (valid[index] && (tag[index] == cpu_addr[31:6])) begin
                valid[index] <= 1'b0;
            end
        end
    end
endmodule

`default_nettype wire
