# Task pack self-test

Spec for the `packs` evals suite (ARCHITECTURE 13 and 14.3, CONTRACTS section 4). It costs $0 (no LLM) and runs
every pack under `backend/tasks/` through the same sandbox scripts a live run uses. A pack that fails its
self-test must not be offered, and the backend refuses to start on a broken pack (`load_packs` fails fast).

Not yet run: the first build pass had no Docker and no simulators. Run it on test day, after
`docker build -t verity-tools:0.1 -f sandbox/Dockerfile sandbox`, with `python -m verity.evals --suite packs`.

## What the suite does, per pack

1. **Integrity.** `TaskPack.load(root)` (parses `task.toml`, which rejects unknown keys, and verifies
   `MANIFEST.sha256`). Also `python tasks/make_manifest.py --check` must print `ok` for the pack. Record the pack hash.
2. **Rules.** `pack.validate_rtl(pack.reference_rtl())` must succeed (the reference obeys the coding rules, the
   line limit and the 20 KB limit). Otherwise the pack could never pass through a normal run.
3. **Reference passes.** Copy the pack with `pack.copy_for_sandbox(tmp)`, put the reference at `dut.v`, call
   `ToolRunner.verify`. Required: `status == "pass"`, `sim.total == sim.passed == task.toml test_count`,
   `lint.ok`, `contract.ok`. Warnings are allowed, errors are not.
4. **Determinism.** Run step 3 twice. Both runs must give the same `sim.total`, `sim.passed` and the same case
   list in `result.json` (`sim_result.json` `cases`). A flaky testbench is a failed pack.
5. **Measurable.** Call `ToolRunner.measure` on the reference. Required: `synth.ok` with an area greater than 0.
   `sta.ok` is reported but not required (STA is the least verified stage, CONTRACTS section 12).
6. **Mutants fail.** For every mutant in the table below, apply the exact text replacement to `reference.v`, check
   the result still passes `pack.validate_rtl` (so the failure must come from the sandbox, not the rules), call
   `verify`, and require `status != "pass"`. The expected status column is a guide; any non-pass status counts,
   and a mutant that passes means the testbench is too weak and the pack fails its self-test.
7. **Equivalence sanity (only when `eqy.enabled`).** `check_equivalence(reference, reference)` should be
   `proven` or `inconclusive`, never `not_equivalent`. `check_equivalence(reference, mutant)` for the first
   `sim_fail` mutant should be `not_equivalent` or `inconclusive`, never `proven`. This guards the proof path
   against false positives.
8. **Pack unchanged.** After all runs, `pack.reverify()` must succeed and the pack hash must equal step 1.

The report line per pack: `pack_id, pack_sha256, reference_pass, tests, mutants_killed/mutants_total,
area_um2, clock_period_ns, eqy_self, eqy_mutant`.

## Mutants

Each row is one exact, single occurrence text replacement in `reference.v` (the find string appears exactly once
in that file).

