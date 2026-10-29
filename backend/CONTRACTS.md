# Verity backend build contract (`backend/`)

Written 2026-10-05 by the architect, before the five builders start. It turns `backend/ARCHITECTURE.md` into exact interfaces. When this file and ARCHITECTURE.md disagree, this file wins for interfaces and ARCHITECTURE.md wins for intent; flag the conflict, do not silently reconcile.

The shared code already exists and is the source of truth for every type named here:

| File | What it holds |
|---|---|
| `verity/config.py` | `Settings`, `load_settings()`, every config section |
| `config.toml` | every default value (budgets, limits, timeouts, routes, prices, auth TTLs) |
| `verity/contracts/` | pydantic mirror of `frontend/src/lib/contracts/types.ts` and `view-types.ts`, API bodies, `CircuitGraph` |
| `verity/core/types.py` | one import point for `Rtl`, `TaskPack`, sandbox result models, protocols, `RunContext`, fitness, captions, errors |

**CPU Lab:** this file does not cover the CPU Lab interfaces. The CPU job wire models are in `verity/contracts/cpu.py` and `verity/contracts/cpu_profile.py` (mirrored in `frontend/src/lib/contracts/cpu.ts`), the `cpu_choice` reply is in `verity/contracts/api.py`, and the block and core packs are in `tasks/` (`rv32i_*`).

**Do not edit shared files.** If one is wrong, fix the smallest thing, say so in your return value, and keep the signature.

---

## 0. Ground rules for every builder

1. No em dash or en dash anywhere (code, comments, docs, copy). Use commas, colons, parentheses.
2. No secrets in code, config or logs. Read keys only through `Settings` (env names in section 10).
3. Typed Python 3.11+, pydantic v2, `async` wherever I/O happens, small modules.
4. Wire JSON is produced ONLY with `verity.contracts.to_wire(model)` / `to_json(model)` / `event_to_json(event)`. Never `model_dump()` without `by_alias=True`. Optional TS fields (`x?: T`) are omitted when `None`; nullable TS fields (`x: T | null`) are sent as `null`. This is already encoded in the models (`opt()` in `contracts/base.py`).
5. Python names are snake_case; the wire is camelCase (alias generator). Construct models with snake_case kwargs: `Candidate(parent_id=None, created_by="INIT", ...)`, `TestCase(pass_=False, ...)`.
6. Where behavior can only be known by running it, write your best implementation and mark it `# UNVERIFIED: <what to check>`. List every such mark in your return value.
7. No runtime testing in this build (no pip install, docker build, servers, network). `python -m compileall -q backend/verity` is allowed. You may write tests under `backend/tests/<area>/` without running them.
8. Only touch your own folders (section 1). Never touch `frontend/`.

---

## 1. Module ownership

| Builder | Owns (write only here) | Must export (exact names) |
|---|---|---|
| **A** | `sandbox/` (Dockerfile + `/verity/bin` scripts + `/verity/lib`), `tasks/`, `verity/evals/`, `evals/` (cassettes, results, report) | Image `verity-tools:0.1` with `/verity/bin/verify`, `/verity/bin/measure`, `/verity/bin/eqy`, `/verity/bin/bundle`, and since 2026-10-22 `/verity/bin/seeds`, `/verity/bin/strength`, `/verity/bin/wave` (with `/verity/lib/verity_sim.py`, `verity_wave.py`); `/verity/lib/verity_tb.py`; task packs `mac4`, `alu8`, `matmul4`, `add8`, `cnt8`, `fsm_seq` (live, must equal `frontend/src/lib/contracts/presets.ts`) and `mm4_int4` (`eval_only = true`); `mac4`, `add8`, `cnt8`, `fsm_seq` are knob families (`family.py`); `verity.evals.__main__:main` |
| **B** | `verity/llm/`, `verity/roles/` (+ `roles/prompts/*.md`) | `verity.llm.factory.build_provider(settings) -> LLMProvider`; `verity.llm.gateway.RunLLM(provider, settings, ledger)` implementing `LLMGateway`; `verity.llm.nebius.NebiusProvider`, `OpenAICompatProvider`; `verity.llm.fake.FakeProvider`; `verity.roles.planner.plan`, `verity.roles.rtl_gen.generate_rtl`, `verity.roles.repair.repair`, `verity.roles.optimizer.optimize`; `verity.roles.default_roles() -> Roles` |
| **C** | `verity/tools/` | `verity.tools.factory.build_tool_runner(settings) -> ToolRunner`; `verity.tools.local_docker.LocalDockerRunner`; `verity.tools.nebius_sandbox.NebiusSandboxRunner`; `verity.tools.parse.parse_stdout_json(stdout) -> dict` |
| **D** | `verity/orchestrator/`, `verity/runs/` | `verity.orchestrator.run_orchestrator(ctx: RunContext) -> None`; `verity.orchestrator.feasibility.check_plan(plan, pack, settings) -> list[str]`; `verity.runs.manager.RunManager(settings, store, provider, tools, packs)` implementing `RunService`; `verity.runs.ledger.RunLedger(limits, settings)` implementing `Ledger`; `verity.runs.runlog.build_run_log(run, events, settings) -> RunLog` |
| **E** | `verity/api/`, `verity/store/`, `verity/main.py` | `verity.api.app.create_app(settings, service) -> FastAPI`; `verity.store.sqlite.SqliteStore(settings)` implementing `Store`; `verity.main:main()` (console script `verity-api`) |

Composition (E writes it in `verity/main.py`, everyone relies on it):

```python
settings = load_settings()
problems = settings.production_problems()          # refuse to start if non-empty
store = SqliteStore(settings); await store.open()
packs = load_packs(settings.tasks_dir)              # fail fast on a broken pack
provider = build_provider(settings)                 # B
tools = build_tool_runner(settings)                 # C
service = RunManager(settings, store, provider, tools, packs)   # D, builds RunLLM + RunLedger per run
app = create_app(settings, service)                 # E; lifespan calls service.start() / service.shutdown()
uvicorn.run(app, host=settings.server.host, port=settings.server.port, workers=1)
```

Protocols and signatures: `verity/core/protocols.py`. Run context: `verity/core/context.py`.

---

## 2. Shared decisions every builder must know

