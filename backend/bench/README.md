# CPU benchmark suite

W8 builds `crc32`, `matmult-int`, and `edn` from Embench-IoT commit
`09c2ed8c3b7008c95d08b038de4a3f6dc103ed70`. Global scale factor is 1 and warmup
heat is 0. The unmodified upstream local scale factors are 170, 39, and 81,
respectively. CRC exercises shifts, lookup loads, branches and software
multiplication in the deterministic PRNG. Matrix multiplication exercises
integer arithmetic and memory traffic. EDN exercises integer DSP and software
multiplication. This subset avoids floating point workloads and provides
different instruction and memory mixes with a small reproducible suite.
These are raw cycle measurements of this subset, not an Embench score.

The xPack RISC-V GCC release is `14.2.0-3`, checked against its published SHA-256:
`f574415b63f12b09bdd3475223ab492a465d23810646c90c13a4c3b676c83503`.
The source and licence files remain in `/opt/embench-iot` and the complete
compiler distribution remains in `/opt/riscv-gcc`. riscv-formal is included at
`c992aa61fdfe0846c5ed90324c596202a1c69b76` in `/opt/riscv-formal` for W9.
Sources are fetched only while building the image.

Upstream sources: [xPack release and published checksum](https://github.com/xpack-dev-tools/riscv-none-elf-gcc-xpack/releases/tag/v14.2.0-3),
[Embench source](https://github.com/embench/embench-iot/tree/09c2ed8c3b7008c95d08b038de4a3f6dc103ed70),
and [riscv-formal source](https://github.com/YosysHQ/riscv-formal/tree/c992aa61fdfe0846c5ed90324c596202a1c69b76).

From `backend/`, build with the sandbox directory as the primary context and
this directory as the named context (requires Docker BuildKit):

```powershell
docker build -t verity-tools:0.2 --build-context bench=bench -f sandbox/Dockerfile sandbox
$env:VERITY_TOOLS__IMAGE = 'verity-tools:0.2'
$env:PYTHONIOENCODING = 'utf-8'
& .venv/Scripts/python.exe -m pytest -q tests/bench
```

`build.py` compiles at image build time with `-O2 -march=rv32i -mabi=ilp32`,
strict alignment and no startup files from libc. It links the RV32I multilib
libgcc and newlib for software arithmetic and memory routines. `crt0.S` sets
the stack and global pointer, clears BSS, calls upstream `main`, stores 1 on
successful verification at `0xfffffff8`, then traps. Code starts at zero,
RAM is 1 MiB, and the stack grows down from its top. The build emits ELF, binary,
word-oriented little endian hex images and a SHA-256 suite manifest in
`/verity/bench`.

`/verity/bin/bench` compiles the supplied `dut.v` once using Verilator and the
fixed C++ driver. ToolRunner appends locked `blocks.v` before this call, just
as for the existing stages. The driver reloads RAM and resets the core for
each image. At every falling edge, it acknowledges a request only when the
previous `mem_ready` is zero, returns the old memory word and applies enabled
byte writes. It samples `rvfi_valid` and `dbg_stall` immediately before each
rising edge. The measured interval includes the cycle after the start store
is acknowledged and excludes the cycle after the stop store is acknowledged.
Cycles, instructions and stall counts use exactly that same half-open window.
Initialization and self-check execution are outside it. The simulator continues
after stop until the result store. A trap before that store fails the row.

The suite has a wall deadline including compilation (`VERITY_BENCH_TIMEOUT_S`, 900 s from the host since 2026-10-28) and a safety
limit of one billion total cycles per benchmark. Results go to
`/work/out/result.json` and stdout with schema `verity.bench/1`. Compilation,
simulation, timeout and self-check failures are distinct statuses. Runtime
`VERITY_BENCH_NAMES=crc32` selects one known image for integration testing;
normal `ToolRunner.bench` always runs and requires the complete ordered suite.
The parser recomputes CPI from counters, requires all self-checks, and rejects
an incomplete suite as a pass. The result's `profile` method sums counters and
computes time only when a measured clock period is supplied. Missing clock and
critical-path evidence stay null. Verilator uses two-state simulation, so this
profile supplements the existing functional verification and formal checks.

The test fixture is an independent scratch interpreter with RVFI
and stall ports. It is test infrastructure, never a CPU pack or a candidate
winner. W9's core is tested after merge.

The default image in `verity/config.py` and `config.toml` is an integration
edit for the orchestrator because those paths are outside W8's ownership. W8 explicitly selects the new image in its
real sandbox tests. Existing tool pins and the 0.1 image are preserved.
The orchestrator also needs to extend the shared `core/protocols.py` ToolRunner
protocol with `bench`; the concrete runners already implement it through
`SandboxRunner` in W8's owned `tools/` directory.
