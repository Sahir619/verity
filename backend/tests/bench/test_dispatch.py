"""Exercise the sandbox entrypoint's top selection without invoking Verilator."""

import json
import os
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from verity.config import AGENT_ROOT


def entrypoint(monkeypatch, tmp_path, top, wait):
    calls, emitted = [], []
    (tmp_path / "suite.json").write_text(json.dumps({"benchmarks": []}))
    vc = SimpleNamespace(
        InputError=ValueError,
        env_float=lambda *args: 300,
        require_file=lambda path: path,
        work_dir=lambda: tmp_path,
        scratch_dir=lambda: tmp_path,
        load_task=lambda: {"top": top},
        fresh_dir=lambda path: path,
        tail=lambda text, limit: text[-limit:],
        emit=lambda data: emitted.append(data),
    )

    def compile_only(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(rc=1, timed_out=False, output="compile intercepted")

    vc.run = compile_only
    monkeypatch.setitem(sys.modules, "verity_common", vc)
    monkeypatch.setenv("VERITY_BENCH_WAIT", str(wait))
    monkeypatch.delenv("VERITY_BENCH_NAMES", raising=False)
    script = runpy.run_path(str(AGENT_ROOT / "sandbox/bin/bench"))
    script["main"].__globals__["Path"] = lambda path: tmp_path if path == "/verity/bench" else Path(path)
    return script["main"], calls, emitted


@pytest.mark.parametrize("top,harness,prefix", [
    ("rv32i_core", "bench_main.cpp", "Vrv32i_core"),
    ("rv32i_cache_core", "bench_main.cpp", "Vrv32i_core"),
    ("rv32i_pipe2_core", "bench_pipe2_main.cpp", "Vrv32i_pipe2_core"),
])
def test_top_selects_matching_harness(monkeypatch, tmp_path, top, harness, prefix):
    main, calls, emitted = entrypoint(monkeypatch, tmp_path, top, 0)
    main()
    cmd, = calls
    assert cmd[cmd.index("--top-module") + 1] == top
    assert cmd[cmd.index("--prefix") + 1] == prefix
    assert cmd[-1] == f"/verity/lib/{harness}"
    assert emitted[0]["wait_cycles"] == 0


def test_pipeline_wait_is_not_supported_before_compilation(monkeypatch, tmp_path):
    main, calls, emitted = entrypoint(monkeypatch, tmp_path, "rv32i_pipe2_core", 3)
    main()
    assert calls == []
    assert emitted[0]["status"] == "not_supported" and emitted[0]["wait_cycles"] == 3


def test_wait_is_canonicalized_for_the_cpp_driver(monkeypatch, tmp_path):
    main, _, emitted = entrypoint(monkeypatch, tmp_path, "rv32i_core", "+0003")
    main()
    assert emitted[0]["wait_cycles"] == 3
    assert os.environ["VERITY_BENCH_WAIT"] == "3"


@pytest.mark.parametrize("wait", [-1, "oops", "3.5"])
def test_invalid_wait_is_an_input_error(monkeypatch, tmp_path, wait):
    main, calls, _ = entrypoint(monkeypatch, tmp_path, "rv32i_core", wait)
    with pytest.raises(ValueError, match="nonnegative integer"):
        main()
    assert calls == []
