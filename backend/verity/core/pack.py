"""TaskPack: the locked reference for one supported design (ARCHITECTURE 13). Loaded from tasks/<id>/task.toml.

Integrity: every file in the pack folder (except MANIFEST.sha256, __pycache__/ and *.pyc) must be listed in
MANIFEST.sha256 with a matching hash, and every listed file must exist. Hashes are SHA-256 over the file bytes
with CRLF normalized to LF (so a Windows checkout hashes the same as Linux). The pack hash recorded on a run is
the SHA-256 of the MANIFEST.sha256 bytes (also LF-normalized).
"""

import hashlib
import logging
import shutil
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError, field_validator, model_validator

from ..contracts import Port, Priority, Spec
from .errors import PackFormatError, PackIntegrityError
from .rtl import DEFAULT_MAX_BYTES, Rtl

log = logging.getLogger("verity.main")

MANIFEST_NAME = "MANIFEST.sha256"
TASK_FILE = "task.toml"
GOLDEN_FILE = "golden.py"
TESTBENCH_FILE = "test_dut.py"
REFERENCE_FILE = "reference.v"
# Files never copied into a sandbox: the reference design (the orchestrator measures it at run time for human_ref, never in a sandbox), the manifest, and family.py (knob renderer).
SANDBOX_EXCLUDE: frozenset[str] = frozenset({REFERENCE_FILE, MANIFEST_NAME, "family.py"})
_IGNORED_PARTS: frozenset[str] = frozenset({"__pycache__", ".pytest_cache"})

ElementType = Literal["int4", "uint4", "int8", "uint8", "none"]
PortDir = Literal["in", "out"]

CODING_RULES: tuple[str, ...] = (
    "Restricted SystemVerilog (IEEE 1800-2012), synthesizable, one file, exactly one module (no helper modules).",
    "Declare every port and signal as `logic`. Do not use `wire` or `reg`, `generate`, or unpacked-array ports.",
    "Use `always_ff @(posedge clk)` with non-blocking assignments (<=) for registers and `always_comb` with blocking assignments (=) for combinational logic.",
    "Write lanes out one by one, or use a `for` loop inside `always_comb`. Use `*` and `+` for arithmetic; do not hand-build adders or multipliers.",
    "The top module name, port names, directions, widths and signedness must equal the given header exactly.",
    "Ports listed with a lane count are flat vectors: lane i of port p with lane width W is p[i*W +: W].",
    "One clock, rising edge only. Use the reset exactly as described (name, polarity, synchronous or asynchronous).",
    "No initial blocks, no latches, no tri-state, no force/release.",
    "No system tasks or functions except $signed, $unsigned and $clog2. No `include, no file I/O, no DPI.",
    "Keep the cycle latency from the spec exactly: outputs must be valid on the same cycle as the reference.",
    "Answer with exactly one ```verilog fenced block containing the whole file.",
)