| Pack | Mutant | Find | Replace | Expected |
|---|---|---|---|---|
| mac4 | done_early | `cnt == 4'd15` | `cnt == 4'd14` | sim_fail |
| mac4 | acc_too_narrow | `output reg [21:0] acc` | `output reg [15:0] acc` | contract_fail |
| mac4 | lane_dropped | `acc <= acc + {4'd0, lane_sum};` | `acc <= acc + {4'd0, p0};` | sim_fail |
| mac4 | restart_after_done | `end else if (start && !done) begin` | `end else if (start) begin` | sim_fail |
| mac4 | missing_reset | `if (!rst_n) begin` | `if (1'b0) begin` | sim_fail |
| matmul4 | done_early | `cnt == 5'd23` | `cnt == 5'd22` | sim_fail |
| matmul4 | c_too_narrow | `output reg [319:0] c` | `output reg [279:0] c` | contract_fail |
| matmul4 | transposed | `ma[(row * 4 + k) * 8 +: 8] * mb[(k * 4 + col) * 8 +: 8]` | `ma[(col * 4 + k) * 8 +: 8] * mb[(k * 4 + row) * 8 +: 8]` | sim_fail |
| matmul4 | wrong_operand | `mb[cnt[1:0] * 32 +: 32] <= b_row;` | `mb[cnt[1:0] * 32 +: 32] <= a_row;` | sim_fail |
| matmul4 | missing_reset | `if (!rst_n) begin` | `if (1'b0) begin` | sim_fail |
| alu8 | sub_no_borrow | `3'd1: begin y_n = dif[7:0]; c_n = dif[8]; end` | `3'd1: begin y_n = dif[7:0]; c_n = 1'b0; end` | sim_fail |
| alu8 | y_too_narrow | `output reg [7:0] y,` | `output reg [6:0] y,` | contract_fail |
| alu8 | shr_no_carry | `3'd6: begin y_n = shr_ext[8:1]; c_n = shr_ext[0]; end` | `3'd6: begin y_n = shr_ext[8:1]; c_n = 1'b0; end` | sim_fail |
| alu8 | cmp_zero_flag | `z_n = (op == 3'd7) ? (a == b) : (y_n == 8'd0);` | `z_n = (y_n == 8'd0);` | sim_fail |
| alu8 | missing_reset | `if (!rst_n) begin` | `if (1'b0) begin` | sim_fail |
| add8 | cout_stuck | `assign cout = total[8];` | `assign cout = 1'b0;` | sim_fail |
| add8 | cin_ignored | `{8'd0, cin}` | `{8'd0, 1'b0}` | sim_fail |
| add8 | sum_too_narrow | `output [7:0] sum,` | `output [6:0] sum,` | contract_fail |
| cnt8 | enable_ignored | `else if (en)` | `else` | sim_fail |
| cnt8 | step_two | `q <= q + 8'd1;` | `q <= q + 8'd2;` | sim_fail |
| cnt8 | missing_reset | `if (!rst_n)` | `if (1'b0)` | sim_fail |
| cnt8 | q_too_narrow | `output reg [7:0] q` | `output reg [6:0] q` | contract_fail |
| fsm_seq | wrong_pattern | `(window == 4'b1011)` | `(window == 4'b1010)` | sim_fail |
| fsm_seq | detect_early | `detect <= hit;` | `detect <= (seen == 4'd3) && (hist == 3'b101);` | sim_fail |
| fsm_seq | detect_kept_on_reset | `detect <= 1'b0;` | `detect <= detect;` | sim_fail |
| fsm_seq | missing_reset | `if (!rst_n) begin` | `if (1'b0) begin` | sim_fail |
| fsm_seq | detect_too_wide | `output reg   detect` | `output reg [1:0] detect` | contract_fail |
| mm4_int4 | done_early | `cnt == 5'd23` | `cnt == 5'd22` | sim_fail |
| mm4_int4 | unsigned_lane | `reg signed [3:0] av;` | `reg [3:0] av;` | sim_fail |
| mm4_int4 | c_too_wide | `output reg [159:0] c,` | `output reg [199:0] c,` | contract_fail |
| mm4_int4 | result_off_by_one | `c[e * 10 +: 10] <= dot;` | `c[e * 10 +: 10] <= dot + 10'sd1;` | sim_fail |
| rv32i_imm | i_zero_extended | `{{20{instr[31]}}, instr[31:20]}` | `{20'b0, instr[31:20]}` | sim_fail |
| rv32i_imm | branch_bit_11_wrong | `instr[31], instr[7], instr[30:25]` | `instr[31], instr[8], instr[30:25]` | sim_fail |
| rv32i_imm | jump_bit_11_wrong | `instr[19:12], instr[20], instr[30:21]` | `instr[19:12], instr[21], instr[30:21]` | sim_fail |
| rv32i_imm | imm_too_narrow | `output logic [31:0] imm` | `output logic [30:0] imm` | contract_fail |
| rv32i_dec | lui_uses_pc | `7'h37: begin a_sel = 2'd2;` | `7'h37: begin a_sel = 2'd1;` | sim_fail |
| rv32i_dec | invalid_shift_allowed | `if (instr[14:12] == 3'b001 && instr[31:25] != 7'h00)` | `if (1'b0)` | sim_fail |
| rv32i_dec | trap_keeps_controls | `if (trap) begin` | `if (1'b0) begin` | sim_fail |
| rv32i_dec | alu_op_too_narrow | `output logic [3:0]  alu_op` | `output logic [2:0]  alu_op` | contract_fail |
| rv32i_alu | unmasked_sll | `4'd2: y = a << b[4:0];` | `4'd2: y = a << b;` | sim_fail |
| rv32i_alu | unsigned_slt | `4'd3: y = {31'b0, ($signed(a) < $signed(b))};` | `4'd3: y = {31'b0, (a < b)};` | sim_fail |
| rv32i_alu | logical_sra | `4'd7: y = $signed(a) >>> b[4:0];` | `4'd7: y = a >> b[4:0];` | sim_fail |
| rv32i_alu | y_too_narrow | `output logic [31:0] y` | `output logic [30:0] y` | contract_fail |
| rv32i_br | unsigned_blt | `3'b100: taken = ($signed(a) < $signed(b));` | `3'b100: taken = (a < b);` | sim_fail |
| rv32i_br | bge_excludes_equal | `3'b101: taken = ($signed(a) >= $signed(b));` | `3'b101: taken = ($signed(a) > $signed(b));` | sim_fail |
| rv32i_br | invalid_taken | `default: taken = 1'b0;` | `default: taken = 1'b1;` | sim_fail |
| rv32i_br | taken_too_wide | `output logic taken` | `output logic [1:0] taken` | contract_fail |
| rv32i_rf | x0_reads_wd | `(ra1 == 5'd0) ? 32'd0` | `(ra1 == 5'd0) ? wd` | sim_fail |
| rv32i_rf | writes_ignored | `else if (we && (wa != 5'd0)) begin` | `else if (1'b0) begin` | sim_fail |
| rv32i_rf | missing_reset | `if (!rst_n) begin` | `if (1'b0) begin` | sim_fail |
| rv32i_rf | rd1_too_narrow | `output logic [31:0] rd1` | `output logic [30:0] rd1` | contract_fail |
| rv32i_lsu | sign_becomes_zero | `{{24{shifted[7]}}` | `{24'd0` | sim_fail |
| rv32i_lsu | ignore_misaligned | `misaligned || (funct3 == 3'b011)` | `1'b0 || (funct3 == 3'b011)` | sim_fail |
| rv32i_lsu | sdata_byte_once | `{4{wdata[7:0]}}` | `{24'd0, wdata[7:0]}` | sim_fail |
| rv32i_lsu | ld_too_narrow | `output logic [31:0] ld` | `output logic [30:0] ld` | contract_fail |
| rv32i_ctl | trap_ignored | `state_n = trap ? 3'd5 : 3'd2;` | `state_n = 3'd2;` | sim_fail |
| rv32i_ctl | fetch_skips_wait | `state_n = mem_ready ? 3'd1 : 3'd0;` | `state_n = 3'd1;` | sim_fail |
| rv32i_ctl | branch_pc_sel | `is_jal | is_jalr | (is_branch & taken)` | `is_jal | is_jalr` | sim_fail |
| rv32i_ctl | missing_reset | `if (!rst_n)` | `if (1'b0)` | sim_fail |
| rv32i_ctl | state_too_narrow | `output logic [2:0] state` | `output logic [1:0] state` | contract_fail |
| rv32i_pctl | redirect_ignored | `ex_valid_n = !(halted_q | trap_now_w) & !redirect_w;` | `ex_valid_n = !(halted_q | trap_now_w);` | sim_fail |
| rv32i_pctl | trap_ignored | `halted_n = halted_q | trap_now_w;` | `halted_n = halted_q;` | sim_fail |
| rv32i_pctl | branch_taken_ignored | `is_jal | is_jalr | (is_branch & taken)` | `is_jal | is_jalr` | sim_fail |
| rv32i_pctl | missing_reset | `if (!rst_n) begin` | `if (1'b0) begin` | sim_fail |
| rv32i_pctl | dbg_stall_too_narrow | `output logic [1:0] dbg_stall` | `output logic dbg_stall` | contract_fail |
| rv32i_core | x0_wdata | `(rvfi_rd_addr != 5'd0) ? wb_data : 32'd0` | `wb_data` | sim_fail |
| rv32i_core | pc_plus_eight | `pc + 32'd4` | `pc + 32'd8` | sim_fail |
| rv32i_core | rvfi_rd_narrow | `output logic [4:0] rvfi_rd_addr` | `output logic [3:0] rvfi_rd_addr` | contract_fail |
| rv32i_core | no_target_trap | `.trap(trap_in),` | `.trap(trap),` | sim_fail |
| rv32i_core | target_bit0 | `&& alu_y[1];` | `&& alu_y[0];` | sim_fail |
| rv32i_cache | stale_after_write | `valid[index] <= 1'b0;` | `valid[index] <= valid[index];` | sim_fail |
| rv32i_cache | wrong_index | `assign index = cpu_addr[5:2];` | `assign index = cpu_addr[6:3];` | sim_fail |
| rv32i_cache | hit_on_write | `& (cpu_wstrb == 4'd0)` | `& 1'b1` | sim_fail |
| rv32i_cache | missing_reset | `valid <= 16'd0;` | `valid <= valid;` | sim_fail |
| rv32i_cache | caches_io | `assign cacheable = !cpu_addr[31];` | `assign cacheable = 1'b1;` | sim_fail |
| rv32i_cache | rdata_too_narrow | `output logic [31:0] cpu_rdata` | `output logic [30:0] cpu_rdata` | contract_fail |
| rv32i_cache_core | cache_reset_ignored | `.rst_n(rst_n), // reset invalidates the cache` | `.rst_n(1'b1), // reset invalidates the cache` | sim_fail |
| rv32i_cache_core | stores_become_reads | `.cpu_wstrb(cpu_wstrb)` | `.cpu_wstrb(4'd0)` | sim_fail |
| rv32i_cache_core | address_alias | `.cpu_addr(cpu_addr)` | `.cpu_addr({cpu_addr[31:6], 1'b0, cpu_addr[4:0]})` | sim_fail |
| rv32i_cache_core | memory_data_zero | `.mem_rdata(mem_rdata)` | `.mem_rdata(32'd0)` | sim_fail |
| rv32i_cache_core | rvfi_rd_narrow | `output logic [4:0] rvfi_rd_addr` | `output logic [3:0] rvfi_rd_addr` | contract_fail |
| rv32i_pipe2_core | no_kill | `assign rvfi_valid = retire;` | `assign rvfi_valid = advance;` | sim_fail |
| rv32i_pipe2_core | pc_instead_of_ex_pc | `2'd1: alu_a = ex_pc;` | `2'd1: alu_a = pc;` | sim_fail |
| rv32i_pipe2_core | link_plus_eight | `assign link_value = ex_pc + 32'd4;` | `assign link_value = ex_pc + 32'd8;` | sim_fail |
| rv32i_pipe2_core | jalr_bit0 | `alu_y & ~32'd1` | `alu_y` | sim_fail |
| rv32i_pipe2_core | x0_wdata | `(rvfi_rd_addr != 5'd0) ? wb_data : 32'd0` | `wb_data` | sim_fail |
| rv32i_pipe2_core | rvfi_rd_narrow | `output logic [4:0] rvfi_rd_addr` | `output logic [3:0] rvfi_rd_addr` | contract_fail |
| rv32i_pipe2_core | no_target_trap | `.trap(trap_in),` | `.trap(trap),` | sim_fail |
| rv32i_pipe2_core | target_bit0 | `&& alu_y[1];` | `&& alu_y[0];` | sim_fail |

