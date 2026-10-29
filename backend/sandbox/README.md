# Verity tool image (`verity-tools:0.2`)

W8 adds the benchmark image `verity-tools:0.2`. Build it from `backend/` with
`docker build -t verity-tools:0.2 --build-context bench=bench -f sandbox/Dockerfile sandbox`.
The existing tools and pins are preserved. See `../bench/README.md` for the
compiler checksum, source commits, suite selection, memory protocol and tests.
The new `bin/bench` stage uses `lib/bench_main.cpp` with Verilator. Keep using
0.1 until 0.2's validation passes and the orchestrator switches the shared config.

One pinned Docker image holds every EDA tool the backend uses. The agent never runs tools itself: the backend
copies a locked task pack and one candidate file into a throwaway container and runs one of three stage scripts.
The I/O contract is `backend/CONTRACTS.md` section 3 (this README only says how to build and try it).

**W8 status:** the 0.2 image builds and all 25 benchmark tests pass. See
`../bench/VALIDATION.md` for the commands, image sizes and real results. The
historical `UNVERIFIED` checklist below covers the other stages and is not a
claim that W8 reran their full validation.

## Layout

```
sandbox/
  Dockerfile            two stages: build OpenSTA (+CUDD), then the runtime image
  bin/verify            lint (Verilator), interface check (Yosys), simulation (cocotb + Icarus)
  bin/measure           Yosys synthesis on Nangate45, OpenSTA timing, circuit graph
  bin/eqy               winner vs v1 with Yosys EQY
  bin/seeds             sign-off: the locked tests on more seeds, lint warnings by tag
  bin/strength          sign-off: the locked tests on planted bugs (mutants.json from the backend)
  bin/wave              one test case as a waveform, cut around its first mismatch
  bin/bundle            stdin-tar fallback for backends that cannot mount files
  lib/verity_common.py  subprocess with deadlines, JSON emit, version probes (stdlib only)
  lib/verity_circuit.py Yosys JSON to the CircuitGraph wire format (stdlib only)
  lib/verity_sim.py     compile and run the locked tests (seeds, strength, wave; verify keeps its own copy)
  lib/verity_wave.py    VCD parsing, sampling, golden replay and the window (stdlib only)
  lib/verity_tb.py      cocotb helpers used by every task pack (Recorder, read_int, fmt_hex, reset, ...; wave mode)
```

`seeds`, `strength` and `wave` were added on 2026-10-22 (CONTRACTS 3.9 to 3.11). The VCD comes from a dump module the
`wave` script writes (a second top level), never from the design, so `$dump*` stays banned in model RTL. No pack file
changes: seeds reach the testbench through `VERITY_SEED`, and wave mode lives in `verity_tb.py`.

Inside the image: scripts in `/verity/bin`, helpers in `/verity/lib` (on `PYTHONPATH`), liberty at
`/verity/pdk/nangate45/NangateOpenCellLibrary_typical.lib` (`$VERITY_LIBERTY`), tool versions in
`/verity/versions.json` (written at build time). The container runs as uid 1000 and writes only `/work` and `/tmp`.

## Build

From `backend/`:

```
docker build -t verity-tools:0.2 --build-context bench=bench -f sandbox/Dockerfile sandbox
```

The build downloads (needs network at build time only): the OSS CAD Suite release `2026-10-05`, the Nangate45
liberty from OpenROAD-flow-scripts commit `6fc813e2b8d6c9fef68376d506c80283733e072f`, cocotb 1.9.2 from PyPI, and
clones OpenSTA (commit `7a034b56de14b485ddf8fd97b420a6f37a91c00d`) and CUDD (commit
`f54f533303640afd5dbe47a05ebeabb3066f2a25`). The build prints the liberty SHA-256; copy it into the
`NANGATE_SHA256` build arg to pin it. Expect a large image (the OSS CAD Suite is the bulk; see the Dockerfile for
what is trimmed) and a first build of 10 to 20 minutes (OpenSTA compile).

The Nangate45 liberty is never committed to this repo. Its source URL and hash are recorded in
`/verity/pdk/nangate45/SOURCE.txt` inside the image.

## Try one stage by hand

The local runner uses exactly this shape (`CONTRACTS.md` 3.3). Lines are for bash; on Windows use Git Bash or
Docker Desktop's shell. The stage prints one JSON object as the last line of stdout.

```
WORK=$(mktemp -d); cp tasks/mac4/reference.v "$WORK/dut.v"
PACK=$(mktemp -d); cp tasks/mac4/{task.toml,golden.py,test_dut.py} "$PACK/"   # no reference.v, no manifest

docker run --rm --network none --read-only --tmpfs /tmp:rw,exec,size=512m \
  --memory 2g --cpus 2 --pids-limit 256 --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges \
  -v "$WORK":/work:rw -v "$PACK":/pack:ro \
  -e VERITY_LINT_TIMEOUT_S=20 -e VERITY_SIM_TIMEOUT_S=60 -e VERITY_SYNTH_TIMEOUT_S=120 \
  -e VERITY_STA_TIMEOUT_S=60 -e VERITY_EQY_TIMEOUT_S=180 \
  verity-tools:0.1 /verity/bin/verify          # or /verity/bin/measure
```

For `eqy` also put the candidate at `$WORK/b.v` (v1 stays at `dut.v`). To debug interactively add
`--entrypoint bash -it` and drop `--read-only`.

Exit codes: 0 result printed (the design may still have failed), 2 input error, 3 internal error
(`CONTRACTS.md` 3.4). Stage results: `verity.verify/1`, `verity.measure/1`, `verity.eqy/1`.

## What each stage runs