class TaskPort(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    dir: PortDir
    width: int = Field(ge=1, le=1024)
    count: int = Field(default=1, ge=1, le=64)
    signed: bool = False

    @property
    def bits(self) -> int:
        """Total Verilog vector width. Lane ports are flattened: count * width."""
        return self.width * self.count

    @model_validator(mode="after")
    def _lanes_unsigned(self) -> "TaskPort":
        if self.count > 1 and self.signed:
            # Signedness of lanes lives in element_type; the flat vector itself is declared unsigned.
            raise ValueError(f"port {self.name}: lane ports (count > 1) are declared unsigned; use element_type")
        return self

    def to_contract(self) -> Port:
        return Port(name=self.name, dir=self.dir, width=self.width, count=self.count if self.count > 1 else None)

    def verilog_decl(self) -> str:
        direction = "input " if self.dir == "in" else "output"
        signed = " signed" if self.signed else ""
        rng = f" [{self.bits - 1}:0]" if self.bits > 1 else ""
        return f"{direction} logic{signed}{rng} {self.name}"


class ClockSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = "clk"  # "" for a purely combinational design


class ResetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = "rst_n"  # "" for no reset
    active_low: bool = True
    synchronous: bool = True


class SimSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seed: int = 1
    max_cycles_per_case: int = 1_000


class EqySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    depth: int = 24  # sby depth; at least latency_cycles + a few


class TaskPack(BaseModel):
    """task.toml plus its folder. Immutable after load; use TaskPack.load()."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_: Literal["verity.task/1"] = Field(alias="schema")
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    title: str
    summary: str  # caption fragment, e.g. "4 lanes of 8-bit multiply-accumulate over 16 cycles"
    spec_text: str
    top: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    family: str = "custom"
    element_type: ElementType = "none"
    latency_cycles: int = Field(ge=0)
    max_lines: int = Field(default=300, ge=10, le=2_000)
    test_count: int = Field(ge=1)
    default_priority: Priority = "area"
    priorities: list[Priority] = Field(default_factory=lambda: ["area", "speed", "power"])
    target_clock_ns: float = Field(default=10.0, gt=0)
    max_plan_blocks: int = Field(default=8, ge=1)
    eval_only: bool = False  # true: not offered as a live preset
    is_custom: bool = False  # true: a GLM-drafted custom block (CE3), locked by code at Approve, never a locked pack
    coding_rules: list[str] = Field(default_factory=list)  # extra pack-specific rules, appended to CODING_RULES
    clock: ClockSpec = Field(default_factory=ClockSpec)
    reset: ResetSpec = Field(default_factory=ResetSpec)
    sim: SimSpec = Field(default_factory=SimSpec)
    eqy: EqySpec = Field(default_factory=EqySpec)
    budget: dict[str, float] = Field(default_factory=dict)  # optional overrides of [budget.run] keys
    ports: list[TaskPort]

    _root: Path = PrivateAttr()
    _manifest_sha256: str = PrivateAttr(default="")

    @field_validator("ports")
    @classmethod
    def _unique_ports(cls, ports: list[TaskPort]) -> list[TaskPort]:
        names = [p.name for p in ports]
        if len(names) != len(set(names)):
            raise ValueError("duplicate port names")
        if not any(p.dir == "out" for p in ports):
            raise ValueError("at least one output port is required")
        return ports

    @model_validator(mode="after")
    def _clock_reset_ports(self) -> "TaskPack":
        names = {p.name for p in self.ports}
        if self.clock.name and self.clock.name not in names:
            raise ValueError(f"clock {self.clock.name} is not a port")
        if self.reset.name and self.reset.name not in names:
            raise ValueError(f"reset {self.reset.name} is not a port")
        if self.default_priority not in self.priorities:
            raise ValueError("default_priority must be one of priorities")
        return self

    # ---------- loading and integrity ----------
    @classmethod
    def load(cls, root: Path) -> "TaskPack":
        """Parse task.toml and verify MANIFEST.sha256. Raises PackFormatError / PackIntegrityError."""
        root = root.resolve()
        manifest_sha = verify_manifest(root)
        task_path = root / TASK_FILE
        try:
            with task_path.open("rb") as fh:
                data = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise PackFormatError(f"{task_path}: {exc}") from exc
        try:
            pack = cls.model_validate(data)
        except ValidationError as exc:
            raise PackFormatError(f"{task_path}: {exc}") from exc
        if pack.id != root.name:
            raise PackFormatError(f"{task_path}: id '{pack.id}' must equal the folder name '{root.name}'")
        for required in (GOLDEN_FILE, TESTBENCH_FILE):
            if not (root / required).is_file():
                raise PackFormatError(f"{root}: missing {required} (no pack, no run)")
        pack._root = root
        pack._manifest_sha256 = manifest_sha
        return pack

    @property
    def root(self) -> Path:
        return self._root

    @property
    def sha256(self) -> str:
        """Pack hash recorded on the run (SHA-256 of MANIFEST.sha256)."""
        return self._manifest_sha256

    def reverify(self) -> None:
        """Lock stage: re-hash the pack on disk and require the hash taken at load time."""
        current = verify_manifest(self._root)
        if current != self._manifest_sha256:
            raise PackIntegrityError(f"pack {self.id} changed since load ({self._manifest_sha256[:12]} -> {current[:12]})")

    def copy_for_sandbox(self, dest: Path) -> Path:
        """Fresh copy of the pack for one sandbox call (excludes reference.v and the manifest). Re-verifies first."""
        self.reverify()
        dest.mkdir(parents=True, exist_ok=False)
        for rel in manifest_entries(self._root):
            if rel in SANDBOX_EXCLUDE:
                continue
            src = self._root / rel
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)
        return dest

    def sandbox_files(self) -> dict[str, bytes]:
        """Same file set as copy_for_sandbox, in memory (for sandbox backends that upload files)."""
        self.reverify()
        return {
            rel: (self._root / rel).read_bytes()
            for rel in manifest_entries(self._root)
            if rel not in SANDBOX_EXCLUDE
        }

    # ---------- views ----------
    @property
    def live_preset(self) -> bool:
        return not self.eval_only

    def port(self, name: str) -> TaskPort | None:
        return next((p for p in self.ports if p.name == name), None)

    def to_spec(self, priority: Priority) -> Spec:
        return Spec(
            preset_id=self.id,
            title=self.title,
            text=self.spec_text,
            ports=[p.to_contract() for p in self.ports],
            priority=priority,
            test_count=self.test_count,
            max_lines=self.max_lines,
        )

    def module_header(self) -> str:
        """The exact ANSI module header the LLM must use. Every port is `logic`; outputs are never redeclared `reg`."""
        decls = [f"    {p.verilog_decl()}" for p in self.ports]
        lane_notes = [
            f"// {p.name}: {p.count} lanes x {p.width} bits, lane i = {p.name}[i*{p.width} +: {p.width}]"
            for p in self.ports
            if p.count > 1
        ]
        head = "\n".join(lane_notes)
        body = f"module {self.top} (\n" + ",\n".join(decls) + "\n);"
        return f"{head}\n{body}" if head else body

    def rules(self) -> list[str]:
        return [*CODING_RULES, *self.coding_rules]

    def spec_rules(self) -> list[str]:
        """The decisions this locked pack makes, in plain words, for the spec/rules.json artifact: the pack's own
        coding and protocol rules first, then clock, reset, latency and lane layout. Never golden.py, test_dut.py or
        reference.v (the same text the model already gets)."""
        out = list(self.coding_rules)
        if self.clock.name:
            out.append(f"One clock, {self.clock.name}; registers change on its rising edge only.")
        else:
            out.append("No clock: the outputs follow the inputs directly (combinational).")
        if self.reset.name:
            polarity = "active low" if self.reset.active_low else "active high"
            kind = "synchronous" if self.reset.synchronous else "asynchronous"
            out.append(f"Reset {self.reset.name} is {polarity} and {kind}.")
        if self.clock.name:
            cycles = "cycle" if self.latency_cycles == 1 else "cycles"
            out.append(f"Latency is {self.latency_cycles} {cycles}, fixed: every version must keep it.")
        for port in self.ports:
            if port.count > 1:
                out.append(
                    f"{port.name} carries {port.count} lanes of {port.width} bits; lane i is {port.name}[i*{port.width} +: {port.width}]."
                )
        return out

    def public_brief(self) -> str:
        """Everything the LLM may see about the pack. Never golden.py, test_dut.py or reference.v."""
        clock = f"clock `{self.clock.name}` (rising edge)" if self.clock.name else "no clock (combinational)"
        if self.reset.name:
            polarity = "active low" if self.reset.active_low else "active high"
            kind = "synchronous" if self.reset.synchronous else "asynchronous"
            reset = f"reset `{self.reset.name}` ({polarity}, {kind})"
        else:
            reset = "no reset"
        lines = [
            f"# Task: {self.title}",
            "",
            self.spec_text,
            "",
            f"- Top module: `{self.top}`",
            f"- Element type: {self.element_type}",
            f"- Latency: {self.latency_cycles} cycles (fixed; optimized versions must keep it)",
            f"- {clock}; {reset}",
            f"- Maximum {self.max_lines} non-blank lines",
            "",
            "## Module header (use exactly)",
            "```verilog",
            self.module_header(),
            "```",
            "",
            "## Coding rules",
            *[f"- {rule}" for rule in self.rules()],
        ]
        return "\n".join(lines)

    def validate_rtl(self, text: str, *, max_bytes: int = DEFAULT_MAX_BYTES) -> Rtl:
        """Raises RtlRejected with the list of problems."""
        return Rtl.validate(text, top=self.top, max_lines=self.max_lines, max_bytes=max_bytes)

    def golden_source(self) -> str:
        """Golden model source for the UI artifact tests/golden_model.py (the UI may show it; the LLM never sees it)."""
        return (self._root / GOLDEN_FILE).read_text(encoding="utf-8")

    def testbench_source(self) -> str:
        return (self._root / TESTBENCH_FILE).read_text(encoding="utf-8")

    def reference_rtl(self) -> str | None:
        """reference.v for evals, pack self-tests and the human_ref measurement. Never passed to a role or a sandbox."""
        path = self._root / REFERENCE_FILE
        return path.read_text(encoding="utf-8") if path.is_file() else None


# ---------- manifest helpers ----------
def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _pack_files(root: Path) -> list[str]:
    out: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel == MANIFEST_NAME or path.suffix == ".pyc" or any(part in _IGNORED_PARTS for part in path.parts):
            continue
        out.append(rel)
    return out


def _parse_manifest(root: Path) -> dict[str, str]:
    path = root / MANIFEST_NAME
    if not path.is_file():
        raise PackIntegrityError(f"{root}: missing {MANIFEST_NAME}")
    entries: dict[str, str] = {}
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        digest, _, rel = line.partition(" ")
        rel = rel.strip().lstrip("*")
        if len(digest) != 64 or not rel:
            raise PackIntegrityError(f"{path}:{n}: expected '<sha256>  <relative path>'")
        if rel.startswith("/") or ".." in Path(rel).parts:
            raise PackIntegrityError(f"{path}:{n}: unsafe path {rel!r}")
        entries[rel] = digest.lower()
    return entries


def manifest_entries(root: Path) -> list[str]:
    return sorted(_parse_manifest(root))


def verify_manifest(root: Path) -> str:
    """Check every file against MANIFEST.sha256. Returns the pack hash. Raises PackIntegrityError."""
    entries = _parse_manifest(root)
    on_disk = set(_pack_files(root))
    missing = sorted(set(entries) - on_disk)
    extra = sorted(on_disk - set(entries))
    if missing:
        raise PackIntegrityError(f"{root}: files listed but missing: {missing}")
    if extra:
        raise PackIntegrityError(f"{root}: files not in {MANIFEST_NAME}: {extra}")
    bad = [rel for rel, digest in entries.items() if _hash_file(root / rel) != digest]
    if bad:
        raise PackIntegrityError(f"{root}: hash mismatch for {bad}")
    if TASK_FILE not in entries:
        raise PackIntegrityError(f"{root}: {TASK_FILE} must be listed in {MANIFEST_NAME}")
    return hashlib.sha256((root / MANIFEST_NAME).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def write_manifest(root: Path) -> str:
    """Author-side helper (pack creation and tests; a run only verifies the manifest). Rewrites MANIFEST.sha256; returns the pack hash."""
    root = root.resolve()
    lines = [f"{_hash_file(root / rel)}  {rel}" for rel in _pack_files(root)]
    (root / MANIFEST_NAME).write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    return verify_manifest(root)


def load_packs(tasks_dir: Path) -> dict[str, TaskPack]:
    """Load every tasks/<id>/ that has a task.toml. Any broken pack raises (fail fast at startup)."""
    packs: dict[str, TaskPack] = {}
    if not tasks_dir.is_dir():
        return packs
    for child in sorted(tasks_dir.iterdir()):
        if child.is_dir() and (child / TASK_FILE).is_file():
            pack = TaskPack.load(child)
            packs[pack.id] = pack
    return packs


def load_custom_packs(custom_dir: Path) -> dict[str, TaskPack]:
    """Load every custom_packs/<id>/ approved in an earlier session (CE3), so one survives a restart. Unlike
    load_packs, a pack that fails to load is skipped and logged, never aborts startup: a custom pack is not a
    locked one, and one bad folder must not take the whole server down."""
    packs: dict[str, TaskPack] = {}
    if not custom_dir.is_dir():
        return packs
    for child in sorted(custom_dir.iterdir()):
        if not (child.is_dir() and (child / TASK_FILE).is_file()):
            continue
        try:
            pack = TaskPack.load(child)
        except (PackFormatError, PackIntegrityError) as exc:
            log.warning("custom pack %s could not be loaded, skipped: %s", child.name, exc)
            continue
        packs[pack.id] = pack
    return packs