A `missing_reset` mutant leaves registers at X (Icarus is 4-state), which the testbench reports as `got: "X"`.

## Width lock (mm4_int4 against matmul4)

The `isolation` suite also uses `mm4_int4` to prove the width lock: feed the `matmul4` reference (8-bit lanes) as
a candidate for `mm4_int4` after renaming its module (`module matmul4` to `module mm4_int4`). `verify` must return `contract_fail` with `port a_row: width 32, expected 16` before any
simulation runs. A repair prompt can therefore never "fix" a failure by widening a port.

## Adding a pack

Create `tasks/<id>/` with `task.toml`, `golden.py`, `test_dut.py`, `reference.v`, then run
`python tasks/make_manifest.py <id>`. The folder name must equal `id` in `task.toml`. A live preset needs the same
`id`, title, text, ports, test count and line limit as `frontend/src/lib/contracts/presets.ts`. Add at least three
mutants to the table above, including one width mutant (`contract_fail`) and one timing mutant.

## rv32i_cache and rv32i_cache_core: W13 evidence, 2026-10-27

Added the independent cache model, deterministic vectors (8 cases, 1478 steps, including 240 random
requests and 1203 random-case steps), shared block testbench, reference RTL, and locked manifests.
The cached-core wrapper composes the unchanged core and cache; its ISA model, assembler and 15 programs
are byte copies of the core pack. Every program runs at waits 0 and 3 (30 cases). Added benchmark waits,
core dispatch, wait parsing, and regression coverage. Only W13-owned paths changed. This entry records
this session's changes because the brief excludes root REPORT.md from the owned paths.

