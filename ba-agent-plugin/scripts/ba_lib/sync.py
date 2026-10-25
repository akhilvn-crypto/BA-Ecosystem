"""Cowork file-bridge support: which project files a command wrote, and a path + sha256 manifest.

In a cloud workspace the project folder is a staged copy of the user's own folder. Only files committed back reach the user,
and only fresh staged copies let `changes check` see the user's own edits. So a command can report exactly the files it wrote,
moved or removed (`--changed-files`), and `manifest` can compare the staged copy with the last committed state.

Every writer is covered because the report is a before/after comparison of the project tree, not a list each command keeps.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .util import atomic_write_text, canonical_json, read_json

MANAGED = ("state", "reports", "snapshots", "source", "extracted", "atomic-requirements", "requirements", "decisions", "baselines",
           "changes", "gaps", "stories")
TOP_FILES = ("START-HERE.md", "ADR-LOG.md")
MANIFEST = "state/manifest.json"
PROBE = "state/.write-probe"


def _walk(root: Path):
    for top in MANAGED:
        base = root / top
        if base.is_dir():
            for dp, dns, fns in os.walk(base):
                dns[:] = [d for d in dns if d != "__pycache__"]
                for f in fns:
                    yield Path(dp) / f
    for f in TOP_FILES:
        if (root / f).is_file():
            yield root / f


def _rel(root: Path, f: Path) -> str:
    return f.relative_to(root).as_posix()


def sig(root) -> dict[str, tuple[int, int]]:
    """Cheap fingerprint of the project tree: relative path -> (size, mtime_ns)."""
    root = Path(root)
    out = {}
    if root.is_dir():
        for f in _walk(root):
            try:
                st = f.stat()
            except OSError:
                continue
            out[_rel(root, f)] = (st.st_size, st.st_mtime_ns)
    return out


def changed(before: dict, after: dict) -> dict:
    """Files this command wrote (new or modified) and removed. A move shows as one removal plus one new path."""
    written = sorted(p for p, v in after.items() if before.get(p) != v and p != PROBE)
    removed = sorted(p for p in before if p not in after and p != PROBE)
    return {"changed_files": written, "removed_files": removed}


def _sha(f: Path) -> str:
    h = hashlib.sha256()
    with open(f, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def hashes(root) -> dict[str, str]:
    root = Path(root)
    return {_rel(root, f): _sha(f) for f in sorted(_walk(root)) if _rel(root, f) not in (MANIFEST, PROBE)}


def manifest(root, write: bool = False, full: bool = False) -> dict:
    """Compare the project tree with the manifest committed at the end of the last stage (`state/manifest.json`).

    Default output is small: counts, the tree hash and the drift. `--full` lists every hash. `--write` records the current
    tree as the new committed state (call it after the files of a stage have been committed back)."""
    root = Path(root)
    now = hashes(root)
    stored_path = root / MANIFEST
    stored = None
    if stored_path.is_file():
        try:
            stored = read_json(stored_path).get("files")
        except Exception:
            stored = None
    out = {"count": len(now), "tree_hash": hashlib.sha256(canonical_json(now).encode("utf-8")).hexdigest()}
    if stored is None:
        out["committed_manifest"] = "none yet: run `manifest --write` after the first stage is committed back"
    else:
        drift = {"edited": sorted(p for p in now if p in stored and now[p] != stored[p]),
                 "added": sorted(p for p in now if p not in stored),
                 "missing": sorted(p for p in stored if p not in now)}
        out["in_sync"] = not any(drift.values())
        out["drift"] = drift
    if full:
        out["files"] = now
    if write:
        atomic_write_text(stored_path, canonical_json({"files": now}) + "\n")
        out["written"] = MANIFEST
    return out