| # | Decision |
|---|---|
| S1 | Task pack id == web `presetId`. Live packs: `mac4`, `alu8`, `matmul4` (fixed presets, matching `presets.ts` `PRESETS` exactly: title, text, ports, testCount, maxLines) and the bench knob families `mac4`, `add8`, `cnt8`, `fsm_seq` (knob ids, kinds, ranges and defaults matching `presets.ts` `FAMILIES` and each pack's `family.py` `KNOBS`). |
| S2 | Ports with `count > 1` are ONE flat Verilog vector of `width * count` bits; lane i is `p[i*W +: W]`. Flat lane vectors are declared unsigned; lane signedness lives in `element_type`. |
| S3 | Sandbox stage order inside `/verity/bin/verify` is **lint (Verilator), then interface check (Yosys JSON), then sim**. Verilator first gives the repair prompt the clearest syntax errors. |
| S4 | Sandbox scripts print exactly one JSON object as the LAST line of stdout. Everything else goes to stderr. |
| S5 | `maxLines` counts non-blank lines. RTL max 20,000 bytes, ASCII only. |
| S6 | Pack hashes are SHA-256 over file bytes with CRLF normalized to LF. Pack hash = SHA-256 of `MANIFEST.sha256`. Use `write_manifest(root)` to (re)generate. |
| S7 | `reference.v` never enters a sandbox for a run and never reaches a role. `FakeProvider` may use it as canned RTL, and the orchestrator measures it once per run for the `human_ref` event. |
| S8 | `ppa.powerMw` is an OpenSTA `report_power` estimate (section 3.6), `null` when it is not available (older records, failed estimate). Fitness drops a missing metric and renormalizes. Recorded `Fitness.weights` are the effective (renormalized) weights. |
| S9 | `seq` and `tMs` are stamped by the `EventSink`. Producers create events with the defaults (0). |
| S10 | Canvas `add_node` and `connect` ops use `by: "ai"`, `update_status` uses `by: "system"`, exactly like `golden.json`, so a recorded run is a drop-in fixture. (ARCHITECTURE 9.1 says `system` for all; flagged, golden.json wins because the web is built against it.) |
| S11 | `auth.required = false` by default: the current `frontend/src/lib/sources/live.ts` sends no token and no `?st=`. Anonymous session id = `"anon-" + sha256(client_ip)[:16]`. The production guard forces `required = true` (web change O5 needed first). |
| S12 | SSE is hand-rolled (Starlette `StreamingResponse`), not sse-starlette. |
| S13 | ToolRunner is batched: `verify` (lint + interface + sim), `measure` (synth + sta + circuit), `check_equivalence`. These replace the six functions sketched in ARCHITECTURE 11.3. |
| S14 | Per-candidate repair limit is checked by the orchestrator (`fix_count < max_repairs_*`); the ledger only keeps totals. |
| S15 | Captions come only from `verity/core/captions.py`. The one place model text appears is `captions.diagnosis()`, truncated and ASCII-cleaned. |
| S16 | Model ids: Ultra `nvidia/Nemotron-3-Ultra-550b-a55b`, Super `nvidia/nemotron-3-super-120b-a12b`. Unknown models are priced with `prices.fallback` (Ultra prices). |

---

## 3. Sandbox I/O contract (builder A writes the scripts, builder C calls them)

### 3.1 Image

- Tag `verity-tools:0.2` (config `tools.image`; 0.2 adds the RISC-V GCC, Embench-IoT and riscv-formal stage for `bench` and `rvfi`, see `sandbox/README.md`). Debian/Ubuntu base, Python 3.11+, Verilator 5.x, Icarus Verilog 12, cocotb pinned (recommend 1.9.2), Yosys, OpenSTA, Yosys EQY + SBY + one SMT solver, Nangate45 liberty downloaded at build time (do not commit it).
- Paths: scripts `/verity/bin/`, helpers `/verity/lib/` (on `PYTHONPATH`), liberty `$VERITY_LIBERTY` (default `/verity/pdk/nangate45/NangateOpenCellLibrary_typical.lib`).
- Runs as uid 1000, no network needed, writes only to `/work` and `/tmp`.

### 3.2 Inputs

| Path | Mode | Content |
|---|---|---|
| `$VERITY_PACK_DIR` (default `/pack`) | read-only | fresh pack copy from `TaskPack.copy_for_sandbox()`: `task.toml`, `golden.py`, `test_dut.py`, `vectors/` (no `reference.v`, no manifest) |
| `$VERITY_WORK_DIR/dut.v` (default `/work/dut.v`) | rw dir | the candidate RTL (`Rtl.text`). For `eqy`: the GOLD design (v1) |
| `/work/b.v` | rw dir | `eqy` only: the GATE design (the candidate being proven) |
| `/work/mutants.json` | rw dir | `strength` only: `{"mutants": [{"id": "m01", "text": "<whole file>"}]}` (section 3.10) |
| `/work/out/` | rw | scripts create it; `result.json` (same JSON as stdout) and logs |
| `/tmp` | tmpfs | build dirs (`/tmp/verity/...`) |

Environment (fixed names, values from `[timeouts]`): `VERITY_LINT_TIMEOUT_S`, `VERITY_SIM_TIMEOUT_S`, `VERITY_SYNTH_TIMEOUT_S`, `VERITY_STA_TIMEOUT_S`, `VERITY_EQY_TIMEOUT_S`. Optional: `VERITY_PACK_DIR`, `VERITY_WORK_DIR`, `VERITY_LIBERTY`. Per call, set by the runner for one script only: `VERITY_SEEDS` (`seeds`), `VERITY_STRENGTH_BUDGET_S` and `VERITY_MUTANT_TIMEOUT_S` (`strength`, from `[signoff]`), `VERITY_WAVE_CASE` (`wave`; also `VERITY_WAVE_BEFORE` and `VERITY_WAVE_AFTER`, default 4).

Scripts in the image since 2026-10-22: `verify`, `measure`, `eqy`, `bundle`, and for the sign-off and waveforms `seeds` (3.9), `strength` (3.10), `wave` (3.11). An older image without them makes those calls fail with an infrastructure error; the run itself is never affected (section 6.3).

### 3.3 Invocation (builder C; fixed templates, no model text in any argument)

Local Docker (`LocalDockerRunner`), one container per call, per-call temp dir on the host holding `dut.v` (and `b.v`) plus a fresh pack copy:

```
docker run --rm --name verity-<12 hex> --network none --read-only
  --tmpfs /tmp:rw,exec,size=512m
  --memory 2g --cpus 2 --pids-limit 256 --user 1000:1000
  --cap-drop ALL --security-opt no-new-privileges
  -v <host_work>:/work:rw -v <host_pack>:/pack:ro
  -e VERITY_LINT_TIMEOUT_S=20 -e VERITY_SIM_TIMEOUT_S=60 ... 
  verity-tools:0.1 /verity/bin/verify
```

Run with `asyncio.create_subprocess_exec` (argument list, never a shell string). Outer timeout = inner budget + `timeouts.sandbox_overhead_s` (verify: lint + sim; measure: synth + sta; eqy: eqy). On outer timeout: `docker kill verity-<id>`, then return a result with `status: "timeout"` (not an exception).

Nebius Sandboxes (`NebiusSandboxRunner`), if the API cannot mount files: `/verity/bin/bundle <verify|measure|eqy|seeds|strength|wave>` reads an uncompressed tar from stdin with members `work/dut.v`, optional `work/b.v` or `work/mutants.json`, and `pack/<files>`. It extracts the pack to `/tmp/verity/pack`, makes it read-only (`chmod -R a-w`), sets `VERITY_PACK_DIR`, writes `work/*` into `$VERITY_WORK_DIR`, then runs the stage script and passes its stdout and exit code through. `# UNVERIFIED: Sandboxes file upload, stdin, image import and timeout APIs (M0).`

### 3.4 Exit codes

| Code | Meaning | Runner action |
|---|---|---|
| 0 | Script finished and printed a result (the design may still have failed) | parse last stdout line |
| 2 | Input error (missing `dut.v`, bad `task.toml`); JSON with `status: "input_error"` printed if possible | raise `ToolInfraError`, no retry |
| 3 | Internal error (tool missing or crashed unexpectedly); JSON with `status: "internal_error"` if possible | retry `tools.infra_retries` times, then `ToolInfraError` |
| 124 / killed | Outer timeout | synthesize `status: "timeout"` result |
| other | Docker or sandbox failure | retry, then `ToolInfraError` |

Each script enforces its own stage deadlines with `subprocess` timeouts and reports `status: "timeout"` with exit 0 when it catches one itself. Logs inside the JSON are capped (lint log first 4,000 chars; sim, synth, sta logs last 4,000; eqy log last 8,000) so stdout stays under the 64 KB sandbox cap.

### 3.5 `/verity/bin/verify` (lint, interface, sim; early exit)

Steps:
1. **Lint**: `verilator --lint-only -Wall -Wno-fatal -Wno-DECLFILENAME --top-module <top> /work/dut.v`. `ok` = exit code 0 and no `%Error` line. `warnings` = count of `%Warning-` lines. Fail: `status: "lint_fail"`, stop.
2. **Interface**: `yosys -q -p "read_verilog -sv /work/dut.v; hierarchy -check -top <top>; proc; write_json /tmp/verity/ports.json"`. Compare `modules[<top>].ports` against `task.toml` ports: same name set, same direction (`input` -> `in`, `output` -> `out`; `inout` is always an error), `len(bits) == width * count`, and for `count == 1` ports the same signedness. One error string per mismatch, e.g. `"port acc: width 16, expected 22"`. Fail: `status: "contract_fail"`, stop. `# UNVERIFIED: key Yosys uses for port signedness in write_json.`
3. **Sim**: cocotb with Icarus (`cocotb.runner.get_runner("icarus")`, `hdl_toplevel=<top>`, `test_module="test_dut"`, build dir `/tmp/verity/sim_build`, `timescale=("1ns","1ps")`), env `PYTHONPATH=$VERITY_PACK_DIR:/verity/lib`, `VERITY_SIM_RESULT=/work/out/sim_result.json`, `VERITY_SEED=<task.toml sim.seed>`, `VERITY_MAX_CYCLES=<sim.max_cycles_per_case>`, `VERITY_TEST_COUNT=<test_count>`. Read `sim_result.json` (section 4). `status` = `pass` when `passed == total == test_count`, else `sim_fail`. Compile error or missing result file: `sim_error` with `message` = first error line.

Stdout (last line), matching `verity.core.results.VerifyResult`:

```json
{
  "schema": "verity.verify/1",
  "status": "pass | contract_fail | lint_fail | sim_fail | sim_error | timeout | input_error | internal_error",
  "lint":     {"ok": true, "errors": 0, "warnings": 1, "log": "%Warning-WIDTH ...", "tool": "Verilator 5.028"},
  "contract": {"ok": true, "errors": [], "ports": [{"name": "a", "dir": "in", "width": 32, "signed": false}]},
  "sim": {
    "ok": false, "total": 16, "passed": 13, "failure_count": 3,
    "failures": [{"name": "acc_16_cycles_max", "inputs": "a=[255,255,255,255] b=[255,255,255,255] x16",
                  "expected": "0x3F8040", "got": "0x008040", "pass": false, "cycle": 16}],
    "seed": 1, "log": "...", "tool": "iverilog 12.0 + cocotb 1.9.2"
  },
  "duration_ms": 2140,
  "message": "",
  "tool_versions": {"verilator": "5.028", "yosys": "0.45", "iverilog": "12.0", "cocotb": "1.9.2"}
}
```

Sections not reached are `null`. `lint` also carries `by_tag` (warnings per Verilator tag, for example `{"UNUSEDSIGNAL": 1, "WIDTHEXPAND": 2}`) and `latches` (the `LATCH` count); both are absent from older images (`by_tag` then reads `{}`, `latches` `null`). `failures` holds at most 5 cases, in case order; `failure_count` is the full count. Mapping to the wire `Verification` is `VerifyResult.to_verification(pack.test_count)` (already written): `contract_fail` and `lint_fail` become `lint: "fail"` with the log in `lintLog`; a vector-count mismatch can never pass.

### 3.6 `/verity/bin/measure` (synth, timing, circuit)

Steps:
1. **Circuit** (before tech mapping): `read_verilog -sv /work/dut.v; hierarchy -check -top <top>; proc; opt; write_json /tmp/verity/pre.json`, then build the `CircuitGraph` (section 3.8).
2. **Synth**: `synth -top <top> -flatten; dfflibmap -liberty $LIB; abc -liberty $LIB; opt_clean; stat -liberty $LIB; write_verilog -noattr /work/out/netlist.v`. `area_um2` = "Chip area" from `stat`; `cell_count` = number of cells; `cells` = per-cell counts.
3. **Timing** (OpenSTA, post-synthesis, no wires): read liberty and `netlist.v`, `link_design <top>`; clocked design: `create_clock -name clk -period <target_clock_ns> [get_ports <clock>]`; combinational design (`clock.name == ""`): virtual clock `create_clock -name vclk -period <target_clock_ns>`; `set_input_delay 0` on all inputs except the clock and `set_output_delay 0` on all outputs; worst setup slack `wns_ns`; `clock_period_ns = target_clock_ns - wns_ns` (the minimum period). `# UNVERIFIED: OpenSTA command names and report parsing.`

4. **Power** (OpenSTA `report_power`, same netlist and constraints as step 3, a separate `sta` run so a power failure never fails timing): `set_power_activity -input -activity 0.1 -duty 0.5`, then `report_power -digits 6`; the `Total` row (Watts) is converted to mW. Method `vectorless`: every non-clock input toggles 0.1 times per cycle, activity propagates through the logic, so it is a relative figure for comparing a candidate with v1, not a measurement of a real workload. The clock is fixed to the pack's `target_clock_ns` (NOT the measured minimum period, which differs per candidate), so v1 and every child are measured identically. Estimate only (open-source tools, Nangate45 typical liberty, no wires or clock tree), not signoff. Simulation-derived activity (VCD/SAIF) is not used: the measure stage does not run the testbench, and RTL signal names do not map onto the gate-level netlist.

Stdout (last line), matching `MeasureResult`:

```json
{
  "schema": "verity.measure/1",
  "status": "ok | synth_fail | sta_fail | timeout | input_error | internal_error",
  "synth": {"ok": true, "area_um2": 1840.2, "cell_count": 412, "cells": {"NAND2_X1": 40}, "log": "...", "tool": "Yosys 0.45"},
  "sta":   {"ok": true, "clock_period_ns": 3.2, "wns_ns": 6.8, "target_period_ns": 10.0, "clocked": true, "log": "...", "tool": "OpenSTA 2.6"},
  "power": {"ok": true, "power_mw": 1.870394, "internal_mw": 1.037427, "switching_mw": 0.784294, "leakage_mw": 0.048673, "method": "vectorless", "input_activity": 0.1, "reference_clock_ns": 10.0, "log": "...", "tool": "OpenSTA 3.1.0"},
  "circuit": { "format": "verity.circuit/1", "candidateId": "", "top": "mac4", "...": "see 3.8" },
  "pdk": "Nangate45",
  "duration_ms": 18000,
  "message": "",
  "tool_versions": {"yosys": "0.45", "opensta": "2.6"}
}
```

Two evidence fields for the optimizer (section 6.2 step 10), both optional and absent from older images: `synth.cell_area` (`{cell: total um2}`, from the `stat -liberty` table) and `sta.path` (`{"startpoint": "acc[3]", "endpoint": "acc[20]", "start_kind": "register", "end_kind": "register", "depth": 32, "delay_ns": 1.556}`: the worst path of `report_checks`, a register end named by the net on its flip-flop's Q pin in the netlist, `depth` = combinational cells on the path, kinds `register | input | output | other`). A malformed `path` becomes `null` and never fails the measurement.

A candidate is **measured** when `synth.ok` and `area_um2` is present (`MeasureResult.measured`). `sta_fail` keeps the area and sends `clockPeriodNs: null`. `power` is optional (absent from older results); when `power.ok` is false the PPA sends `powerMw: null` and the status stays `ok`. `synth_fail` means rejected (child) or run error (baseline). PPA mapping: `MeasureResult.to_ppa()`.

### 3.7 `/verity/bin/eqy`

Gold = `/work/dut.v` (v1), gate = `/work/b.v` (candidate). Config written to `/tmp/verity/eqy/verity.eqy`:

```
[gold]
read_verilog -sv /work/dut.v
prep -top <top>

[gate]
read_verilog -sv /work/b.v
prep -top <top>

[strategy sby]
use sby
depth <task.toml eqy.depth>
engine smtbmc <solver in the image>
```

Run `eqy -f verity.eqy -d /tmp/verity/eqy/out` under `VERITY_EQY_TIMEOUT_S`. Status (first run, 2026-10-06): the script first runs a reset-aware miter (sby: reset asserted in cycle 0, outputs compared from cycle 1, engines `abc pdr` and `smtbmc yices`): PASS -> `proven`, FAIL -> `not_equivalent` (a real trace from reset). Otherwise EQY runs: all partitions proven -> `proven`; a failed partition -> `not_equivalent` only for a design without a reset (no hidden state), else `inconclusive` (EQY starts from any register state, so its counterexample may be unreachable); unknown partitions -> `inconclusive`; own deadline hit -> `timeout`; tool crash -> `error`. `# UNVERIFIED: eqy summary lines and exit codes, and whether the sby strategy proves (induction) or only bounds.`

```json
{"schema": "verity.eqy/1", "status": "proven | not_equivalent | inconclusive | timeout | error",
 "log": "last 8000 chars", "duration_ms": 41000, "message": "", "tool": "yosys-eqy", "tool_versions": {"eqy": "0.45"}}
```

Mapping: `EquivalenceResult.to_equivalence(against_id)`: `timeout` and `error` become `inconclusive`, never `proven`. If `task.toml eqy.enabled = false`, the orchestrator does not call it and records `not_run_equivalence(...)`.

### 3.8 CircuitGraph (built in the sandbox from `pre.json`)

Model: `verity/contracts/circuit.py`. Cell classes: `$add $sub $neg $alu $macc` -> `add`; `$mul` -> `mul`; `$mux $pmux $bmux $demux` -> `mux`; `$eq $ne $lt $le $gt $ge $eqx $nex $logic_*` compare family -> `cmp`; any `$*dff*` / `$*latch*` -> `reg`; non-`$` cell types -> `submodule`; everything else -> `logic`; module ports -> `port`. At most 60 nodes: merge cells of the same kind and width that feed the same targets into one node labeled `"<kind> x<n>"`, else drop the remainder and set `truncated: true`. Edges carry the bit width. No coordinates. `candidateId` is `""`; the backend fills it (`MeasureResult.circuit_for(id)`). An invalid or oversized graph becomes `null` without failing the measurement.

### 3.9 `/verity/bin/seeds` (sign-off: more seeds, lint by tag)

Input: the winner at `/work/dut.v`, `VERITY_SEEDS` = comma separated integers (1 to 16; the runner sends `[signoff] extra_seeds` minus the pack's own seed). The pack is not changed: the seed reaches the testbench through `VERITY_SEED` and `COCOTB_RANDOM_SEED`, exactly as `verify` passes `task.toml [sim] seed` (every `test_dut.py` seeds its `random.Random` from `VERITY_SEED`). Steps: lint once (same Verilator command as `verify`), compile once (`iverilog`), then one `vvp` run per seed, two at a time, each under `VERITY_SIM_TIMEOUT_S`. A seed passes only when the full `test_count` ran and passed (the parser re-checks against the pack).

```json
{"schema": "verity.seeds/1", "status": "pass | sim_fail | lint_fail | sim_error | timeout | input_error | internal_error",
 "lint": {"ok": true, "errors": 0, "warnings": 2, "by_tag": {"UNUSEDSIGNAL": 1, "WIDTHEXPAND": 1}, "latches": 0, "log": "...", "tool": "Verilator 5.020"},
 "seeds": [{"seed": 2, "ok": true, "total": 16, "passed": 16, "failure_count": 0, "failures": [], "timed_out": false, "message": ""}],
 "duration_ms": 11600, "message": "", "tool_versions": {}}
```

`failures` holds the first failing case of that seed only. Outer timeout: lint + sim x ceil(seeds / 2) + 10 s + overhead.

### 3.10 `/verity/bin/strength` (sign-off: planted bugs)

Input: `/work/mutants.json` (1 to 40 entries, ids `[A-Za-z0-9_]{1,16}`, each text at most 20 KB) and `/work/dut.v` (the design they came from). The backend plants the mutants with fixed rules and no model (`verity/core/mutants.py`, one edit each, every mutant passes the same RTL rules as a model's design): `drop_reset`, `invert_enable`, `flip_compare`, `off_by_one`, `flip_operator` (a binary `+ - & | ^ && ||` swapped; without it a one-line datapath such as `a + b + cin` would get no mutant), `const_plus_one`, `swap_operands`; taken round robin over the rules and spread over each rule's places, at most `[signoff] max_mutants` (20). Each mutant is compiled and simulated with the pack's seed, two at a time, each under `VERITY_MUTANT_TIMEOUT_S`, all under `VERITY_STRENGTH_BUDGET_S`.

```json
{"schema": "verity.strength/1", "status": "ok | timeout | input_error | internal_error",
 "mutants": [{"id": "m01", "status": "caught | survived | invalid | not_run", "detail": "count_up", "passed": 1, "total": 10, "duration_ms": 1900}],
 "duration_ms": 21000, "message": "", "tool_versions": {}}
```

`caught`: the locked tests fail on it (a failing case, a crash, or a timeout: the design would be rejected). `survived`: every locked test passes. `invalid`: it does not compile; not a planted bug, not counted. `not_run`: the budget ran out first; not counted.

### 3.11 `/verity/bin/wave` (one test case as a waveform)

Input: a design at `/work/dut.v`, `VERITY_WAVE_CASE` = a case name (empty: the first case, a directed one in every pack). One extra simulation: the script compiles the design with a dump module Verity writes (`module verity_wave_dump; initial begin $dumpfile(...); $dumpvars(1, <top>); end endmodule`, a second top level, the cocotb recipe for Icarus), so `$dump*` stays banned in model RTL. `verity_tb.run_cases` runs in wave mode (section 4): every case up to the target in order, so the random generator is consumed exactly as in a normal run, then for a clocked design `VERITY_WAVE_AFTER` more falling edges with the last inputs held. The VCD is cut to a window: about `VERITY_WAVE_BEFORE` samples before and `VERITY_WAVE_AFTER` after the first mismatch (a passing case: its first 12 samples). Samples are taken where the testbench drives and checks: every falling clock edge, or for a design with no clock every 1 ns vector step. Signals: the ports in `task.toml` order (not the clock), then up to 4 internal signals of the top module that only change on the rising clock edge (registers, or logic fed only by registers).

Expected values come from the pack's `golden.py` replayed on the sampled inputs (cnt8, fsm_seq, add8 per cycle; mac4 by its protocol: `done` at every sample after reset, `acc` after reset and from `done` on). The replay is shown only when it agrees with the test's own verdict (no difference before the failing sample, at least one at it; for a passing case no difference at all). Otherwise, and for packs without a replay, `expected` holds only the values the failing check names (`q=0x05`) at the failing cycle, and `note` says so.

```json
{"schema": "verity.wave/1", "status": "ok | no_case | compile_error | sim_error | timeout | input_error | internal_error",
 "wave": {"case": "count_up", "fail_cycle": 4, "start_cycle": 0,
          "signals": [{"name": "q", "width": 8, "got": ["0x00", "0x01", "0x02", "0x03", "0x05"], "expected": ["0x00", "0x01", "0x02", "0x03", "0x04"]}],
          "note": "Expected values come from the golden model, replayed on the same inputs. ..."},
 "duration_ms": 1500, "message": "", "tool_versions": {}}
```

Cycle numbers: the failing sample carries the test's own `cycle` (from the failure record) and the others count from it; without one, the sample index from the start of the case. Values: one bit `"0"`/`"1"`, wider `"0x0A"` (one hex digit per 4 bits, as the testbench prints them), `"x"` when any bit is X or Z; `""` in `expected` where nothing is expected. Rows of different lengths are dropped by the parser (status `sim_error`).

---

## 4. cocotb testbench contract (builder A)

- `golden.py`: pure Python reference model. No I/O, no cocotb import, no randomness without an explicit seed argument. Function names are pack-defined.
- `test_dut.py`: exactly ONE cocotb test, `@cocotb.test() async def run_all(dut)`, that runs exactly `test_count` named cases (directed cases first, then seeded random ones from `random.Random(int(os.environ["VERITY_SEED"]))`). It resets the DUT before each case, never stops at the first failure, records every case, then writes the result file and finally asserts all passed.
- It finds the golden model with a plain `import golden` (the pack directory is on `PYTHONPATH`) and the helper with `from verity_tb import Recorder, read_int, fmt_hex` (`/verity/lib/verity_tb.py`, builder A).
- Each case is bounded by `VERITY_MAX_CYCLES` clock cycles; waiting longer records a failure (`got: "no done after N cycles"`).
- Latency is checked: a case that expects `done` at cycle 18 fails if it arrives at 17 or 19 (golden.json examples).
- X or Z on an output is a failure with `got: "X"` (`read_int` returns `None`).
- Values are formatted as hex with the output width (`fmt_hex(value, bits)` -> `"0x3F8040"`); `inputs` is a short human description (max 200 chars).

`Recorder` writes `$VERITY_SIM_RESULT`:

```json
{
  "schema": "verity.sim/1",
  "top": "mac4",
  "total": 16,
  "passed": 13,
  "failure_count": 3,
  "failures": [
    {"name": "acc_carry_boundary", "inputs": "a=[64,0,0,0] b=[64,0,0,0] x16",
     "expected": "0x010000", "got": "0x000000", "pass": false, "cycle": 16}
  ],
  "cases": [{"name": "acc_zero", "pass": true}],
  "seed": 1
}
```

`failures`: first 5 failing cases in case order. `cases`: every case name with pass/fail, no values. `verify` copies `total`, `passed`, `failure_count`, `failures`, `seed` into its `sim` section and cross-checks `total` against `task.toml test_count`.

Pack self-test (evals `packs` suite, builder A): `reference.v` passes; at least one mutant (wrong width, off-by-one done, missing reset) fails. Since 2026-10-22 it also checks the sign-off tools per pack: the reference passes one more seed (`seeds`), the reference's first case gives a waveform with no failure, and the first failing mutant's first failing case gives a waveform that names the failing cycle (`wave`).

Wave mode (`verity_tb.py` only, outside every pack hash; `test_dut.py` files are unchanged): when `VERITY_WAVE_TRACE` is set, `run_cases` runs the cases in order up to `VERITY_WAVE_CASE` (default the first), records each case's start and end simulation time (ns) and its verdict (`ok`, `expected`, `got`, `cycle`), lets `VERITY_WAVE_AFTER` more falling edges of `VERITY_WAVE_CLOCK` pass after the target case, and writes them to the trace file. It writes no result file and does not assert. Without `VERITY_WAVE_TRACE` nothing changes.

---

## 5. `task.toml` schema (builder A writes packs, `TaskPack` parses them)

Model: `verity/core/pack.py` (`extra = "forbid"`: unknown keys are errors). Example for the showcase preset, consistent with `presets.ts` `mac4`:

```toml
schema = "verity.task/1"
id = "mac4"                         # == folder name == web presetId
title = "4-lane MAC array, 8-bit"
summary = "4 lanes of 8-bit multiply-accumulate over 16 cycles"   # used in the Spec caption
spec_text = "A 4-lane multiply-accumulate unit. Each cycle it multiplies four pairs of unsigned 8-bit inputs and adds the four products to a running sum. After 16 cycles it raises done and holds the 22-bit result until reset. Smallest area matters most."
top = "mac4"
family = "mac"
element_type = "uint8"              # int4 | uint4 | int8 | uint8 | none
latency_cycles = 16                 # pack-defined meaning; testbench enforces it exactly; optimizers must keep it
max_lines = 300
test_count = 16                     # exact number of cases test_dut.py runs
default_priority = "area"
priorities = ["area", "speed", "power"]
target_clock_ns = 10.0              # STA reference period
max_plan_blocks = 8
eval_only = false
coding_rules = []                   # extra pack rules appended to CODING_RULES

[clock]
name = "clk"                        # "" for combinational

[reset]
name = "rst_n"                      # "" for none
active_low = true
synchronous = true

[sim]
seed = 1
max_cycles_per_case = 64

[eqy]
enabled = true
depth = 24

[budget]                            # optional; may only LOWER [budget.run] values
# max_candidates = 4

[[ports]]
name = "clk"
dir = "in"
width = 1

[[ports]]
name = "rst_n"
dir = "in"
width = 1

[[ports]]
name = "start"
dir = "in"
width = 1

[[ports]]
name = "a"
dir = "in"
width = 8
count = 4                           # flat vector [31:0], lane i = a[i*8 +: 8]

[[ports]]
name = "b"
dir = "in"
width = 8
count = 4

[[ports]]
name = "acc"
dir = "out"
width = 22

[[ports]]
name = "done"
dir = "out"
width = 1
```

Pack folder: `task.toml`, `golden.py`, `test_dut.py`, optional `vectors/`, `reference.v`, and `MANIFEST.sha256` (lines `<sha256>  <relative path>`, every file except the manifest, LF-normalized hashes). Generate it with `from verity.core.types import write_manifest; write_manifest(Path("tasks/mac4"))`.

The LLM sees only `TaskPack.public_brief()` (title, spec text, module header, latency, limits, coding rules).

---

## 6. Orchestrator to events (builder D)

Reference stream: `frontend/public/runs/golden.json` (140 events). Match its shapes and ordering. Everything below is emitted through `ctx.sink.emit(ctx.run_id, event)`.

### 6.1 Canvas ids, zones, edges

| Thing | Node id | kind | zone | label | refId |
|---|---|---|---|---|---|
| Spec | `spec` | `spec` | `plan` | `Spec` | `spec` |
| Plan block | `blk-<slug>` (normalize the model's ids: lowercase, `[a-z0-9-]`, unique, prefix `blk-`) | `block` | `plan` | block name | same id |
| Golden model | `golden` | `golden` | `plan` | `Golden model` | `golden` |
| Candidate n | `c<n>` (creation order, from 1) | `candidate` | `tree` | `v<n>` | `c<n>` |

| Edge | source -> target | kind | label |
|---|---|---|---|
| Plan chain | `spec` -> first block, block -> next block | `plan` | none |
| Checks | `golden` -> `c1` | `checks` | `checks every version` |
| Lineage | parent -> child | `lineage` | none |
| Proof | winner -> `c1`, only when `proven` | `proof` | `Proven equal (EQY)` |

Node statuses via `update_status` (by `system`): blocks `replanning` -> `drafted` (note `Changed: <what>`) -> `approved` (gate closed) -> `built` (v1 measured); golden `pending` -> `locked`. Candidate node status follows `candidate` events automatically (the reducer syncs it); do not send `update_status` for candidates.

Order rule: `add_node` before `connect`, and both before the first `candidate` event of that id.

### 6.2 Stage script

`[stage]` means `StageEvent`; `[cand cN status]` means a full `CandidateEvent` upsert; captions name the template in `core/captions.py` and their chapter.

1. **spec**: `run running`; `human {kind: spec, tMs: ctx.now_ms(), presetId, priority}`; `spec` (`pack.to_spec(priority)`); artifact `spec/rules.json` (section 6.3); `add_node spec`; `[stage spec done]`; caption `spec_read` (Spec).
2. **plan**: `[stage plan active]`; caption `drafting_plan` (Plan); `roles.plan(...)`; credits; artifact `plan/plan.r1.json`; `plan {revision 1, blocks}` (no diff); per block: `add_node blk-*`, `connect` (plan chain); `gate {open, autoContinueMs: plan.gate_auto_continue_ms}`; caption `plan_drafted`. Set `ctx.gate_state`.
3. **gate loop** (until auto-continue elapses, `build_now`, or `max_hold_s` while held):
   - `pin` / `toggle`: `human` echo (PinInput / ToggleInput with `ctx.now_ms()`); `update_status blk-* replanning`; caption `pinned` / `toggled`; if replans left (`plan.max_replans`): `roles.plan(previous=..., steering=[all pins/toggles so far])`, else apply the pin/toggle to the blocks in code (no LLM). Then `plan {revision n+1, blocks, diff, reason}` (diff computed in code: added/changed/removed block ids); `update_status ... drafted` with note; `add_node`/`remove` for added/removed blocks; `gate {open, autoContinueMs: plan.gate_after_replan_ms}`; caption `plan_updated`.
   - `gate hold` (or `autopilot` off): `gate {held, autoContinueMs: <remaining>}`; caption `gate_held`. `resume` (or `autopilot` on): `gate {open, autoContinueMs: <remaining>}`. `build_now`: close now. Gate steers have no `human` echo (HumanInput has no gate kind).
   - Close: `gate {closed, autoContinueMs: 0}`.
4. **feasibility** (`check_plan` returns reasons; empty = ok): ok -> `[stage plan done]`, `update_status blk-* approved`. Fail -> `[stage plan failed, detail=<first reason>]`, caption `plan_rejected`, `run error {message}`.
5. **lock**: `[stage lock active]`; `add_node golden`; `update_status golden pending`; `pack.reverify()` (PackIntegrityError -> `[stage lock failed]`, run error); artifacts `tests/golden_model.py` (= `pack.golden_source()`) and `tests/test_dut.py`; `update_status golden locked`; `[stage lock done]`; caption `locked` (Lock).
6. **rtl**: `[stage rtl active]`; `add_node c1`; `connect golden->c1 checks`; `[cand c1 drafting]` (attempts empty, rtl ""); caption `writing_v1` (Build and fix); `roles.generate_rtl` (R4: with `[baseline] samples` > 1 it is called that many times in parallel at `[baseline] temperature` and `top_p`, each sample gets the declaration fixer and one sandbox check with no events, samples that still fail lint are dropped, the one passing the highest fraction of tests becomes attempt 1 of c1, and caption `sampled` follows; if none passes lint the one with the fewest lint issues continues; one failed sample does not end the run, all failing does; `samples = 1` is the single call path); credits; `[stage rtl done]`.
7. **verify loop for c1** (one sandbox call per attempt):
   - validate: `pack.validate_rtl(text)`; `RtlRejected` -> attempt with `Verification(lint="fail", lintLog="Rejected before simulation:\n- ...", passed=0, total=test_count, tool="Verity RTL rules", durationMs=0)` and go to fail handling (caption `rejected_rules`).
   - `[stage lint active]`; `[cand c1 linting]` (rtl = the new text); `tools.verify`; then `[stage lint done|failed]`; if lint passed: `[stage sim active]`, `[cand c1 simulating]`, caption `simulating`, then the result.
   - append `Attempt(n, by, reason, rtl, verification)`; candidate `verification` = that verification.
   - PASS: `[stage sim done]`; if fixes happened `[stage fix done, detail "<k> fix" / "<k> fixes"]`; `[cand c1 pass, isBaseline true]`; caption `baseline_passes`.
   - FAIL: `[cand c1 fail]`; `[stage sim failed]` (or lint failed); caption `failed_tests` / `failed_lint`; if `fix_count < max_repairs_baseline` and budget allows: for a test failure (`sim_fail` with a failing case) and `[signoff] waves_on_repair`, one `tools.waveform(rtl, pack, <first failing case>)` call and artifact `waves/c1.json` (section 6.3; no waveform on lint, rule or interface failures, or when the call fails); `[stage fix active]`; `[cand c1 fixing]`; `roles.repair(..., waves=<the trace>)` (prompt `repair.v4.md`: the failing cases, then the cycle table around the first mismatch; a failing child also gets `parent_rtl`, its passing parent, after the evidence); credits; set `diagnosis` on the failed attempt's verification (re-upsert); caption `diagnosis`; `fix_count += 1`; next attempt has `by: "FIX"`. Otherwise: caption `no_baseline`, `run error "no passing baseline"`.
8. **synth (v1)**: `[stage synth active]`; `[cand c1 synthesizing]`; `tools.measure`; `ppa = to_ppa()`; `fitness = compute_fitness(ppa, ppa, priority, "c1")` (score 0); `[cand c1 measured]`; `[stage synth done]`; `update_status blk-* built`; caption `baseline_measured`; artifacts `reports/synth_v1.txt`, `reports/sta_v1.txt`, `circuit/c1.json`. Synth failure on v1: caption `baseline_synth_failed`, run error.
9. **fast mode** stops here: `[stage evolve skipped]`, `[stage prove skipped]`, `winner {c1, ai, aiPickId c1}`, then step 12.
10. **evolve round r** (r = 1, then 2 if `max_rounds >= 2`, round 1 beat v1 or `round2_requires_improvement = false`, and budget allows):
    - `[stage evolve active, detail "R<r> of <max_rounds>", round r]`; `round {round r, parents}`; caption `round_started` (Round 1 / Round 2).
    - Round 1: parent `c1`, children `round1_children` with strategies `round1_strategies` in order. Round 2: parents = current elites (best first), `round2_children` children assigned round-robin; strategy = the parent's `createdBy` (OPTIMIZE if the parent is INIT).
    - Per child, in creation order: `ledger.count("candidate")`; `add_node cN`; `connect parent->cN lineage`; `[cand cN drafting, round r, parentId, createdBy]`. Then generate all children in parallel (`asyncio.gather`), each followed by its own verify, repair (`max_repairs_child`, with the waveform of step 7) and measure. The optimizer call gets `tried` (the other finished versions of the run, not v1 and not the parent: strategy, own reason, area and clock against v1 or "rejected"; empty in round 1 because those children run at once) and `parent_measure` (prompt `optimize.v4.md`): the parent's 6 largest cell types by area, its flip-flop count and area, and its critical path (start, end, logic depth, delay) from `synth.cell_area` and `sta.path` (section 3.6), else parsed from the parent's synth and STA logs. During evolve, no per-candidate `stage` events for lint/sim/fix/synth: only `candidate` events and captions (`child_failed`, `child_rejected`, `child_duplicate`, `child_synth_failed`, `child_measured`). When the children of a round have settled, a version whose cell count, area and clock period equal an earlier measured version's (v1 included) gets the reason `Same result as vN` (section 7; status and score unchanged).
    - Duplicate RTL (same `Rtl.sha256` as any earlier attempt in the run): `[cand cN rejected]`, no sandbox call.
    - After all children settle: `elites = select_elites(all candidates, evolution.elites)`; `round {round r, parents, elites}`; re-upsert every candidate whose `isElite` changed; caption `elites_kept`.
11. **winner and prove**: `ai = pick_winner(...)` (section 7: only a version no worse than v1 on the goal metric is eligible); `winner {candidateId: ai, by: "ai", aiPickId: ai}`; `[stage evolve done]`; caption `winner` (or `baseline_wins`). If winner is `c1`: `[stage prove skipped, detail "v1 is the winner"]`. Else `[stage prove active]`; caption `proving` (Prove); `tools.check_equivalence(v1, winner)`; upsert winner with `eqy`; `proven` -> `connect winner->c1 proof "Proven equal (EQY)"`, caption `proven`; `inconclusive` -> caption `not_proven`, winner stands; `not_equivalent` -> winner `rejected` (fitness null, isElite false), caption `not_equivalent`, next best becomes winner (`winner` event again), prove again, at most `max_prove_attempts`, then `c1`. Artifact `reports/eqy_<label>_vs_v1.log`. `[stage prove done]`.
12. **end**: `human_ref` stage (the human reference proof); `credits`; artifact `reports/summary.json`; `run done` (or `cap_reached` with `budget_reached` caption first); caption `done` (Done) AFTER the run event, as in golden.json. Set `ctx.outcome`.
13. **sign-off** (starts after step 12, once the result is out, so it neither delays the result nor competes with the human reference proof; section 6.3): wait for it (at most `[signoff] max_s`, and never past the watchdog's wall-clock cutoff), then artifact `waves/<winner>.json`, artifact `signoff/<winner>.json`, caption `signoff` (Done). Then the orchestrator returns and the live stream closes. The stream therefore ends with a Done-chapter caption in both cases.

Budget: `BudgetExhausted` from any role call means: no new LLM calls; finish verifying and measuring what is in flight; pick the winner among measured; still prove if the tool budget allows; terminal phase `cap_reached` with `message` naming the limit. Wall clock over `max_wall_s` behaves the same.

Credits: after every LLM call, `credits {usedUsd: round(ledger.snapshot().usd, 4), capUsd: ledger.limits.max_usd}`.

Artifact events: content capped at `sse.max_event_bytes - 1024` bytes, with `\n[truncated]` appended when cut. Paths: `tests/golden_model.py`, `tests/test_dut.py`, `plan/plan.r<n>.json`, `reports/synth_<label>.txt`, `reports/sta_<label>.txt`, `reports/eqy_<label>_vs_v1.log`, `reports/summary.json`, `circuit/<cid>.json`, `spec/rules.json`, `waves/<cid>.json`, `signoff/<cid>.json` (section 6.3). The web zip export takes `tests/` and `reports/`.

Disk (via `store.write_run_file`, ARCHITECTURE 15): `spec.md`, `plan.r<n>.json`, `candidates/<cid>/attempt-<n>.v`, `attempt-<n>.verify.json`, `attempt-<n>.llm.json` (raw model text + CallMeta; for a deterministic fix `meta.model` is `verity-declaration-fixer` and `raw` is empty), `candidates/c1/samples.json` (only when baseline sampling ran: per sample `lintOk`, `passed`, `total`, `fixerChanges`, `outputTokens`, `reasoningTokens`, `finishReason`, plus `chosen`), `final/llm-calls.json` (one entry per model call: role, model, temperature, topP, input, output and reasoning tokens, `finishReason`, `maxOutputTokens`, usd; `finishReason` `length` means the call was cut at the output cap), `attempt-<n>.autofix.json` (only when the declaration fixer changed attempt n: `fromAttempt`, `lintIssues`, `changes`), `stall-<n>.json` (only when a stalled baseline was restarted from the plan: `signature`), `ppa.json`, `synth.log`, `sta.log`, `circuit.json`, `attempt-<n>.waves.json` (the waveform given to the repair of attempt n), `signoff.waves.json` (the winner's), `final/winner.v`, `final/eqy.log`, `final/summary.json`, `final/signoff.json`, `final/signoff-mutants.json` (every planted bug: `id`, `rule`, `line`, `before`, `after`, `result`, `detail`, `eqy`), `runlog.json`.

Candidate field rules: `rtl` = text of the attempt in flight or the last attempt; `attempts` only contains attempts that have a verification; `verification` = last attempt's verification; `reason` = attempt 1's `RtlOut.reason` (fallback `"First version, written from the plan"`); `fixCount` = number of FIX attempts; `fitness` null unless measured and not rejected; `isBaseline` true for c1 from its first pass.

Terminal events: the orchestrator emits `done | error | cap_reached`. The run manager emits `stopped` (Stop, after cancelling the task), `error` with `"timed out: no progress for 90 s"` (watchdog; `runs.status = timed_out`), and `error` with `captions.RESTARTED` (startup recovery; the stored `error_reason` stays `"server restarted"`).

RunLog (`build_run_log`): `meta.runId`, `sample: false`, `title` = pack title, `presetId`, `recordedAt` = run start ISO UTC (`Z`), `durationMs` = last event tMs, `chapters` = first tMs of each caption chapter label in `captions.CHAPTERS` order (id from `CHAPTER_ID_BY_LABEL`), `note` = `"Recorded live run. Models: planner=<id>, rtl_gen=<id>, repair=<id>, optimizer=<id>."`; events sorted by seq.

### 6.3 Sign-off, waveforms and spec rules (artifacts; no new event type)

Models: `verity/contracts/signoff.py`. Each is an `artifact` event whose `content` is JSON (camelCase). Every field is new, so older clients ignore them and older run logs simply lack them.

**`spec/rules.json`** (spec stage, step 1): `{ "rules": string[] }`, the decisions the locked pack makes, in plain words: the pack's own `coding_rules` (protocol and behavior) first, then one line each for the clock (or "No clock"), the reset (polarity and kind), the latency (clocked designs), and the lane layout of lane ports (`TaskPack.spec_rules()`). Same text the model already gets; never `golden.py`, `test_dut.py` or `reference.v`.

**`waves/<candidateId>.json`**: `{ candidateId, case, failCycle: number | null, startCycle, signals: { name, width, got: string[], expected?: string[] }[], note? }` (one value per cycle, every row the same length; `"x"` unknown; `""` in `expected` where nothing is expected). Sent for a version that failed its tests, right before its repair (its first failing case; a later one for the same version replaces it), and for the winner at sign-off (its first case, `failCycle: null`). Shape and meaning of the values: section 3.11.

**`signoff/<candidateId>.json`**, on every run (fast and full) that has a winner, after `run done` and the Done caption:

```
{ candidateId: string;
  tests: { seedsRun: number; seedsPassed: number; perSeed: { seed: number; passed: number; total: number }[] };
  lint: { errors: number; warnings: number; byTag: Record<string, number>; latches: number };
  eqyVsV1: "proven" | "inconclusive" | "not_equivalent" | "not_run" | "v1_is_winner";
  eqyVsHuman: "proven" | "inconclusive" | "not_equivalent" | "not_run";
  strength: { planted: number; caught: number; harmless: number; undecided: number;
              status: "ok" | "skipped" | "failed"; note?: string } | null;
  durationMs: number }
```

- `tests`: `perSeed` starts with the seed the run already used (`task.toml [sim] seed`, the winner's own verification), then `[signoff] extra_seeds` (2 to 9) from one `seeds` call. A seed passes only with `passed == total == test_count`. If the call failed or was not reached, only the first seed is listed.
- `lint`: from the `seeds` call (Verilator, uncapped log, warnings per tag, `latches` = `LATCH`); else counted from the winner's own lint log. `errors` is 0 for a winner (it passed lint).
- `eqyVsV1`: the prove stage's result for the winner (`not_run` in fast mode for a non-v1 winner, or when the prove stage did not run); `v1_is_winner` when the winner is v1. `eqyVsHuman`: the `human_ref` event's `eqy.status`, `not_run` when there is no `reference.v`.
- `strength`: up to `[signoff] max_mutants` planted bugs (section 3.10). `planted` = caught + survived (invalid and not run are not counted). Up to `[signoff] max_survivor_proofs` (3) survivors are checked with the reset-aware EQY against the winner (`tools.check_equivalence(winner, survivor)`): `proven` is `harmless`; `not_equivalent` is a miss (the tests let a real difference through); anything else, and every survivor not checked, is `undecided`. So `missed = planted - caught - harmless - undecided`. No proof is tried when equivalence is off for the task or the winner's own prove EQY timed out. `note` says all of this in plain words with the counts. `status: "skipped"` when nothing was planted (no place found, turned off) or the time limit was already reached; `"failed"` when the planted-bug run broke (sandbox error) or did not finish by the deadline. `null` is allowed by the shape but the backend always sends an object.
- `durationMs`: wall time of the sign-off, from its start (right after the result is out) to the artifact.
- Deadline: `[signoff] max_s` (300 s) from its start, never past the watchdog's hard cutoff (`max_wall_s + eqy_s + sandbox_overhead_s`, minus 15 s); parts not finished are cancelled and reported as above. Stop cancels it (no sheet; the run keeps its `done`). A sandbox error never changes the run's status.
- Then caption `signoff` (Done chapter), for example `Sign-off for v4: all 16 tests passed on 9 seeds, 0 lint errors and 2 warnings, 19 of 20 planted bugs caught.`
- Files: `final/signoff.json` (same body), `final/signoff-mutants.json` (section 6.2 Disk).

Delivery: these events come after the terminal `run` event while the run is still live (the run slot is held until they are out, typically 20 to 90 s; `GET /health` can say `busy` meanwhile). A live client that wants them keeps its stream open after the terminal event instead of closing it: the server ends the SSE response 5 s after the last event (`TERMINAL_GRACE_S`), `EventSource` reconnects with `Last-Event-ID`, receives the rest while the run is still finishing, and gets `204` once everything was delivered. `GET /runs/{id}/log` after the run ends has them too. `GET /health` lists `"signoff"` in `features`.

---

## 7. Fitness (frontend ARCHITECTURE 8.3), implemented in `verity/core/fitness.py`

```
gain_m  = 100 * (v1_m - c_m) / v1_m        m in {area, clock period, power}; lower is better
fitness = w_area * gain_area + w_timing * gain_timing + w_power * gain_power
weights: area {0.60, 0.25, 0.15}; speed {0.25, 0.60, 0.15}; power {0.25, 0.15, 0.60}
missing metric (null for v1 or c) -> drop it, renormalize remaining weights to sum 1
FAIL or rejected -> fitness null, never ranked
elites: best 2 by fitness among measured versions so far (v1 counts, score 0)
winner: highest fitness among versions no worse than v1 on the goal metric (area for area, clock period for speed,
        power for power; zero tolerance, equal is allowed; v1 always eligible; a missing metric skips the check);
        tie -> smaller area (then older candidate). Elites ignore the goal check.
copy: same cell count, area and clock period as an earlier measured version -> reason "Same result as vN";
      status and score unchanged, the older version wins the tie, never picked after its original was demoted
```

Use `compute_fitness`, `select_elites`, `pick_winner`. Do not reimplement.

---

## 8. Captions (`verity/core/captions.py`)

| When | Function | Example text | Chapter |
|---|---|---|---|
| spec loaded | `spec_read(pack.summary, priority)` | Spec read: 4 lanes of 8-bit multiply-accumulate over 16 cycles. Goal: smallest area. | Spec |
| planning | `drafting_plan()` | Drafting the plan. | Plan |
| plan ready | `plan_drafted(n, ms)` | Plan drafted: 4 blocks. Building in 10 s unless you steer. | Plan |
| toggle | `toggled(block, variant, value)` | You set pipeline on Multiplier array (4 lanes) to 2-stage. Replanning. | Plan |
| pin | `pinned(block, pin)` | You pinned "Minimize area" on Adder tree. Replanning. | Plan |
| replanned | `plan_updated(ms)` | Plan updated. Building in 6 s. | Plan |
| hold | `gate_held()` | Plan on hold. Building when you resume. | Plan |
| infeasible | `plan_rejected(reason)` | Plan rejected before any Verilog was written: ... | Plan |
| lock | `locked(n)` | Answer key locked: 16 tests from the golden model. The agent cannot edit them. | Lock |
| rtl | `writing_v1()` | Writing v1 from the plan. | Build and fix |
| sim | `simulating(n)` | Simulating 16 tests against the golden model. | Build and fix |
| fail | `failed_tests(label, k, n)` | v1 failed 3 of 16 tests. Reading the mismatches. | Build and fix |
| lint fail | `failed_lint(label)` | v1 did not pass lint. Reading the errors. | Build and fix |
| rules | `rejected_rules(label, problem)` | v1 broke the coding rules: initial blocks are not allowed (use reset logic). Fixing it. | Build and fix |
| repair | `diagnosis(text)` | Diagnosis: the accumulator is 16 bits and wraps. | Build and fix |
| baseline | `baseline_passes(label, n)` | v1 passes 16 of 16. This is the baseline. | Build and fix |
| no baseline | `no_baseline(k)` | No passing version after 3 fixes. Stopping here. | Build and fix |
| v1 PPA | `baseline_measured(label, ppa)` | v1 measured: 1,840 µm², 3.20 ns, power not measured. Everything after is compared to this. | Build and fix |
| round | `round_started(r, n, parents, from_elites)` | Round 1: three new versions from v1. | Round r |
| child fail | `child_failed(label, k, n)` | v3 failed 2 of 16 tests. Fixing it before it can compete. | Round r |
| rejected | `child_rejected(label)` | v6 failed again after a fix and was rejected. | Round r |
| duplicate | `child_duplicate(label, other)` | v4 was identical to v2 and was dropped. | Round r |
| child PPA | `child_measured(label, "v1", ppa, base)` | v5: area -17.4% vs v1, clock period +5.6%, power not measured. | Round r |
| elites | `elites_kept(labels)` | Kept the two best: v3 and v2. | Round r |
| no gain | `no_improvement()` | No version beat v1 this round. Skipping round 2. | Round 1 |
| winner | `winner(label)` / `baseline_wins()` | Winner by fitness: v5. | Round r |
| prove | `proving(label)` | Checking v5 against v1 with Yosys EQY. | Prove |
| proven | `proven()` | Proven equal to v1. | Prove |
| inconclusive | `not_proven()` | Tested, not proven: EQY could not decide in time. | Prove |
| not equal | `not_equivalent(label, next)` | v5 is not equal to v1 and was rejected. Next best: v3. | Prove |
| cap | `budget_reached(limit)` | Budget reached (max usd). Finishing with what is measured. | current |
| done | `done(versions, fixes, rejected, human)` | Done. 6 versions, 2 fixes, 1 rejected. Human inputs: 2. | Done |
| sign-off (after done, 6.3) | `signoff(label, tests, seeds_passed, seeds_run, errors, warnings, caught, planted)` | Sign-off for v4: all 16 tests passed on 9 seeds, 0 lint errors and 2 warnings, 19 of 20 planted bugs caught. | Done |
| error | `failed(reason)` | Run ended: no passing baseline | current |
| error (infrastructure) | `failed(sandbox_failed(detail))` / `failed(model_failed(...))` | Run ended: The test machine (Docker) is not running. Start Docker and try again. | current |

Infrastructure failures end the run with one plain sentence as the `run error` message and in its caption (`captions.DOCKER_DOWN`, `IMAGE_MISSING`, `SANDBOX_TIMEOUT`, `SANDBOX_BROKEN`, `MODEL_TIMEOUT`, `MODEL_UNREACHABLE`, `MODEL_RATE_LIMITED`, `MODEL_KEY`, `MODEL_DOWN`, and `RESTARTED` for a run cut off by a restart). The raw detail goes to the server log only. Event shapes and field names are unchanged.

Chapter labels are the constants `CH_*` in `captions.py`; `round_chapter(r)` picks Round 1 or Round 2.

---

## 9. HTTP API (builder E)

| Method and path | Auth (when `auth.required`) | Request | Response |
|---|---|---|---|
| `GET /health` | none | | `HealthResponse {liveEnabled, reason?, capUsd, provider, models, authRequired, features}` |
| `GET /runs` | none (public, like `/log` of a finished run) | `?limit=` (1 to 100, default 50), `?offset=` | `200 RunsResponse {runs: RunSummaryItem[]}`, newest first |
| `GET /runs/mine` | session (a Verity session token or a Supabase access token), owner only | | `200 MyRunsResponse {runs: [{id, title, threadId: string \| null, createdAt, status: string \| null}]}`, newest first, at most 50; `502` when the chat store is unreachable |
| `POST /auth/session` | none, `limits.auth_per_ip` per `auth_window_s` | `SessionRequest {accessCode}` | `200 SessionResponse {token, expiresAt}` or `401` |
| `POST /runs` | session | `CreateRunRequest {presetId, priority, mode?, params?, researchId?, threadId?}` | `201 CreateRunResponse {runId, capUsd, streamToken}`; `429 {error, reason}`; `422 {error}`; `503 {error, reason: "sandbox_down"}` |
| `POST /parse` | session, `limits` parse bucket per session | `ParseRequest {text}` (1 to 300 characters) | `200 ParseReply`; `422`; `429 {error, reason}`; `502`; `503 {error, reason: "maintenance"}` |
| `POST /runs/{id}/stream-token` | session, owner | | `200 {streamToken}` (a fresh token for `{runId, sid}`, `Cache-Control: no-store`); `403`; `404` |
| `GET /runs/{id}/events` | `?st=<stream token>`; a finished run replays without a valid one (public, like `/log`) | `Last-Event-ID` header or `?after=<seq>` | SSE, or `204` when a finished run has nothing after the resume point |
| `GET /runs/{id}/log` | session, owner | | `RunLog` |
| `POST /runs/{id}/steer` | session, owner | `SteerRequest` | `202` |
| `POST /runs/{id}/stop` | session, owner | | `202` |
| `POST /runs/{id}/ask` | session, owner; `limits.ask_per_session_per_min` per session | `AskRequest {text, focusVersion?}` (1 to 300 characters) | `200 AskReply`; `403`; `404`; `409` while the run is queued or running; `422`; `429 {error, reason}`; `503 {error, reason: "maintenance"}` |
| `POST /threads/{threadId}/messages` | session; `limits.message_per_session_per_min` per session | `ThreadMessageRequest {text, runId?, focusVersion?, cpuJobId?}` (text 1 to 300 characters) | `200 ThreadMessageReply {threadId, check, reply}`; `403`; `404`; `409` while the run named by `runId` is queued or running; `422`; `429 {error, reason}`; `502`; `503 {error, reason: "maintenance"}` |
| `GET /threads` | session, owner only | | `200 ThreadsResponse {threads: ThreadListEntry[]}` |
| `GET /threads/{threadId}` | session, owner only | | `200 ThreadView {threadId, runIds, turns}`; `404`; `422` |
| `GET /auth/me` | session | | `200 AuthMeResponse {email, role, credits, isAdmin, isSuperAdmin, canBuild, anonymous}` (`canBuild`: signed in) |
| `POST /admin/users` | session, admin only | `AdminCreateUserRequest {email, password, credits?}` | `200 AdminCreateUserResponse {ok, email, credits, userId}`; `400`; `403`; `502` |
| `POST /admin/credits` | session, admin only | `AdminSetCreditsRequest {owner, credits}` | `200 AdminSetCreditsResponse {ok, owner, credits}`; `400`; `403`; `502` |
| `GET /admin/runs` | session, admin only | `?owner=<owner>` or `?email=<email>` | `200 AdminRunsResponse {owner, email?, runs: MyRun[]}`, same shape and order as `GET /runs/mine`, for the given user; `403`; `422` |

`GET /admin/users` and `GET /admin/runs` are also read from the bench sidebar's "Accounts" panel (`AdminUsersPanel.tsx`, admin only): every account, each expanding to its own runs; `POST /admin/credits` sets a new balance in place. The panel cannot resume another account's run (`POST /runs/{id}/resume` is owner-only).

- `GET /runs` item (`RunSummaryItem`): `{id, presetId, params?, title, status, finishedAt, winner: {label, areaUm2, clockPeriodNs} | null, outcome?}`. `outcome` is optional and comes from the `run_outcome` table (migration 3); it is left out for a run that has no row (still going, or it ended before outcomes were kept). `outcome = {areaVsV1, clockVsV1, powerVsV1, versions, usd, signoff?}`: the three ratios are the winner against THIS run's own v1 (`1.0` = same as v1, lower is better, `null` when unknown, for example power when it was not measured or a run with no winner), `versions` is the versions tried, `usd` is the run's actual spend, and `signoff = {seedsPassed, seedsRun}` is read from the stored `final/signoff.json` (section 6.3) when that sheet exists, is for the winner and ran at least one seed; otherwise the key is left out. The route stays public and its other fields are unchanged. The web's Workspace page (`/workspace`) joins this list to the runs started in the browser by `id`.
- Tokens: `itsdangerous.URLSafeTimedSerializer(settings.signing_secret)`, salt `verity.session` (payload `{sid, role}`, max age `auth.session_ttl_s`) and salt `verity.stream` (payload `{runId, sid}`, max age `auth.stream_ttl_s`). Access codes: compare `sha256(code).hexdigest()` against `settings.access_codes` with `hmac.compare_digest`. Outside production with no `VERITY_SIGNING_SECRET`, a random secret is created once in `settings.data_dir/signing-secret` (backend/data/, gitignored) and read on later starts, so session and stream tokens survive a restart; if that file cannot be written, a per-process secret is used and the log says tokens will not survive a restart. The env var always wins. Production refuses to start without it. The secret is never logged.
- Accounts and Credits (Supabase sign-in, ARCHITECTURE 17.5). `Authorization: Bearer <token>` is either a Verity session token (`POST /auth/session` with the access code, as above) or a Supabase access token. The server tries the Verity token first, then the Supabase token when `SUPABASE_URL` is set. A Supabase token is checked against the project's JWKS (`<url>/auth/v1/.well-known/jwks.json`, read again after `[supabase] jwks_cache_s`, 600 s at most): the token's `kid` picks the key and the key's own algorithm is the only one accepted (ES256 for an EC P-256 key, RS256 for an RSA key), so HS256 and `none` tokens are refused whatever their header says; `iss` must be `<url>/auth/v1`, `aud` must be `authenticated`, `exp` must be in the future (10 s leeway) and `sub` must be a uuid. A key id the server does not know makes it read the keys again, at most every 30 s. A project that publishes no keys (it still signs with the shared secret) is checked with `GET <url>/auth/v1/user` (`apikey`: `SUPABASE_PUBLISHABLE_KEY`, `Authorization: Bearer <the user's token>`); `200` means valid, the user id and email are taken from that answer, and the answer is kept 60 s per token. The user's session id (`sid`) is `"u:" + <user id>` with role `user`: the same on every device, so rate limits, the per-session spend limit (`core.budget.spend_owner`), run ownership, stream tokens, threads and `GET /runs/mine` follow the user. Each account is seeded with 3 run credits by default (`initial_credits`, table `user_credits`). Credits apply only to an anonymous visitor (role `anonymous`, possible only when `auth.required` is false): `POST /runs` checks that its credits > 0 and deducts 1 credit upon slot acquisition; if exhausted, admission is rejected with 429 `{error: "You have used all your run credits. Contact an admin to add more.", reason: "credits_exhausted"}`. Any signed-in principal (a Supabase account, an access code session of any role, or the local-admin visitor; `Settings.can_build(role)`, true unless the role is `anonymous`) is never refused for credits and never charged, and the orchestrator's credit checkpoints and `POST /runs/{id}/resume` let its runs pass. Who may build: chip runs (`POST /runs`), CPU jobs (`POST /cpu-jobs`), core retries (`POST /cpu-jobs/{id}/retry`), upgrades (`POST /cpu-jobs/{id}/upgrade`) and the chat agent's `propose_cpu_job`, `propose_cpu_retry` and `propose_cpu_upgrade` are open to every signed-in principal and need no admin role; a visitor with no session is refused a CPU build with 403 "Sign in to build a CPU." and keeps the credit charge for chip runs. Owner checks stay (only the owner, or an admin, views, retries or upgrades a job) and `/admin/*` stays admin only. The global daily dollar limit, the per-session spend limit (chip runs; a CPU job is outside it), each run's and CPU job's `max_usd`, concurrency and slot limits, maintenance and sandbox checks apply to everyone. A chat proposal still starts nothing until the user presses Confirm. Public signup is disabled; only designated admins (`admin_emails`, default `admin@verity.com`, or team role) can create accounts via `POST /admin/users` or adjust credits via `POST /admin/credits`. A super admin (`super_admin_emails`, default `supadmin@verity.com`, or role `super_admin`) is also an admin and can see any user's runs via `GET /admin/runs`.
- Checkpoint and resume. A run that hits credits exhausted, a retryable model-call network error, or a sandbox infra error, after the evolve stage has started, saves `checkpoint.json` (plan, candidates, evolve progress) and ends as `stopped` with `error_reason` `credits_exhausted`, `network_error` or `sandbox_error` instead of being lost. `POST /runs/{id}/resume` (owner only) re-admits it (an anonymous visitor spends another credit, a signed-in one none; same admission checks as `POST /runs`), reopens the stream from the stored events and the orchestrator resumes from the checkpoint straight into the evolve stage. The web's `/workspace` Runs cards show a Resume button on any card whose status is `stopped` (`resumeRun()` in `frontend/src/lib/sources/live.ts`). The same failures before evolve has started (spec, plan, lock, baseline) end the run as `error`: nothing expensive was done yet, so restarting with a new `POST /runs` costs the same as resuming.
- `auth.required = false`: no token needed anywhere; session id from the client IP (S11); a stream token is still returned and accepted but not required.
- Stream tokens follow the user through `POST /runs/{id}/stream-token`: the token from `POST /runs` lives only in the tab that started the run, so another device or tab of the same account (or access-code session) asks this owner-only route for a fresh one before it opens `GET /runs/{id}/events` for a run that is still going.
- `GET /runs/{id}/events` access: a valid stream token for that run opens it (owner check). Without one (missing, junk or expired after `auth.stream_ttl_s`), a FINISHED run (not queued or running) replays on the same public rule as `GET /runs/{id}/log`, so a reload or a reconnect after a restart still shows it. A queued or running run never streams without a valid token, except to its owner's anonymous session when `auth.required` is false. An invalid token for an unknown run is `401`.
- SSE: `Content-Type: text/event-stream`, `Cache-Control: no-cache`, `X-Accel-Buffering: no`. First line `retry: 3000` (`retry: 250` when a finished run is replayed, so the reconnect that is answered 204 comes at once). Each event `id: <seq>\ndata: <event_to_json(e)>\n\n`. `: ping\n\n` every `sse.ping_s`. Source: `service.stream(run_id, after_seq)`; close after the terminal run event.
- Errors: `AdmissionDenied` 429 `{error, reason}`, except reason `sandbox_down`, which is 503 `{error, reason: "sandbox_down"}` with a plain sentence in `error` ("The test machine (Docker) is not running. Start Docker and try again." or "The test machine is missing its tool image. Build the sandbox image and try again."); the refusal does not use up the hourly run budget; `UnknownPreset` and validation 422; `NotFound` 404; `Forbidden` 403; `Conflict` 409; bad or expired token 401; the Supabase chat store unreachable, or refusing a call, `502 {error: "Saved chats are unreachable right now."}` on every route that reads or writes a thread or the run list (the cause is logged as a status and a Postgres error code, never a key). Bodies use `ErrorResponse`.
- Rate limits (in-process token buckets, `[limits]`): auth per IP, API requests per session per minute, new runs per session per hour (429 `rate_limited`). Concurrency (per session, global), the global daily spend and the per-session daily spend are the RunManager's admission (`busy`, `budget_exhausted`, `session_budget_exhausted`).
- Spend limits (all estimated dollars, persisted in SQLite so a restart does not reset them): `[budget.run] max_usd` is the per-run cap ($0.50, enforced by the run's own ledger; also the `capUsd` the API reports); `[limits] daily_usd` is the global limit per UTC day ($10.00, table `daily_spend`); `[limits] session_usd` is the limit for one session per UTC day ($3.00, table `session_spend(session_id, day, usd)`, migration 5). The session limit uses the same reserve-then-settle pattern as the daily one and is checked at the same three admission points: `POST /runs` (the run's cap must fit in what is left), `POST /parse` and `POST /runs/{id}/ask` when they need a model call (their worst case is held until the turn ends; a template ask turn costs nothing and is never refused). Settling adds a real cost to both totals in one transaction (`Store.add_daily_spend(day, usd, session_id)`), from the run's credits events, the parser and chat calls, and the research spend. A request is refused with `429 {error, reason: "session_budget_exhausted"}` when this session's spend plus its open holds plus the new worst case would pass `session_usd`, after the global check has passed (a used-up day answers `budget_exhausted` first). `error` is a plain sentence with no dollar figure ("You have reached today's spend limit for this session. Try again tomorrow."); the web shows what runs spent, never a limit. The key is `core.budget.spend_owner(session_id)`, which returns the session id: for a signed-in user that is `"u:" + user id`, so the $3 follows the user across devices and sign-ins. A new access-code login (or a new IP when `auth.required` is false) still has its own $3. `GET /health` ignores the session limit (it has no session).
- Health: `liveEnabled` false with `reason` (first match wins): `maintenance` (provider `nebius`/`openai_compat` without an API key, or tool backend not configured), `sandbox_down` (local_docker: `docker image inspect <tools.image>` fails, so Docker is not reachable or the tool image is missing; this applies to the fake provider too, because fake runs use the real tools), `budget_exhausted` (today's spend >= `limits.daily_usd`), `busy` (global concurrency full). The sandbox check is `ToolRunner.sandbox_problem()`: one docker call, limited to about 2 s, cached for 15 s (a sandbox call that fails drops the cache, so the next check looks at Docker again); `nebius_sandbox` has no cheap check and reports none. `POST /parse` does not check the sandbox. `capUsd` = `budget.run.max_usd`; `models` = `settings.models_by_role()`.
- CORS: `settings.cors_origin_list`, methods GET/POST/OPTIONS, headers `Authorization`, `Content-Type`, `Last-Event-ID`.
- `features` (on `GET /health`): `string[]` of what this backend serves end to end. Today `["params", "human_ref", "parse", "ask", "agent", "signoff"]` (`agent`: `POST /threads/{threadId}/messages` and the two thread reads; `signoff`: the sheet, the waveforms and `spec/rules.json` of section 6.3), plus `"research"` when a search adapter is configured (a search key is set: Tavily, Serper or both; or the fake adapter outside production; see `[research]`), plus `"accounts"` when Supabase sign-in is configured (`SUPABASE_URL` is set; `authRequired` keeps its meaning). Unknown values are ignored by clients; absent means `[]`. `models` lists all nine roles: `planner`, `rtl_gen`, `repair`, `optimizer`, `router`, `parser`, `chat`, `research`, `memory`.
- `params` (on `POST /runs`): optional `{ "<knob id>": int | bool | string }`. Omitted means the chip's defaults. Keys and values are validated against the family's `KNOBS` table (`tasks/<id>/family.py`): an unknown key, a wrong type (a bool is not an int) or an out-of-range value is `422 {error}` with a sentence meant to be shown as is; a chip with no knobs and a non-empty `params` is `422 "This chip has no settings."`. The run executes against the pack rendered at those knobs (`verity.core.family.render_pack`), so the `spec` event and every test describe that chip. Knobs: `mac4` lanes 1 to 8 (4), width 4 to 8 (8), cycles 4 to 32 (16); `add8` width 4 to 32 (8); `cnt8` width 4 to 16 (8), enable bool (true); `fsm_seq` pattern string of 0/1, length 3 to 8 ("1011"), overlap bool (true).
- `POST /parse` (the fact check of a thread's first message): the sentence goes only in the user message of one `parser`-route call (GLM 5.3, `reasoning_effort` low, JSON); the server re-validates family and knobs against the knob tables, so the reply only pre-fills the bench. `ParseReply` is one of
  `{ "result": "in_range", "family": "<id>", "params": { ... }, "note"?: "<one sentence>", "verdict"?, "intentClass"?, "research"?, "statedKnobs"?: string[] }` (params fully filled with defaults) or
  `{ "result": "out_of_range", "reason": "<sentence>", "closest": { "family": "<id>", "params": { ... } } | null, "verdict"?, "intentClass"?, "research"?, "statedKnobs"?: string[] }` (`closest` is always present; the model is told to suggest the nearest buildable family and clamps its values into range, `null` only when nothing relates and always for `not_chip`). Unreadable model output is an `out_of_range` with a fixed fallback reason and verdict `could_not_check`, never a 500. A parser-call provider failure is `502`; `budget_exhausted` is `429 {error, reason}`.
  - `statedKnobs` (optional, absent from older servers; for `out_of_range` only when `closest` is set, and then about the knobs of `closest`): the knob ids the sentence itself stated, in the family's knob order. Code decides, not the model (`verity.core.parse.stated_knobs`). A knob is listed only when the model named it, its final value is the value the model named (a value clamped into range is not what the sentence said), and the sentence's own words back that value: a number next to the knob's word (`8-bit`, `4 lanes`, `sixteen cycles`; number words are read), the knob's word with its meaning (`with enable`, `non-overlapping`, `without overlap`; a negation flips the value that counts as stated), or the bit string itself (`1011`, `1 0 1 1`, `1-0-1-1`). A knob id the code does not know a word list for is matched by its own name. `[]` means the sentence stated no knob. Every other knob is an assumption: its value is the chip's default, or the model's reading when the model gave a value the code could not confirm (for example "a nibble counter" gives width 4 and is not listed). The UI shows listed knobs as the user's and the rest as assumptions; the values are always the ones in `params`.
  - `intentClass` (the model's class after code checks): `not_chip | not_digital | too_big | in_family | near_family | unsure`. A model `not_chip` on a sentence with a hardware word (adder, counter, multiplier, FSM, register, clock, bit, Verilog, RTL, ALU, CPU, GPU, FIFO, chip, circuit, timer and similar; `verity.core.parse.HARDWARE_WORDS`) becomes `unsure`, and its reason is replaced by a code sentence. When the model then names no closest chip, code picks one from the sentence's own nouns (MAC array for multiplier, GPU or matrix words; adder for adder, ALU or CPU words; sequence detector for FSM words; else the counter), so that reply is `buildable_with_changes` with a closest chip, never `could_not_check` for lack of one.
  - `verdict` (set by code, never by a model): `buildable_now` (in_family, valid knobs, `in_range`); `buildable_with_changes` (in_family with clamped knobs, or near_family / unsure with a valid `closest`); `not_buildable_here` (too_big, not_digital); `not_a_chip` (not_chip); `could_not_check` (unreadable answer, unsure with no closest, or research that failed, was rate limited or hit the daily Serper credit cap on a hardware request). An in-family request whose research failed stays `in_range` (it can still be run) with verdict `could_not_check`.
  - `research`: always on the wire from this server. `null` when no research ran (`not_chip`, unreadable answer, research off, or new research refused by a limit), else a `Research` object: `{ id, status: "ok" | "partial" | "failed", reused, createdAt, queries: string[], sentences: [{ text, sourceIds: number[] }], sources: [{ n, url, title, domain, tier: "A" | "B" | "C" | "P", date: string | null, endpoint: "search" | "scholar" | "patents" }], techniques: [{ id, label }], doneBefore, pastRuns, usd, serperCredits, tavilyCredits? }`. `queries` are the exact texts sent to the search service (code templates from the family and knobs, or from the parser label reduced to a fixed hardware vocabulary; never the user sentence). `sourceIds` are source `n` values (S1 to S8); code dropped any other id. `techniques` come from the fixed per-family list (`verity.research.techniques`) with our own labels. `doneBefore` is true only with a tier A, B or P source of relevance 0.5 or more. `pastRuns` counts earlier Verity runs with the same family, knobs, task pack hash and tool image digest. `usd`, `serperCredits` and `tavilyCredits` are what THIS request spent (all 0 when `reused`); `tavilyCredits` is omitted when Tavily is not in use. A source's `endpoint` is the endpoint of the query that found it (`search`, `scholar` or `patents`), whichever service answered. `status`: `failed` when every search failed or the summary call failed (verdict `could_not_check`), `partial` when some searches failed or the summary was unreadable.
  - Money: before the calls, the request worst case (parser at its output cap plus one full research) is reserved on the daily budget and on the session's, and released after (`429 session_budget_exhausted` when the session's day cannot hold it); the real cost (parser call, research summary, search credits: Serper at `research.usd_per_credit`, Tavily at `research.tavily_usd_per_credit`) is added to the daily spend (`daily_spend.usd`) and to the session's (`session_spend.usd`), and the credits to `daily_spend.serper_credits` and `daily_spend.tavily_credits` (migration 4; caps `research.serper_credits_per_day` and `research.tavily_credits_per_day`, checked per service against the calls a research is about to make, and a research that would pass a cap is refused as `could_not_check`). The fake provider and the fake search adapter spend nothing. New research is limited per session (`research.per_session_per_hour`); a fresh cached record (same family-plus-knobs key, or the same templated queries, status ok, under `research.ttl_days` old) is reused for free and is not limited. Every session that receives a research id is recorded in `research_grant` (sha256 of the session id).
- Search services (`verity/research/`): Tavily (`tavily.py`, `POST https://api.tavily.com/search`, `Authorization: Bearer <key>`) is the general web search; Serper (`serper.py`, `POST https://google.serper.dev/{search|scholar|patents}`, `X-API-KEY`) keeps Google Scholar and Google Patents. With `research.adapter = "auto"` and a real provider: both keys give Tavily for `search` and Serper for `scholar` and `patents` (`routed.py`), one key serves all three endpoints from that service (Tavily alone restricts a `scholar` query to the paper and standards domains and a `patents` query to `patents.google.com`), no key turns research off. `serper` or `tavily` forces that service only; the fake LLM provider always gets the canned fake adapter, whatever keys exist, so it never spends credits. Tavily request body: `query`, `search_depth: "basic"` (1 credit), `max_results` (`research.num`), `include_domains` (the tier allowlist of `verity.research.sources`, which only narrows the results: code still drops any host not on the allowlist, and `.edu` style university hosts are not requested from Tavily because wildcard syntax is not documented), `include_published_date`, `include_usage` (the response's `usage.credits` is what the call cost, else 1). Tavily's own `answer` is never requested: the summary is the research route's, checked by code against numbered sources. Errors become `SearchError` kinds from the status code only (never the response body): 401 and 403 `auth`, 429 `rate_limited`, 432 and 433 `plan_limit`, 5xx `server`, other 4xx `bad_request`, plus `timeout`, `network` and `bad_response`; a failed query makes the research `partial`, all failing `failed`. Queries are code templates only (family and knobs, or the parser label reduced to a fixed hardware vocabulary), never the user sentence.
- `researchId` (on `POST /runs`): optional, at most 64 characters (longer values are dropped). Used only when this session was handed that id by `POST /parse` AND the research is about the same family and knobs; otherwise it is ignored (the run starts, never an error). Without a usable id the run looks up research by the exact family-plus-knobs key, then the nearest knobs of the same family (distance 0.25 or less: sum of |a - b| / (max - min) over int knobs plus 1 per bool or bits mismatch). The run then plans with a "Known approaches" section (technique labels, our own text) and code-written lines about comparable past runs (same pack hash and tool image digest only), and its EXPLORE child gets one technique its parent does not use (techniques ranked by our own past wins, then the run goal from the fixed list's `goals` tag, then the research order). No web text, no summary text and no user text reaches a Nemotron role. The research used is stored on `runs.research_id`.
- `threadId` (on `POST /runs`): optional, a UUID (written with hyphens, any case); anything else is dropped before validation and the run starts as usual. When the thread is THIS session's (`threads.owner` is `core.budget.spend_owner(session id)`), code adds the run id to the thread's facts record, marks the thread checked (a run means a fact check already happened), makes the run's chip the block under discussion and clears the proposal that waited for Run. A thread that does not exist, or belongs to another owner, is left alone and never created. A failure to do this never fails the run.
- `GET /runs/mine` and the run index. Once a run has started, `POST /runs` also records `(owner, runId, threadId, title)` in the run index (`user_runs`; `owner` is `core.budget.spend_owner(session id)`, `threadId` the request's `threadId` or null); a failure of that write is logged and never fails the run. `GET /runs/mine` answers `{ "runs": [{ "id", "title", "threadId": string | null, "createdAt", "status": string | null }] }`, newest first, at most 50, only the runs this session started (for a signed-in user: on any device). `title` is the chip's title, `createdAt` is ISO 8601 UTC, `threadId` is `null` (present, not left out) when the run did not start from a thread, and `status` is the run's status on this server (`queued`, `running`, `done`, `error`, `cap_reached`, `stopped`, `timed_out`) or `null` when its files are no longer here. The web joins it to `GET /runs` by `id` for the outcome figures.
- `POST /runs/{id}/ask` (plan claim 3, the design partner after the first message). Thread state decides first, in code: owner session only (same rule as steer and stop: `403` for another session, `404` for an unknown run), and only an ENDED run (`done`, `stopped`, `error`, `cap_reached`, `timed_out`); `queued` or `running` is `409 {error: "The run is still going. Ask again when it ends."}` and does not use a token of the ask bucket. Body `{ "text": string (1 to 300 characters after trimming), "focusVersion"?: "v2" }`; a `focusVersion` that is not `v<number>` or not a version of this run is ignored. `AskReply` is one of
  `{ "kind": "answer", "text", "bullets": string[], "basis": string, "templated": boolean, "steps"?: AskStep[] }` (`basis` says where the answer comes from, e.g. "From this run: versions, tests, reports and the tie rule."; `templated` true when code wrote it, false when the chat model worded it and code checked every number; `steps` only on a walkthrough, see below, and `bullets` is `[]` then),
  `{ "kind": "proposal", "text", "proposal": { "presetId", "params", "priority", "mode"?, "title", "idea": string | null, "lastRunUsd": number | null, "capUsd" } }` (a new run of the same chip that the user confirms; the web posts it to `POST /runs`; nothing runs from `/ask` itself),
  `{ "kind": "new_chip", "text" }` (a different chip: the UI offers a new thread and fact-checks it there), or `{ "kind": "refused", "text" }` (off topic).
  - Intent: a free keyword pre-filter decides the clear cases (why kept, dropped, clock or critical path, cost, Verilog, make it faster or smaller, a different chip, greetings, and plainly off-topic words such as joke, poem, weather or recipe, which are refused without a router call); anything else goes to one `router`-route call (Nemotron 3.5 Lightning, JSON, thinking off) that answers `{intent, topic, version}`. Code checks the intent against `run_question | rtl_question | change_request | research | walkthrough | new_chip | small_talk | off_topic`, the topic against `selection | drop | timing | area | power | tests | cost`, and a version label against the run's real versions. A router failure or an unknown intent becomes a general run question. A version the run does not have gets a code answer ("This run has no version v9.").
  - Tools (all called by code, never by a model, `verity/ask/tools.py`): the run card (stored events: latest candidate per id, captions, rounds, winner, report artifacts; the run row; `candidates/<id>/sta.log` and `synth.log`; `final/llm-calls.json`), `explain_selection` (replays `verity.core.fitness` ranking: higher score, then smaller area, then the older version; names the tie-break that decided; reports dedupe drops that exist only as the caption "vN was identical to vM and was dropped."; whether round 2 ran), `version_detail`, `critical_path` (parses the OpenSTA report: start and end points, gates, arrival, setup, minimum period, slack), `compare` (deltas computed by code), `propose_run`, and the research service.
  - Answers: topics `selection`, `drop`, `timing` and `cost` are code templates with no model call. Other questions go to the `chat` route (GLM 5.3) with a numbered facts block (F1, F2, ...: every value with its unit and "vs v1"; a version's Verilog only for an `rtl_question`, as data) and the last three turns of this session on this run (kept in memory only, lost on restart). Code strips fenced blocks and every sentence with Verilog or the word "module", then extracts every number and requires each to be a facts value after normalizing its format (8,799 = 8799, 0.3890 = 0.389; a percentage must be a facts percentage and a dollar amount a facts dollar amount). One retry names the bad numbers; then the code answer replaces the reply (`templated: true`). A model failure never surfaces as an error: the turn falls back to code.
  - `propose_run`: one `parser`-route call (the current family, knobs and knob table plus the request) answers which knobs change; code drops unknown settings, applies `validate_params` on top of the current knobs, clamps an out-of-range whole number into its range (the reply says so) and refuses any other bad value (the current knobs stay, with the knob table's sentence). Priority comes from words in the request (faster, smaller, lower power, ...), else the run's own goal. `idea` is the technique the new run's EXPLORE version will try first, from the research that run will read (`ResearchService.references_for_run`, ranked by our own past wins, then the run goal, then the research order); `null` when there is no research for those knobs. `lastRunUsd` is this run's spend; `capUsd` the new run's cap. A different family in the answer makes the turn `new_chip`.
  - `walkthrough` intent ("walk me through it", "explain the architecture", "show the steps", "how was this built", "tell me step by step how you built it"; the pre-filter decides, the router may also answer it): no model call and no budget reserved. The answer is `kind: "answer"`, `templated: true`, `bullets: []`, and `steps`, nine objects in this order (`verity.contracts.WALK_STEP_ORDER`): `{ stepId: "spec" | "plan" | "rtl" | "lint" | "simulate" | "synth" | "improve" | "equivalence" | "signoff", title, text, candidateId?: string, nodeIds?: string[] }`. `text` is built by `verity/ask/walkthrough.py` from the run card, every number read from run data (events, artifacts, run files), at most 900 characters. The version the version steps talk about is the version named in the text, else `focusVersion`, else the kept version; `candidateId` is that version's id (`c1`) on the steps about one version and left out on `plan` and `improve` (about the whole run). Sources: `spec` the `spec` event, the artifact `spec/rules.json` (`{ "rules": string[] }`) and the stage's tests; `plan` the last `plan` event (an ordered list of sections of one module, said as such) and the artifact `plan/plan.rN.json` latency; `rtl` the file `candidates/c1/samples.json` (the first drafts of v1 and the chosen one) or the candidate's origin and reason; `lint` the artifact `signoff/<candidateId>.json` `lint` (errors, warnings, `byTag`, `latches`), else the candidate's Verilator log; `simulate` the candidate's verification, plus `tests.seedsRun` and `tests.seedsPassed` from the sign-off sheet; `synth` PPA against v1 and the OpenSTA critical path; `improve` the rounds, `explain_selection` and the drop reasons; `equivalence` the sign-off sheet's `eqyVsV1` and `eqyVsHuman`, else the candidate's `eqy` and the `human_ref` event; `signoff` the sheet (tests on N seeds, lint, both proofs, test strength: caught of planted, harmless, undecided, or skipped or failed with its note). With no sheet it says the sign-off is still running when the run ended less than 15 minutes ago, else that the run has none; a sheet for the kept version is shown, with a sentence saying so, when the focus version has none. `nodeIds` are ids in `candidateId`'s artifact `circuit/<candidateId>.json` and appear only for exact matches: on `spec` the `port` nodes whose label equals a spec port name, on `rtl` the circuit's own `reg` nodes. Unreadable or wrongly shaped artifacts are skipped, never an error; a run that ended early still returns all nine steps, each saying what did not happen. A version label the run does not have gets the plain "no version" answer instead.
  - `research` intent: the run's own research (`runs.research_id`) if it has one, else the research service for the run's family and knobs (same cache, limits and spend as `POST /parse`). The answer carries the code-checked sentences with their source numbers, the known approaches and up to three sources (`S<n> title (domain, date): url`).
  - Money: a turn that may call a model first reserves its worst case on the daily budget and on the session's (router at its cap when the pre-filter did not decide, plus the largest of two chat calls, one proposal call or one research; about $0.02 to $0.03 with the default routes) and releases it after; every call's real cost goes into `daily_spend.usd` and `session_spend.usd`. At most two model calls plus one validator retry per turn. A template turn costs nothing, reserves nothing and still answers when the day's budget is used up; a model turn then gets `429 {reason: "budget_exhausted"}`, or `session_budget_exhausted` when it is this session's day that is used up. `503 maintenance` only for a model turn when the provider has no key. The fake provider spends nothing.
- `POST /threads/{threadId}/messages` (the bench chat after the first chip request; backend ARCHITECTURE 17.4). `threadId` is a UUID (any version, written with hyphens, case ignored; the lowercase form is stored and echoed); anything else is `422 {error: "The thread id must be a UUID."}`. Body `{ "text": string (1 to 300 characters after trimming), "runId"?: string, "focusVersion"?: "v2", "cpuJobId"?: string }` (a `focusVersion` that is not `v<number>`, or not a version of the thread's latest finished run, is ignored). `cpuJobId` is the CPU job whose board is on screen: it is kept only when it matches `cpu_<32 hex>` and passes the rule of `GET /cpu-jobs/{id}` (owner, or an admin); otherwise it is dropped without an error and the message is still answered. The agent then reads that job in `<cpu_job>`, adds a `<session>` block (the owner's other recent CPU jobs and runs, one line each), and, when the job's chat is gone, links the job to this thread. A `runId` must be one of this session's runs: `404` for an unknown run, `403` for another session's (same rule as steer and stop), and `409 {error: "The run is still going. Ask again when it ends."}` while that run is queued or running (same as `POST /runs/{id}/ask`), all checked before the message bucket is used. Response `200` (`Cache-Control: no-store`):
  `{ "threadId": string, "check": ParseReply | null, "reply": AgentReply | null }`. `check` is exactly the `POST /parse` reply and is present only when this message ran the fact check; `reply` is null only when `check.verdict` is `not_a_chip`. `AgentReply` is one of
  `{ "kind": "answer", "text", "bullets": string[], "basis", "templated": boolean, "steps"?: AskStep[], "toolSteps": ToolStep[], "chips"?: string[], "openTab"?: OpenTab, "research"?: Research }` (`steps`: the nine walkthrough steps when the agent ran the `walkthrough` tool, and `bullets` is `[]` then; `chips`: at most 5 short phrases of at most 40 characters that the web sends as the next message; `openTab` the last `open_tab` that worked; `research` the last research that worked),
  `{ "kind": "proposal", "text", "proposal": RunProposal, "toolSteps": ToolStep[] }` (a chip card; nothing runs until the user presses Run and the web posts it to `POST /runs` with this `threadId`; `text` ends with any clamp notes written by code; `proposal.mode` is omitted, `idea` is the technique the new run's EXPLORE version will try first or null, `lastRunUsd` is null),
  `{ "kind": "refused", "text", "toolSteps": ToolStep[] }` (the model could not be asked or its answer could not be used, and no tool had found anything to say: a fixed sentence).
  `ToolStep = { "tool": "list_blocks" | "research" | "run_facts" | "walkthrough" | "propose_run" | "open_tab" | "draft_custom_block", "label": string, "ok": boolean }`: `label` is a fixed phrase of code built only from code-owned values (a tool, a version label the run has, a count): "Read the build list", "Searched papers and the web: 3 sources", "Looked up the timing of v4", "Wrote the walkthrough steps", "Made a chip card", "Opened the Verilog tab"; `ok` false is a step code refused, "Could not <what the tool does>: <fixed reason>" ("Could not make a chip card: a setting was not valid", "Could not search for that: only one new search per message"). A word the model wrote (a bad block, setting or argument name) is never in a label; it appears only in the result text the model reads. A model that names a tool outside this list gets a refused step in its own context and no `ToolStep` on the wire. `OpenTab = { "runId", "tab": "result" | "versions" | "verilog" | "architecture" | "signoff", "version"?: string }`.
  - Flow, decided in code. The thread is loaded or created for `(spend_owner(session id), threadId)`. A `runId` on the request is added to the thread's facts and the thread counts as checked. If the thread is not checked, the sentence goes through the same fact check as `POST /parse` (own reserve, own spend, research): class `question` returns `check: null` and the thread stays unchecked while the agent answers; verdict `not_a_chip` returns the check with `reply: null`, stores nothing and the next message is checked again; any other verdict returns the check, marks the thread checked and writes the verdict, block, settings, what the sentence stated and the research id into the facts record, and the agent then writes a note of at most 2 sentences and 3 bullets that adds what the card does not say. A checked thread runs only the agent. Both turns (the user's text cut to 300 characters, Verity's to 1,200, with the tool steps) are stored; the older turns are folded into a 20 to 40 word summary in batches: when 14 turns are not in the summary yet, the oldest 4 are folded in (one `memory` call, a second only when the summary comes back under 20 words for turns that held more than 20 words of the user's text), leaving 10. So the agent reads the summary plus 10 to 13 recent turns, and a fold happens once every 4 turns, not on every message. After a failed fold more are waiting, and the next fold takes everything above 10, at most 8.
  - Agent: at most 4 tool steps and 5 chat-route calls per message, one JSON action per call (`{"action":"tool","tool":...,"args":{...}}` or `{"action":"answer","text":...,"bullets":[...],"chips":[...]}`). Tools (`verity/ask/agent_tools.py`), each validated by code: `list_blocks()`; `research(topic)` (topic reduced to letters, digits, spaces and hyphens, 60 characters, then to the fixed hardware vocabulary; queries are code templates; saved research is reused; the per-session research limit and the daily credit caps apply; the agent reads the checked summary sentences and source titles and domains, never snippets); `run_facts(run_id?, topic?, version?)` (topic `overview | selection | drop | timing | cost | tests | rtl`; Verilog only for `rtl`); `walkthrough(run_id?)`; `propose_run(family, knobs, priority?)` (an unknown family, setting or priority, and a value the knob table does not allow other than a whole number out of range, are refused; a whole number out of range is clamped and the notes say so; settings left out keep the pending proposal's or the thread's values for that family, else the defaults); `open_tab(tab, version?, run_id?)`; `draft_custom_block(description)` (CE3, section 9a): a nested `chat`-role call drafts a spec, worked examples and a Python golden model within the fixed custom-block limits; the sandbox self-checks it (`check_golden`, one redraft on failure); the reply carries the draft for the user to Approve (`POST /custom-blocks/approve`), and nothing is written to disk before that. A run id is accepted only when it is in the thread's facts and its row belongs to this session; `run_facts` and `walkthrough` need it finished. A message runs at most one new research: a second `research` whose topic is not already saved is a refused step ("only one new search per message"), saved research stays free, and the hold covers one research. On the thread's first message (the one with the fact check) `propose_run` and `draft_custom_block` are refused steps too: the card above is the user's chip.

### 9a. CE3: custom blocks

- `draft_custom_block`'s nested call is priced and recorded the same as a chat call (`core.budget.price_usd`, `RunManager._record_usd`), but is not included in the turn's pre-reservation hold (a known simplification: it is bounded in practice by the daily and session spend ceilings, which `record` always checks against after the fact, same as every other real call).
- `POST /custom-blocks/approve` | session | `ApproveCustomBlockRequest {draft: CustomBlockDraft}` | `201 ApproveCustomBlockResponse {ok, presetId}`; `422` (a shape problem, a re-check failure, or the id already exists) | `CustomBlockDraft = {id, title, summary, specText, top, elementType, latencyCycles, ports: [{name, dir, width (1 to 64), count (1), signed}], clockName, resetName, goldenPy, cases: [{name, steps: [{inputs: {<port>: int}, rst: boolean, expected: {<port>: int}}]}]}`. The server re-validates the draft's shape and re-runs `check_golden` against exactly what gets written, never trusting the round-tripped payload blindly.
- A new sandbox script, `/verity/bin/golden_check` (schema `verity.golden_check/1`, `status: pass | fail | timeout | input_error | internal_error`): pure Python, no cocotb, no Verilog tools, no pack mount. Input `$VERITY_WORK_DIR/golden.py` and `$VERITY_WORK_DIR/cases.json` only. `golden.py` must define `golden_step(state: dict, inputs: dict, rst: bool) -> tuple[dict, dict]`.
- `TaskPack.is_custom: bool` (default false): marks a pack locked by `POST /custom-blocks/approve` rather than one of the four locked packs. No `reference.v` is ever written for one, so `human_ref.measure_human_ref` already returns `None` cleanly (unchanged code) and the sign-off UI shows "Golden model drafted by Verity, approved by you" instead of the human-reference row.
- A sixth `Verdict`, `"custom_block"`, is wired through the wire type (backend and frontend) and the frontend's `VERDICT` record (shape `"plus"`), but `POST /parse` does not produce it in this pass; the live CE3 path is `draft_custom_block` inside an ongoing thread.
  - Time: the agent part of a message (its model calls and tools, not the fact check before it) has a deadline, `[agent] turn_deadline_s` (90). A model call never outlives it, no new call or tool starts after it, and code then answers (`templated: true`) from what the steps found. A tool still running at the deadline is left to finish (a research records its own spend). The web gives up at 120 s.
  - Code answers, when the model cannot be asked again or its answer cannot be used: each tool has one plain sentence (the build list by block titles; a run overview from the run's winner and goal; the checked research sentence, or a count of sources; "The Verilog is in the Verilog tab." with an `openTab` to it for `run_facts` topic `rtl`), all through `strip_verilog`. On a first message the note fits the verdict of the card above it: `buildable_now` "Check the card above and press Run when you are ready, or tell me what to change."; `buildable_with_changes` "The card above is the closest chip I can build. Pick it, change it, or ask me about the difference."; `not_buildable_here` "I cannot build that as one block. Ask me what it is made of, or pick the closest chip above."; `could_not_check` "The check could not finish this time. You can send that again, or tell me what to change."
  - The answer: code strips Verilog (fenced blocks and every sentence with Verilog), then every number must be in the `<verity_facts>` block, a tool result of this message, the user's own message or the fact check of this message. The summary and the earlier turns never count. One retry names the bad numbers (it uses one of the 5 calls); then code writes the reply (`templated: true`) from what the last tool found, or `kind: "refused"`. A model failure is never a `5xx`: the reply falls back to code. Text inside tool results, earlier turns and the summary is data; code acts on none of it, and no model text can start a run.
  - Money: before the fact check, the service holds the worst case of the agent part on the daily budget and on the session's (5 chat calls at their output cap with the full context, one research, and two summary calls over the turns the fold of this message would take when it can start one; the fake provider holds 0), released when the message ends; every call's real cost, the summary call included, goes into `daily_spend.usd` and `session_spend.usd`. `429 {reason: "budget_exhausted" | "session_budget_exhausted"}` and `503 {reason: "maintenance"}` are answered before anything is spent, and a refused message gives its place in the bucket back (so does `502`, the fact check's parser call failing). The bucket is `limits.message_per_session_per_min` (6) per session: the 7th message in a minute is `429 {reason: "rate_limited"}` with `Retry-After`.
- `GET /threads` and `GET /threads/{threadId}` (owner only: the owner is the caller's session through `core.budget.spend_owner`; another owner's thread does not exist for the caller). `GET /threads` answers `{ "threads": [{ "id", "title", "updatedAt", "runIds": string[] }] }`, newest first, at most 50, only threads with at least one message; `title` is the first user message on one line cut to 60 characters, `updatedAt` ISO 8601 UTC. `GET /threads/{threadId}` answers `{ "threadId", "runIds": string[], "turns": [{ "seq", "role": "user" | "verity", "text", "toolSteps"?: ToolStep[], "createdAt" }] }`, every stored row oldest first; a turn is two rows (the user's, `seq` odd, then Verity's, `seq` even), `toolSteps` is on Verity's rows only and `[]` when it took none. A Verity row also carries `reply` (the `AgentReply` exactly as `POST /threads/{threadId}/messages` sent it: the chip card, chips, walkthrough steps, research) and, when that message ran the fact check, `check` (the `ParseReply`), so a thread can be drawn again on another device. Both are optional: a row written before accounts, and a reply whose JSON is over 32 KB, have neither (the text and steps are always kept), and the web then rebuilds the reply from the text, the steps and `runIds`. The user's rows never carry them. `404 {error: "Thread not found."}` for an unknown id or another owner's thread, `422` for an id that is not a UUID.
- Thread storage (migration 6) is reached only through the `ThreadStore` protocol (`verity/core/protocols.py`): `get_or_create(owner, thread_id)`, `load(owner, thread_id, since_summary=False)` (facts, summary, `summarized_through` and the turns), `append_turns(owner, thread_id, turns)`, `update_facts`, `update_summary(owner, thread_id, summary, through_seq)`, `list_threads(owner, limit)`, `claim(from_owner, to_owner)` (moves every thread of one owner to another; nothing calls it, because moving a session's chats into an account was dropped), `add_user_run(owner, run_id, thread_id, title)` and `list_user_runs(owner, limit)` (the run index). `SqliteStore` (migrations 6 and 7) and `SupabaseThreadStore` (`verity/store/supabase_threads.py`, Postgres through the Data API, ARCHITECTURE 17.5) implement it, and `[threads] store` picks one at start (`auto`: Supabase when `SUPABASE_URL` and `SUPABASE_SECRET_KEY` are set, else SQLite; `sqlite` forces local; `supabase` insists). The agent and the routes depend on the protocol only. Tables: `threads(owner, id, created_at, updated_at, facts_json, summary, summarized_through, PRIMARY KEY(owner, id))` and `thread_turns(owner, thread_id, seq, role CHECK IN ('user','verity'), text, steps_json, reply_json, created_at, PRIMARY KEY(owner, thread_id, seq))` (migration 7 added `reply_json`: the `{ "check", "reply" }` wire JSON of a Verity row, 32 KB at most, NULL when absent or larger) and, from migration 7, `user_runs(owner, run_id, thread_id, title, created_at, PRIMARY KEY(owner, run_id))`. The Supabase tables have the same columns (`jsonb` for `facts`, `tool_steps` and `reply`, plus `threads.title`, set with the first message) and are created by `backend/supabase/schema.sql` (README). `owner` is `core.budget.spend_owner(session id)`. `facts_json` is written by code only: `checked`, `verdict`, `intent_class`, `family`, `knobs`, `stated`, `research_id`, `run_ids` (at most 20), `proposal`. Text is cut when stored (user 300 characters, Verity 1,200).
- Run outcomes: every run writes one `run_outcome` row and one `strategy_outcome` row per version after v1, from both endings (orchestrator summary, then RunManager finalize, which also covers stop, timeout and crash) with an idempotent upsert keyed by run id. Numbers are ratios against that run's own v1 (`winner_area_rel`, `winner_clock_rel`, `winner_power_rel`; lower is better); `partial` is true for any status but `done`.
- `human_ref` (SSE event): `{ "seq", "tMs", "t": "human_ref", "ppa": PPA, "eqy": Equivalence }`, emitted once after the `winner` event and before the terminal `run` event (also on the cap_reached path when a winner exists), only when the pack has a `reference.v`. A `caption` "Measuring the human-written design with the same tools." precedes it. `ppa` is `reference.v` (rendered at the run's knobs) through the same synth, timing and power flow as the candidates, with nulls for anything not measured. `eqy` is EQY of the reference (gold) against the winner (gate), `againstId` = the winner id, `status` one of `proven | not_equivalent | inconclusive | not_run`. EQY is `not_run` (reason in `log`) when equivalence is off for the task, the wall limit was reached, or the winner's own prove EQY timed out. A failure here never changes the run's status.
- Logs never include `Authorization` headers, tokens, access codes or API keys.

---

## 10. Environment variables (names only; `.env` is gitignored)

| Name | Maps to | Notes |
|---|---|---|
| `VERITY_CONFIG` | path of config.toml | default `backend/config.toml` |
| `VERITY_ENV` | `env` | development, production, test |
| `VERITY_PROVIDER` | `provider` | fake, nebius, openai_compat |
| `VERITY_TOOL_BACKEND` | `tool_backend` | local_docker, nebius_sandbox |
| `VERITY_DATA_DIR`, `VERITY_TASKS_DIR` | paths | relative to `backend/` |
| `VERITY_CORS_ORIGINS` | `cors_origins` | comma separated |
| `VERITY_ACCESS_CODE_HASHES` | `access_code_hashes` | `role:sha256hex,role:sha256hex` |
| `VERITY_SIGNING_SECRET` | `signing_secret` | 32+ chars in production; when unset outside production, a stored secret in `data_dir/signing-secret` is used |
| `VERITY_<TABLE>__<KEY>` | any table value | e.g. `VERITY_AUTH__REQUIRED=true`, `VERITY_BUDGET__RUN__MAX_USD=0.3` |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL_ULTRA`, `LLM_MODEL_SUPER` | `[llm]` | Nebius Token Factory, OpenAI-compatible |
| `SANDBOX_BASE_URL`, `SANDBOX_API_KEY` | `[tools.nebius_sandbox]` | key falls back to `LLM_API_KEY` |
| `SERPER_API_KEY` | `[research] api_key` (SecretStr) | sent only as the `X-API-KEY` header to `research.serper_base_url` (production pins its host to `google.serper.dev`); scrubbed by the log filter. Serper keeps Google Scholar and Patents. Without any search key, `research.adapter = "auto"` uses the fake adapter only when the LLM provider is `fake` and turns research off on a real provider (canned sources are never shown as real research) |
| `SUPABASE_URL` | `[supabase] url` | `https://<ref>.supabase.co`. Turns on sign-in with Supabase access tokens (feature `accounts`). Production needs https and a host that ends with `[supabase] host_suffix` (`.supabase.co`). Production accepts Supabase sign-in OR access code hashes (either is enough) |
| `SUPABASE_SECRET_KEY` | `[supabase] secret_key` (SecretStr) | server only. Sent only as the `apikey` header (never as a bearer token: `sb_secret_` keys are not JWTs) to `<url>/rest/v1/`; scrubbed by the log filter (the `apikey` header and any `sb_secret_` string). With `SUPABASE_URL` it moves chats and each user's list of runs to Supabase (`[threads] store = "auto"`) |
| `SUPABASE_PUBLISHABLE_KEY` | `[supabase] publishable_key` (SecretStr), optional | only for the legacy check of a project that still signs tokens with the shared secret (`GET <url>/auth/v1/user`) |
| `VERITY_THREADS__STORE` | `[threads] store` | `auto` (default), `sqlite`, `supabase`. Production refuses `supabase` without the url and the secret key |
| `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | read by `frontend/` only | public by design (the publishable key is meant for browsers); neither is set: no sign-in gate. The backend never reads them |
| `TAVILY_API_KEY` | `[research] tavily_api_key` (SecretStr) | sent only as `Authorization: Bearer <key>` to `research.tavily_base_url` (production pins its host to `api.tavily.com`); scrubbed by the log filter (the Authorization header and any `tvly-` string). Tavily is the general web search; with both keys Serper still answers Scholar and Patents |

---

## 11. LLM layer notes (builder B)

- Routes per role (`[routes.<role>]`): `model` (exact id) wins over `tier`; optional `reasoning_effort`, `thinking`, `max_output_tokens`, `timeout_s`. Run roles: `planner` (Nemotron Ultra), `rtl_gen` and `repair` (Ultra), `optimizer` (Super). Chat roles: `router` (`nvidia/Nemotron-3_5-Lightning`, thinking off, 300 tokens, 30 s), `parser`, `chat`, `research` and `memory` (`zai-org/GLM-5.3`, `reasoning_effort` low; 2,000 / 2,000 / 3,000 / 800 tokens; 25 / 30 / 45 / 25 s, short so the fallback below starts fast; `memory` has temperature 0 and writes the thread summary). The agent loop of `POST /threads/{threadId}/messages` runs on the `chat` route. A config.toml without the chat routes gets these defaults. Prices per 1M tokens (Nebius catalog): GLM 5.3 $1.40 / $4.40, GLM 5.3 Flash $0.15 / $0.50, Nemotron 3.5 Lightning $0.06 / $0.24, and DeepSeek V4 Pro $1.75 / $3.50 (a third-party listing of 2026-10-23, not confirmed on the Nebius page).
- Model fallback (`[llm.fallback."<primary model id>"]`, `chain = [{ model, thinking?, reasoning_effort? }, ...]`; `[llm] fallback_cooldown_s`, default 300). Shipped: `zai-org/GLM-5.3` and `zai-org/GLM-5.3-Flash` each fall back first to `deepseek-ai/DeepSeek-V4-Pro` (no `thinking`, no `reasoning_effort`: it answered a JSON call in 0.6 s on 2026-10-23) and then to `nvidia/Nemotron-3-Ultra-550b-a55b` with `thinking = false`. `OpenAICompatProvider.complete()` is the one place it lives: when a model that has a chain fails with a timeout, a connection error or a 5xx (never a 4xx, never an empty or unreadable answer), the same request goes to each chain entry in order, one try each, with that entry's own `thinking` and `reasoning_effort` (the primary's are not carried over), the same `max_output_tokens` and a timeout of at least 30 s. `LLMRequest.deadline_s` (seconds the whole call may take, default none) caps that further: a fallback attempt gets at most what is left of it, one with under 5 s left is not made, and when no attempt could be made the call raises `LLMError("no time left for a fallback model")`. The thread agent sets it on every chat call to the time left in its turn. Every model that failed is marked unhealthy for `fallback_cooldown_s`; while it is, requests skip it, and the cooldown simply expires (one log warning when a model is marked and one when it is used again, never prompt text or keys). With every model of a chain in cooldown they are all tried anyway. `LLMResponse.model` is the model that served the call: every caller that prices a call uses `settings.price_for(response.model)`, the gateway records it as `servedModel` and prices and ledgers the call on it, and a reply's basis line names it ("Worded by Nemotron 3 Ultra"). A worst-case hold prices each call at the highest input price and the highest output price across the route's primary model and its chain (`Settings.hold_price_for`; GLM 5.3 with DeepSeek V4 Pro and Ultra holds at $1.75 in and $4.40 out per 1M tokens), in the thread agent, the older ask, the fact check, research and the run ledger's reservations, so a chain model may cost more per token than the primary. An empty `chain` turns one model's fallback off; env: `VERITY_LLM__FALLBACK_COOLDOWN_S`, or `VERITY_LLM__FALLBACK` as JSON (for example `'{"zai-org/GLM-5.3": {"chain": []}}'`). A chain that lists its own primary is refused at load. `FakeProvider` has no chain.
- `[agent] turn_deadline_s` (90, greater than 0): the time limit of the agent part of one thread message (section 9). Each chat call's `timeout_s` is cut to what is left of it, and so is every fallback attempt of the call (`deadline_s`, above).
- `LLMRequest.thinking` is sent as `chat_template_kwargs.enable_thinking` only to models whose id starts with one of `llm.thinking_switch_prefixes` (`nvidia/`); GLM 5.3 accepts the field and ignores it (probe 2026-10-22). `LLMRequest.reasoning_effort` is sent as `reasoning_effort`. JSON mode (`response_format json_object`) is remembered per model id: one model refusing it does not turn it off for the others. The gateway records `servedModel` (the id the endpoint returned) next to the requested `model`, and each call keeps its own `seq` under parallel calls.
- `scripts/probe_models.py`: the capped probe (GET /models, then three short JSON calls) that chose these routes; it never prints the key.
- `NebiusProvider` and `OpenAICompatProvider` share one class over `openai.AsyncOpenAI(base_url, api_key, timeout)`; `request_id` from the response id (or the `x-request-id` header); usage from `response.usage` (add `completion_tokens_details.reasoning_tokens` if reported separately). `# UNVERIFIED: Nemotron reasoning token reporting and JSON mode on Nebius.`
- `FakeProvider`: zero network. Cassettes `backend/evals/cassettes/<task_id>/<role>[.<strategy>][.<n>].txt` keyed by `LLMRequest.tags` (`task_id`, `strategy`, `attempt`); without a cassette it returns a canned plan built from the run's pack (tag `pack_root`, the rendered pack of a knob run; never put in a prompt) and its `reference.v` as the RTL, so the full pipeline runs at $0.
- Prompt version: `"<template file name>@<sha256(template)[:12]>"`, recorded per attempt.
- Roles extract exactly one fenced Verilog block (accept `verilog`, `systemverilog`, `v` or bare fences; if several, take the one containing `module <top>`). Planner JSON: tolerate fences, retry once (`plan.json_retries`), then `RoleOutputError`. Truncate `reason` to 200 chars and `diagnosis` to 300.
- Repair prompts get the RTL plus either the lint log (first 4,000 chars) or at most 5 failing cases; never `golden.py`, `test_dut.py` or `reference.v`.

---

## 12. Open items marked UNVERIFIED (check on test day)

1. Nebius Sandboxes API: file upload vs stdin bundle, image import, per-run timeout above 120 s, cancel support, outbound network.
2. Yosys `write_json` port signedness key; OpenSTA commands and slack parsing; EQY result parsing and proof strength.
3. Nemotron on Nebius: JSON reliability, reasoning tokens in `usage`, request id location.
4. cocotb runner API for the pinned version (`cocotb.runner` in 1.9 vs `cocotb_tools.runner` in 2.x).
5. Docker Desktop on Windows: bind mounts of temp dirs with `--user 1000:1000` and `--read-only`.