Commands below ran from backend, serially. The first build succeeded into the builder cache only;
--load was needed to expose the local image. No shared image tag was changed.

```powershell
docker build -t verity-tools:w13 --build-context bench=bench -f sandbox/Dockerfile sandbox
docker build --load -t verity-tools:w13 --build-context bench=bench -f sandbox/Dockerfile sandbox
$env:PYTHONIOENCODING = 'utf-8'
$env:VERITY_TOOLS__IMAGE = 'verity-tools:w13'
$env:VERITY_BENCH_IMAGE = 'verity-tools:w13'
& .venv/Scripts/python.exe -m verity.evals --suite packs --tasks rv32i_cache,rv32i_cache_core
& .venv/Scripts/python.exe -m pytest -q tests/packs/test_packs_static.py tests/evals/test_injectors_and_mutants.py tests/bench
& .venv/Scripts/python.exe -m pytest -q -s tests/bench/test_sandbox.py::test_cached_programs_report_hits
& .venv/Scripts/python.exe tests/bench/measure_references.py
```

The image digest recorded by evals was
`sha256:fe1ab7e60134cbda46af4f3667c9b2a5ea175a2ecebb5348b2e651d00fb7c623`.
After the orchestrator's memory notice, docker ps was checked before each new sandbox command;
no other worktree's tools container was present. The idle BuildKit service remained running.