| Script | Steps (stop at the first failure) | Fixed commands |
|---|---|---|
| `verify` | lint, interface, sim | `verilator --lint-only -Wall -Wno-fatal -Wno-DECLFILENAME`; `yosys -p "read_verilog -sv; hierarchy -check; proc; write_json"`; `iverilog -g2012` then `vvp -M <cocotb libs> -m libcocotbvpi_icarus` |
| `measure` | synth, timing, power, circuit | one `yosys -s` script (`synth -flatten; dfflibmap; abc -liberty; stat -liberty`), then `sta` (OpenSTA) on the written netlist for timing, and a second `sta` run for `report_power` (vectorless, input activity 0.1, at the pack's `target_clock_ns`; an estimate, not signoff) |
| `eqy` | prove | 1. a reset-aware miter (`sby`, engines `abc pdr` + `smtbmc yices`): reset assumed in cycle 0, outputs compared after, so state re-encodings are proven and its counterexamples are real. 2. `eqy -f -d <dir> verity.eqy` with `[strategy sby]`, engines tried in turn: `abc pdr`, then `smtbmc yices` (`VERITY_EQY_ENGINE`, `;` separated); only ports and register names are matched (`rename -hide`). EQY starts from any register state, so for a design with a reset its FAIL is reported as `inconclusive`, not `not_equivalent` |

Deviation from `CONTRACTS.md` 3.5: the simulation is started with `iverilog` + `vvp` directly (the documented
cocotb Makefile recipe) instead of `cocotb.runner`, because the runner has no per-call timeout and its API
differs between cocotb 1.9 and 2.x. The result file contract is unchanged.

## Tools and licenses (ARCHITECTURE 11.1, risk R11)

All tools run as separate executables inside the image. Verity's own code does not link them. The image build
file references upstream sources; nothing below is vendored in the repo.

| Tool | Role | License | Source in this image |
|---|---|---|---|
| Icarus Verilog 12 | simulation | GPL-2.0-or-later | Ubuntu 24.04 package |
| Verilator 5.x | lint | LGPL-3.0-only or Artistic-2.0 | Ubuntu 24.04 package |
| cocotb 1.9.2 | testbench framework | BSD-3-Clause | PyPI wheel |
| Yosys | synthesis, interface check | ISC | OSS CAD Suite 2026-10-05 |
| Yosys EQY | equivalence | ISC | OSS CAD Suite 2026-10-05 |
| SymbiYosys (SBY), yosys-smtbmc | EQY proof engine | ISC | OSS CAD Suite 2026-10-05 |
| Yices 2 (default solver) | SMT | GPL-3.0 (verify) | OSS CAD Suite 2026-10-05 |
| Z3, Boolector, Bitwuzla (alternatives) | SMT | MIT (verify) | OSS CAD Suite 2026-10-05 |
| ABC (`yosys-abc`) | technology mapping | UC Berkeley BSD-style (verify) | OSS CAD Suite 2026-10-05 |
| OpenSTA | static timing | GPL-3.0 (verify) | built from `parallaxsw/OpenSTA` at the pinned commit |
| CUDD | BDD library for OpenSTA | BSD-style (verify) | built from `cuddorg/cudd` at the pinned commit |
| Nangate45 Open Cell Library | liberty for area and timing | verify the redistribution terms | downloaded at build time, never committed |
| Python 3.12, bash, perl | scripts | PSF, GPL-3.0, Artistic/GPL | Ubuntu 24.04 packages |
| xPack RISC-V GCC 14.2.0-3 | bare metal RV32I benchmark compiler | bundled upstream toolchain licences | checksum-verified xPack release |
| Embench-IoT | integer benchmarks and self-checks | GPL-3.0-or-later, individual source notices retained | pinned commit, `/opt/embench-iot` |
| riscv-formal | W9 RVFI checks | ISC | pinned commit, `/opt/riscv-formal` |

Items marked "verify" are assumptions from memory and must be checked against each upstream `LICENSE` file before
the repo is published (risk R11). REvolution is credited as a reference for principles only, in the project docs.

## UNVERIFIED (check on test day)

1. Image build: CUDD and OpenSTA compile on Ubuntu 24.04; the `rm -rf` trimming of the OSS CAD Suite does not
   break `yosys`, `eqy`, `sby` or `yosys-smtbmc`; Ubuntu's iverilog is 12.x (the build fails fast if not).
2. PATH mix: Ubuntu `iverilog`/`verilator` come first, OSS CAD Suite `bin/` is last. If an OSS CAD wrapper needs
   `source /opt/oss-cad-suite/environment`, set it in the Dockerfile `ENV`.
3. cocotb 1.9.2 with the direct `vvp -M ... -m libcocotbvpi_icarus` flow: `LIBPYTHON_LOC`, `PYTHONHOME`, `MODULE`
   env names, and Python 3.12 inside a venv.
4. Yosys `write_json` port signedness key (`verify`, `compare_ports`).
5. `stat -liberty -json` layout and the text fallback parser (`measure`); `dfflibmap` legalizing synchronous
   resets onto Nangate45 flip-flops; the written netlist loading in OpenSTA.
6. OpenSTA: `all_inputs -no_clocks`, `report_worst_slack` output format, time unit assumed to be ns.
7. EQY: marker files `PASS/FAIL/UNKNOWN/TIMEOUT/ERROR` in the work dir, and whether `[strategy sby]` proves by
   induction at the configured depth or only bounds (anything but PASS is reported as not proven).
8. Docker Desktop on Windows: bind mounts of host temp dirs with `--user 1000:1000` and `--read-only`.
9. Nebius Sandboxes: file upload versus the `bundle` stdin tar, image import, timeouts (`CONTRACTS.md` 3.3).
