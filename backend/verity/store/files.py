"""Write-once artifact files under data_dir (ARCHITECTURE 15).

    data/blobs/<sha[0:2]>/<sha>          content-addressed, shared
    data/runs/<run_id>/<relpath>         per-run path, a hard link to the blob (copy if links are unsupported)

Every path is generated from validated ids and checked against its root before use. Nothing a model or a
client wrote ever becomes a path. These functions are blocking; the store calls them via asyncio.to_thread.
"""

import hashlib
import os
import re
import tempfile
from contextlib import suppress
from pathlib import Path

_RELPATH_RE = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_RELPATH = 200


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_run_id(run_id: str) -> str:
    if not _RUN_ID_RE.fullmatch(run_id):
        raise ValueError("invalid run id")
    return run_id


def check_relpath(relpath: str) -> str:
    """relpath must match ^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$ and no segment may be made only of dots."""
    if len(relpath) > _MAX_RELPATH or not _RELPATH_RE.fullmatch(relpath):
        raise ValueError("invalid artifact path")
    for segment in relpath.split("/"):
        if segment.strip(".") == "":
            raise ValueError("invalid artifact path")
    return relpath


class ArtifactFiles:
    def __init__(self, data_dir: Path) -> None:
        self.root = Path(data_dir).resolve()
        self.blobs = self.root / "blobs"
        self.runs = self.root / "runs"

    def ensure_dirs(self) -> None:
        self.blobs.mkdir(parents=True, exist_ok=True)
        self.runs.mkdir(parents=True, exist_ok=True)

    # ---------- blobs ----------
    def blob_path(self, sha: str) -> Path:
        if not _SHA_RE.fullmatch(sha):
            raise ValueError("invalid blob hash")
        return self.blobs / sha[:2] / sha

    def put_blob(self, data: bytes) -> tuple[str, Path]:
        """Idempotent: identical content is stored once."""
        sha = sha256_hex(data)
        path = self.blob_path(sha)
        if path.exists():
            return sha, path
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            try:
                os.link(tmp, path)
            except FileExistsError:
                pass  # another writer stored the same content first
            except OSError:
                # Hard links unsupported here: move the temp file into place unless someone beat us to it.
                if not path.exists():
                    os.replace(tmp, path)
        finally:
            with suppress(OSError):
                tmp.unlink()
        return sha, path

    # ---------- per-run files ----------
    def _run_path(self, run_id: str, relpath: str) -> Path:
        check_run_id(run_id)
        check_relpath(relpath)
        base = (self.runs / run_id).resolve()
        target = (base / relpath).resolve()
        if not target.is_relative_to(base):
            raise ValueError("artifact path escapes the run directory")
        return target

    def write_run_file(self, run_id: str, relpath: str, data: bytes, *, overwrite: bool = False) -> tuple[str, Path]:
        """Create data/runs/<run_id>/<relpath>. An existing path with different content raises FileExistsError
        unless overwrite is True; identical content is accepted (idempotent retry)."""
        target = self._run_path(run_id, relpath)
        sha, blob = self.put_blob(data)
        target.parent.mkdir(parents=True, exist_ok=True)
        if overwrite and target.exists():
            tmp = target.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(target)
            return sha, target
        if target.exists():
            self._require_same(target, sha, run_id, relpath)
            return sha, target
        try:
            os.link(blob, target)
        except FileExistsError:
            self._require_same(target, sha, run_id, relpath)
        except OSError:
            try:
                with open(target, "xb") as fh:
                    fh.write(data)
            except FileExistsError:
                self._require_same(target, sha, run_id, relpath)
        return sha, target

    @staticmethod
    def _require_same(target: Path, sha: str, run_id: str, relpath: str) -> None:
        if sha256_hex(target.read_bytes()) != sha:
            raise FileExistsError(f"artifact already exists with different content: runs/{run_id}/{relpath}")

    def read_run_file(self, run_id: str, relpath: str) -> bytes | None:
        target = self._run_path(run_id, relpath)
        try:
            return target.read_bytes()
        except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
            return None