Actual eval output (exit 0, no FAIL lines):

```text
eval run e_20261027T204431Z_9fc928: provider=fake suites=packs tasks=rv32i_cache,rv32i_cache_core
-- suite packs
results: backend/evals/results/e_20261027T204431Z_9fc928.jsonl (estimated spend $0.0000)
```

The rewritten evals/report.md was restored byte for byte. Results from that JSONL:

| Pack | Pack SHA-256 | Reference | Cases | Mutants killed | Area um2 | Clock ns | EQY self | EQY mutant |
|---|---|---|---:|---|---:|---:|---|---|
| rv32i_cache | `d2c5d273026eb83e38a15db77c42dd915b6b78ee5cd558166118c3e627d5b6c3` | pass | 8 | 6/6 | 7300.64 | 2.957 | proven | not_equivalent |
| rv32i_cache_core | `fe299a10d57401294481af89c44b9d211c3ea1de804fda49be0a9f5307fc59f9` | pass | 30 | 5/5 | 20809.45 | 8.757 | disabled | disabled |

Both packs also passed determinism, the extra seed, and reference/mutant waveform checks.

Actual pytest outputs:

```text
141 passed in 215.76s (0:03:35)
CACHE_HITS program=01_sum1to10.s wait=3 requests=37 external=10 hits=27
CACHE_HITS program=02_fibonacci.s wait=3 requests=57 external=12 hits=45
CACHE_HITS program=03_array_sum.s wait=3 requests=15 external=15 hits=0
1 passed in 8.57s
```

Hits are counted from public pins: fetched instructions (including the final trap), plus retired legal
data accesses, less acknowledged external memory requests. No internal candidate signal is used.

Measured reference benchmarks, same locked images and the marker window of the unified harness:

| Core | Benchmark | Wait | Cycles | Instructions | CPI | Busy | Fetch | Data | Pass |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| rv32i_core | crc32 | 0 | 23508612 | 5746546 | 4.090912 | 23508612 | 0 | 0 | True |
| rv32i_core | matmult-int | 0 | 97466906 | 24112967 | 4.042095 | 97466906 | 0 | 0 | True |
| rv32i_core | crc32 | 3 | 42315534 | 5746546 | 7.363647 | 23508612 | 17239638 | 1567284 | True |
| rv32i_core | matmult-int | 3 | 172850921 | 24112967 | 7.168380 | 97466906 | 72338901 | 3045114 | True |
| rv32i_cache_core | crc32 | 0 | 23508612 | 5746546 | 4.090912 | 23508612 | 0 | 0 | True |
| rv32i_cache_core | matmult-int | 0 | 97466906 | 24112967 | 4.042095 | 97466906 | 0 | 0 | True |
| rv32i_cache_core | crc32 | 3 | 40356615 | 5746546 | 7.022760 | 23508612 | 15280719 | 1567284 | True |
| rv32i_cache_core | matmult-int | 3 | 106538363 | 24112967 | 4.418302 | 97466906 | 6026343 | 3045114 | True |

All eight rows passed their self-check (1). At wait 3 the cache reduced crc32 cycles by 4.6293% and
matmult-int cycles by 38.3640%. Every reduction was in fetch stalls; data stalls and instruction counts
were unchanged. At wait 0 both cores had identical cycle counts.

The measurement process saved all four passing results, then exited 1 in its table formatter because
it read cycles_by_stall instead of the serialized cyclesByStall field. The formatter was corrected and
run against the saved raw JSON without rerunning Docker. That replay validated all eight rows and
printed the table above successfully (exit 0). The corrected helper is included for reproducibility.
The replay used the backend venv's python.exe with this Python body on stdin:

```python
import json, runpy, tempfile
from pathlib import Path
rows = json.loads((Path(tempfile.gettempdir()) / 'verity-w13-measurements.json').read_text())
assert len(rows) == 4 and all(r['status'] == 'pass' for r in rows)
runpy.run_path('tests/bench/measure_references.py')['print_table'](rows)
```

Pipeline dispatch is covered by the entrypoint unit tests, including not_supported for wait above 0.
The W12 pipeline harness is not present in this worktree, so no real pipeline benchmark is claimed.
