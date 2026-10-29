// Cycle driver for the locked benchmark images on rv32i_pipe2_core.
// Both ports share one 1 MiB memory and answer in the low half of the cycle.
#include "Vrv32i_pipe2_core.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <string>

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    if (argc != 3) return 2;
    std::array<uint32_t, 262144> memory{};
    std::ifstream image(argv[1]);
    uint64_t word = 0, index = 0;
    while (image >> std::hex >> word) {
        if (index >= memory.size() || word > UINT32_MAX) return 2;
        memory[index++] = uint32_t(word);
    }
    if (!image.eof() || index == 0) return 2;
    const uint64_t limit = std::stoull(argv[2]);
    Vrv32i_pipe2_core dut;
    dut.clk = 0; dut.rst_n = 0; dut.imem_rdata = 0; dut.dmem_rdata = 0;
    for (int i = 0; i < 4; ++i) {
        dut.clk = 0; dut.eval(); dut.clk = 1; dut.eval();
    }
    dut.rst_n = 1;
    bool active = false, started = false, stopped = false, result_seen = false;
    uint64_t cycles = 0, instructions = 0, stalls[3] = {};
    uint32_t result = 0;
    std::string error;
    for (uint64_t tick = 0; tick < limit && error.empty(); ++tick) {
        dut.clk = 0; dut.eval();
        const uint32_t iaddr = dut.imem_addr;
        if (iaddr >= 1048576 || (iaddr & 3)) {
            error = "memory address outside aligned 1 MiB RAM";
        } else {
            dut.imem_rdata = memory[(iaddr >> 2) & 0x3ffff];
        }
        if (error.empty() && dut.dmem_valid) {
            const uint32_t addr = dut.dmem_addr;
            const uint32_t data = dut.dmem_wdata;
            const unsigned mask = dut.dmem_wstrb;
            dut.dmem_rdata = 0;
            if (addr == 0xfffffff0 || addr == 0xfffffff4 || addr == 0xfffffff8) {
                if (mask != 15) error = "marker requires a word store";
                else if (addr == 0xfffffff0) {
                    if (started || stopped) error = "duplicate start marker";
                    started = true; active = true;
                } else if (addr == 0xfffffff4) {
                    if (!active || stopped) error = "stop without start";
                    active = false; stopped = true;
                } else {
                    if (!stopped) error = "result before stop";
                    result = data; result_seen = true;
                }
            } else if (addr >= 1048576 || (addr & 3)) {
                error = "memory address outside aligned 1 MiB RAM";
            } else {
                uint32_t &cell = memory[(addr >> 2) & 0x3ffff];
                dut.dmem_rdata = cell;
                for (unsigned lane = 0; lane < 4; ++lane)
                    if (mask & (1u << lane))
                        cell = (cell & ~(0xffu << (lane * 8))) | (data & (0xffu << (lane * 8)));
            }
        } else if (error.empty()) {
            dut.dmem_rdata = 0;
        }
        dut.eval();
        if (result_seen) break;
        if (dut.halted || (dut.rvfi_valid && dut.rvfi_trap)) {
            error = "core halted or trapped before self-check result";
            break;
        }
        // Both cycles and retire pulses use the same half-open marker window.
        if (active) {
            ++cycles;
            if (dut.rvfi_valid) ++instructions;
            if (dut.dbg_stall < 3) ++stalls[dut.dbg_stall];
            else error = "halted stall code inside measurement window";
        }
        dut.clk = 1; dut.eval();
    }
    if (!result_seen && error.empty()) error = "cycle limit exceeded";
    const bool passed = error.empty() && started && stopped && result_seen && result == 1 && instructions > 0;
    std::cout << "{\"cycles\":" << cycles << ",\"instructions\":" << instructions
              << ",\"cycles_by_stall\":{\"busy\":" << stalls[0] << ",\"fetch\":" << stalls[1]
              << ",\"data\":" << stalls[2] << "},\"passed\":" << (passed ? "true" : "false")
              << ",\"self_check\":" << (result_seen ? std::to_string(result) : "null")
              << ",\"message\":\"" << error << "\"}\n";
    dut.final();
    return 0;
}
