"""Folder restriction, working copies and model revisions."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import time
from pathlib import Path

from . import errors
from .errors import AdapterError


def _real(path: str | os.PathLike) -> Path:
    # realpath follows symlinks and, on Windows, junctions / reparse points, so the
    # containment check below is made on the place the file really lives.
    return Path(os.path.realpath(os.path.expanduser(str(path))))


def is_inside(path: str | os.PathLike, root: str | os.PathLike) -> bool:
    p, r = _real(path), _real(root)
    try:
        return os.path.commonpath([os.path.normcase(p), os.path.normcase(r)]) == os.path.normcase(r)
    except ValueError:  # different drives
        return False


def require_inside(path: str | os.PathLike, roots: list[Path], purpose: str) -> Path:
    resolved = _real(path)
    for root in roots:
        if is_inside(resolved, root):
            return resolved
    allowed = "; ".join(str(_real(r)) for r in roots) or "(none configured)"
    raise AdapterError(
        errors.NOT_ALLOWED,
        f"Path '{resolved}' is outside the folders allowed for {purpose}.",
        f"Allowed: {allowed}. Add a folder with STRUCT_MCP_MODEL_DIRS or move the file.",
    )


_SAFE_NAME = re.compile(r"[^\w.\-]+", re.UNICODE)


def safe_name(name: str, default: str = "file") -> str:
    cleaned = _SAFE_NAME.sub("_", Path(name).name).strip("._")
    return cleaned or default


def file_revision(path: str | os.PathLike) -> dict | None:
    """Identify the saved state of a model file: content hash + modification time."""
    p = Path(path)
    if not p.is_file():
        return None
    digest = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    stat = p.stat()
    return {
        "id": digest.hexdigest()[:16],
        "modified": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(stat.st_mtime)),
        "size_bytes": stat.st_size,
    }


def make_working_copy(source: Path, workdir: Path, program: str, companions: tuple[str, ...] = ()) -> Path:
    """Copy a model into <workdir>/copies/<program>/<stem>__<timestamp>/ and return the copy.

    `companions` are extra extensions (e.g. result files) copied when they sit next
    to the model with the same stem. The source is never modified.
    """
    if not source.is_file():
        raise AdapterError(errors.INVALID_INPUT, f"Model file not found: {source}")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target_dir = workdir / "copies" / program / f"{safe_name(source.stem)}__{stamp}"
    target_dir.mkdir(parents=True, exist_ok=False)
    target = target_dir / source.name
    shutil.copy2(source, target)
    for ext in companions:
        side = source.with_suffix(ext)
        if side.is_file():
            shutil.copy2(side, target_dir / side.name)
    return target
