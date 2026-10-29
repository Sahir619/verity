#!/usr/bin/env python3
"""Regenerate MANIFEST.sha256 for task packs (author-side tool; never run at run time).

Usage (from anywhere):
    python backend/tasks/make_manifest.py             # rewrite every pack's manifest, then load each pack
    python backend/tasks/make_manifest.py mac4 alu8   # only these packs
    python backend/tasks/make_manifest.py --check     # verify only, change nothing

Hashes are SHA-256 over file bytes with CRLF normalized to LF, so a Windows checkout and a Linux checkout agree
(backend/.gitattributes also forces LF for tasks/). The pack hash recorded on a run is the SHA-256 of the manifest.
Loading a pack with TaskPack.load also validates task.toml against the schema (unknown keys are errors).

Re-run this after ANY edit inside a pack folder; a stale manifest makes the backend refuse to start.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

TASKS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TASKS_DIR.parent))

from verity.core.types import PackFormatError, PackIntegrityError, TaskPack, verify_manifest, write_manifest  # noqa: E402


def pack_dirs(selected: list[str]) -> list[Path]:
    found = sorted(p for p in TASKS_DIR.iterdir() if p.is_dir() and (p / "task.toml").is_file())
    if not selected:
        return found
    names = {p.name: p for p in found}
    missing = [s for s in selected if s not in names]
    if missing:
        sys.exit(f"unknown pack(s): {', '.join(missing)} (have: {', '.join(names)})")
    return [names[s] for s in selected]


def main() -> int:
    parser = argparse.ArgumentParser(description="Regenerate or verify task pack manifests.")
    parser.add_argument("packs", nargs="*", help="pack ids (default: all)")
    parser.add_argument("--check", action="store_true", help="verify only; do not rewrite manifests")
    args = parser.parse_args()

    failed = False
    for root in pack_dirs(args.packs):
        try:
            pack_hash = verify_manifest(root) if args.check else write_manifest(root)
            pack = TaskPack.load(root)
        except (PackIntegrityError, PackFormatError) as exc:
            print(f"FAIL {root.name}: {exc}")
            failed = True
            continue
        kind = "eval-only" if pack.eval_only else "live"
        print(f"ok   {root.name:10s} {kind:9s} tests={pack.test_count:<3d} latency={pack.latency_cycles:<3d} {pack_hash}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
