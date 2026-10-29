# W8 change log and validation, 2026-10-27

Implemented the pinned xPack GCC, Embench and riscv-formal image stage, RV32I
startup and board support, image build script, Verilator benchmark driver,
`/verity/bin/bench`, bundle dispatch and GCC version probe. Added the concrete
`SandboxRunner.bench` method, strict result parser, camelCase `CpuProfile`, and
an independent scratch RV32I fixture with parser and real sandbox tests.
Existing task packs and existing tool pins were not edited.

Commands below ran from this worktree's `backend/` unless stated otherwise.
No paid model or search-provider calls were made. Docker-heavy work was serial
after the orchestrator's memory instruction.

```powershell
docker build -t verity-tools:0.2 --build-context bench=bench -f sandbox/Dockerfile sandbox
```

Exit 0. The cold build completed, then the same command completed again from
cache with the final runner files. The compiler archive printed
`/tmp/gcc.tar.gz: OK`. The final image ID from Docker inspect is
`sha256:b103382e7d714c710a4caac7c4ad54497d581a8ca6ec0ad9e20e1ca43304ef8a`.
Build-time version probes: GCC 14.2.0, Verilator 5.020, Icarus 12.0,
Yosys 0.69+190, OpenSTA 3.1.0, cocotb 1.9.2.

```powershell
docker image inspect verity-tools:0.1 verity-tools:0.2 --format '{{.RepoTags}} {{.Id}} {{.Size}}'
```

Docker's `Size` field, bytes:

| Image | Before/after size |
|---|---:|
| verity-tools:0.1 | 671916387 |
| verity-tools:0.2 | 1106834478 |
| Increase | 434918091 |

```powershell
$env:PYTHONIOENCODING = 'utf-8'
& .venv/Scripts/python.exe -m pytest -q tests/bench/test_parser.py
```

Final parser-only run: `22 passed in 13.38s`.

```powershell
$env:PYTHONIOENCODING = 'utf-8'
& .venv/Scripts/python.exe -m pytest -q tests/bench
```

Exit 0: `25 passed in 125.16s (0:02:05)`. This includes a real CRC32 run,
the real complete suite through `ToolRunner.bench`, and a corrupted result
store that must fail. Real tests explicitly select `verity-tools:0.2` and run
with the normal LocalDockerRunner isolation and resource limits.

An early `python -m pytest -q` baseline run was interrupted when the
orchestrator instructed workers to stop full-suite testing because of memory
pressure. It had reported failures but no final failure details or pass count.
The full-suite comparison is unverified and deferred to the orchestrator after
merge. It was not restarted. W9's actual core and formal checks are outside
this fixture-based validation.

`python -m compileall -q bench verity/tools/bench.py verity/contracts/cpu_profile.py`
and `git diff --check` both exited 0.

The retained real run used an external temporary work directory with the
fixture plus the locked core pack's `blocks.v` in `dut.v`, and its `task.toml`
in a separate pack directory. Its exact Docker command, after setting
`$runDir = Join-Path $env:TEMP 'verity-w8-run'`:

```powershell
docker run --rm --name verity-w8-report --pull never --network none --read-only --tmpfs /tmp:rw,exec,size=512m --memory 2g --memory-swap 2g --cpus 2 --pids-limit 256 --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges --mount "type=bind,source=$runDir/work,target=/work" --mount "type=bind,source=$runDir/pack,target=/pack,readonly" verity-tools:0.2 /verity/bin/bench
```

Exit 0. `/work/out/result.json` follows verbatim, with whitespace expanded:

```json
{
  "schema": "verity.bench/1",
  "status": "pass",
  "bench": [
    {
      "cycles": 17762066,
      "instructions": 5746546,
      "cycles_by_stall": {"busy": 17762066, "fetch": 0, "data": 0},
      "passed": true,
      "self_check": 1,
      "message": "",
      "name": "crc32",
      "cpi": 3.0909116537133783
    },
    {
      "cycles": 73353939,
      "instructions": 24112967,
      "cycles_by_stall": {"busy": 73353939, "fetch": 0, "data": 0},
      "passed": true,
      "self_check": 1,
      "message": "",
      "name": "matmult-int",
      "cpi": 3.0420951100708593
    },
    {
      "cycles": 206807001,
      "instructions": 68613140,
      "cycles_by_stall": {"busy": 206807001, "fetch": 0, "data": 0},
      "passed": true,
      "self_check": 1,
      "message": "",
      "name": "edn",
      "cpi": 3.0141019781342173
    }
  ],
  "expected": ["crc32", "matmult-int", "edn"],
  "duration_ms": 79431
}
```

These are scratch-fixture numbers, not measurements of W9's core. The fixture
and immediate-acknowledgment memory produce zero fetch and data wait cycles.

Integration remains with the orchestrator: switch the default image in
`verity/config.py` and `config.toml`, extend `core/protocols.py`'s ToolRunner
protocol with `bench`, and wire CPU jobs. Those files are outside the W8
paths. This file provides the session change log for that integration.
