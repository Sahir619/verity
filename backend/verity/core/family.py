"""Knob params: a pack with a family.py can be rendered at given knob values into a cached, ordinary TaskPack.

tasks/<id>/family.py exports KNOBS (list of dicts, see Knob) and render(params) -> {file name: full text} for any of
task.toml, reference.v and params.json. render_pack copies the rest of the pack (golden.py, test_dut.py, ...), writes the
rendered files, regenerates the manifest and loads the result like any other pack. At the default knobs (or for a pack
without a family.py) the base pack is returned unchanged, so default runs keep today's pack hash.
"""

import hashlib
import importlib.util
import json
import shutil
import uuid
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from .errors import PackFormatError, PackIntegrityError
from .pack import MANIFEST_NAME, TaskPack, _pack_files, write_manifest

FAMILY_FILE = "family.py"
ParamValue = int | bool | str


class Knob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["int", "bool", "bits"]
    default: int | bool | str
    min: int | None = None
    max: int | None = None
    min_len: int | None = None
    max_len: int | None = None

    @model_validator(mode="after")
    def _validate_default(self) -> "Knob":
        _check_knob(self, self.default)
        return self


class ParamsError(ValueError):
    """The message is a sentence the user can read as is."""


def _check_knob(knob: Knob, value: object) -> ParamValue:
    if knob.kind == "int":
        lo, hi = knob.min, knob.max
        ok = isinstance(value, int) and not isinstance(value, bool) and (lo is None or value >= lo) and (hi is None or value <= hi)
        if not ok:
            if lo is not None and hi is not None:
                raise ParamsError(f"{knob.id} must be a whole number from {lo} to {hi}.")
            raise ParamsError(f"{knob.id} must be a whole number.")
        return int(value)  # type: ignore[arg-type]
    if knob.kind == "bool":
        if not isinstance(value, bool):
            raise ParamsError(f"{knob.id} must be true or false.")
        return value
    lo, hi = knob.min_len or 1, knob.max_len
    if not isinstance(value, str) or not value or set(value) - {"0", "1"} or len(value) < lo or (hi is not None and len(value) > hi):
        span = f"{lo} to {hi}" if hi is not None else f"at least {lo}"
        raise ParamsError(f"{knob.id} must be {span} characters, each 0 or 1.")
    return value


def validate_params(knobs: list[Knob], raw: Mapping[str, object] | None) -> dict[str, ParamValue]:
    """Fill defaults, reject unknown keys, wrong types (a bool is not an int) and out-of-range values."""
    raw = dict(raw or {})
    by_id = {k.id: k for k in knobs}
    unknown = sorted(set(raw) - set(by_id))
    if unknown:
        raise ParamsError(f"Unknown setting: {', '.join(unknown)}. Valid settings are {', '.join(by_id)}.")
    return {k.id: _check_knob(k, raw[k.id]) if k.id in raw else k.default for k in knobs}


def family_module(pack: TaskPack) -> ModuleType | None:
    """Import <pack root>/family.py by path; None when the pack has none."""
    path = pack.root / FAMILY_FILE
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(f"verity_family_{pack.id}", path)
    if spec is None or spec.loader is None:
        raise PackFormatError(f"{path}: cannot import")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _cache_key(pack: TaskPack, params: Mapping[str, ParamValue]) -> str:
    canonical = json.dumps({"base": pack.sha256, "params": dict(params)}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _render_into(pack: TaskPack, module: ModuleType, params: dict[str, ParamValue], dest: Path) -> None:
    dest.mkdir(parents=True)
    for rel in _pack_files(pack.root):
        if rel == FAMILY_FILE:
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pack.root / rel, target)
    files = module.render(dict(params))
    for name, text in files.items():
        (dest / name).write_bytes(text.replace("\r\n", "\n").encode("utf-8"))
    (dest / MANIFEST_NAME).unlink(missing_ok=True)
    write_manifest(dest)


def render_pack(pack: TaskPack, params: Mapping[str, object] | None, cache_dir: Path) -> TaskPack:
    """The pack at these knob values. Raises ParamsError for bad params."""
    module = family_module(pack)
    if module is None:
        if params:
            raise ParamsError("This chip has no settings.")
        return pack
    knobs = [Knob(**k) for k in module.KNOBS]
    values = validate_params(knobs, params)
    if values == {k.id: k.default for k in knobs}:
        return pack
    folder = Path(cache_dir) / f"{pack.id}-{_cache_key(pack, values)}"
    dest = folder / pack.id
    if dest.is_dir():
        try:
            return TaskPack.load(dest)
        except (PackFormatError, PackIntegrityError):
            shutil.rmtree(folder, ignore_errors=True)
    staging = Path(cache_dir) / f".tmp-{uuid.uuid4().hex}"
    try:
        _render_into(pack, module, values, staging / pack.id)
        try:
            staging.rename(folder)
        except OSError:
            if not dest.is_dir():  # a concurrent render may have won the rename; otherwise it is a real failure
                raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    prune_render_cache(cache_dir)
    return TaskPack.load(dest)


def prune_render_cache(cache_dir: Path, max_entries: int = 500) -> int:
    """Keep the render cache bounded. Removes the oldest rendered folders when over max_entries."""
    cache = Path(cache_dir)
    if not cache.is_dir():
        return 0
    folders = [f for f in cache.iterdir() if f.is_dir() and not f.name.startswith(".tmp-")]
    if len(folders) <= max_entries:
        return 0
    folders.sort(key=lambda p: p.stat().st_mtime)
    to_remove = folders[: len(folders) - max_entries]
    removed = 0
    for folder in to_remove:
        shutil.rmtree(folder, ignore_errors=True)
        removed += 1
    return removed
