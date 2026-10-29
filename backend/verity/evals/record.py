"""One result row per task per repeat (ARCHITECTURE 14.4), written as JSONL.

Fields beyond the 14.4 list: `repeat`, `case` (injector or check name), `ok` (pass/fail of a self-check suite),
`note`, `error` and `extra` (suite specific values such as the packs report line).
"""

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, PrivateAttr


class EvalRecord(BaseModel):
    eval_run_id: str
    git_sha: str | None = None
    task_id: str
    suite: str
    provider: str
    repeat: int = 1
    case: str | None = None
    model_ids: dict[str, str] = Field(default_factory=dict)
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    tool_image_digest: str = ""
    candidate_count: int | None = None
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0  # part of output_tokens
    finish_reasons: dict[str, int] = Field(default_factory=dict)  # per model call: stop, length, ...
    est_usd: float = 0.0
    runtime_s: float = 0.0
    stage_times: dict[str, float] = Field(default_factory=dict)  # stage -> seconds
    initial_pass: bool | None = None
    final_pass: bool | None = None
    repairs_used: int | None = None
    repair_success: bool | None = None
    baseline_area_um2: float | None = None
    final_area_um2: float | None = None
    baseline_clock_ns: float | None = None
    final_clock_ns: float | None = None
    fitness_best: float | None = None
    improved: bool | None = None
    equivalence: str | None = None
    pack_hash_ok: bool | None = None
    ok: bool | None = None  # packs and isolation: did every check of this row pass
    note: str | None = None
    error: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))

    _started: float = PrivateAttr(default_factory=time.monotonic)

    @property
    def elapsed_s(self) -> float:
        """Seconds since this record was created (the suite starts the clock when it asks for the record)."""
        return time.monotonic() - self._started

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        """Accumulate wall time into `stage_times[name]`. Used by the suites that do not go through the orchestrator."""
        t0 = time.monotonic()
        try:
            yield
        finally:
            self.stage_times[name] = round(self.stage_times.get(name, 0.0) + time.monotonic() - t0, 3)

    def to_json_line(self) -> str:
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"))


class ResultFile:
    """Append-only JSONL file for one eval run: <results_dir>/<eval_run_id>.jsonl."""

    def __init__(self, results_dir: Path, eval_run_id: str) -> None:
        results_dir.mkdir(parents=True, exist_ok=True)
        self.path = results_dir / f"{eval_run_id}.jsonl"

    def write(self, record: EvalRecord) -> None:
        with self.path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(record.to_json_line() + "\n")


def load_records(path: Path) -> list[EvalRecord]:
    """Read one JSONL file. Lines that do not parse are skipped (a crashed run may leave a cut line)."""
    rows: list[EvalRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(EvalRecord.model_validate_json(line))
        except ValueError:
            continue
    return rows
