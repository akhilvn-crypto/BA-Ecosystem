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
# Local recovery copies and transient helper input: never committed back, so never reported or hashed.
LOCAL_ONLY = ("state/backups", "state/inbox")


def _walk(root: Path):
    skip = {(root / d).resolve() for d in LOCAL_ONLY}
    for top in MANAGED:
        base = root / top
        if base.is_dir():
            for dp, dns, fns in os.walk(base):
                dns[:] = [d for d in dns if d != "__pycache__" and (Path(dp) / d).resolve() not in skip]
                for f in fns:
                    yield Path(dp) / f
    for f in TOP_FILES:
        if (root / f).is_file():
            yield root / f


def _stage(rel: str) -> bool:
    """Does a session need this file staged? The core reads records (`json/`) and state, never the generated `md/` views, and the
    approved stories' `md/` are the user-editable copies that `changes check` compares. Those same files are what `manifest` hashes."""
    parts = rel.split("/")
    if parts[-1] in TOP_FILES:  # generated views, rewritten by every writing command
        return False
    if parts[0] in ("state", "snapshots", "source", "extracted") or "json" in parts[:-1]:
        return True
    return rel.startswith("stories/approved/md/")


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


BRIDGE = "state/bridge.json"


def bridge_on(root) -> bool:
    """Is bridge mode recorded for this project? Shell state does not persist between calls, so the switch lives in `state/`."""
    try:
        return bool(root) and bool(read_json(Path(root) / BRIDGE).get("on"))
    except Exception:
        return False


def bridge(root, mode: str | None = None) -> dict:
    """`bridge on|off` records whether every command reports `changed_files` / `removed_files` (Cowork); no argument shows it."""
    root = Path(root)
    if mode:
        atomic_write_text(root / BRIDGE, canonical_json({"on": mode == "on"}) + "\n")
    on = bridge_on(root)
    return {"bridge": "on" if on else "off",
            "note": "Every command now reports changed_files and removed_files: commit exactly those back." if on
                    else "Commands report changed_files only with --changed-files."}


def _sha(f: Path) -> str:
    h = hashlib.sha256()
    with open(f, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def stage_list(root) -> dict:
    """The project files to copy into a cloud workspace at session start (everything else is generated or local-only)."""
    root = Path(root)
    files = sorted(r for r in (_rel(root, f) for f in _walk(root)) if _stage(r) and r != PROBE)
    return {"stage": files, "count": len(files),
            "not_needed": "md/ views (except stories/approved/md/), reports/, START-HERE.md, ADR-LOG.md; state/backups/ and state/inbox/ are local only",
            "also": "the BRD at state.source.original_path (take a fresh copy), and any file the user says they edited"}


def hashes(root) -> dict[str, str]:
    root = Path(root)
    return {r: _sha(f) for f in sorted(_walk(root)) if _stage(r := _rel(root, f)) and r not in (MANIFEST, PROBE)}


def manifest(root, write: bool = False, full: bool = False, stage: bool = False) -> dict:
    """Compare the project tree with the manifest committed at the end of the last stage (`state/manifest.json`).

    It covers the files a session stages (`stage_list`), so a lean staged copy is in sync. Default output is small: counts, the
    tree hash and the drift. `--full` lists every hash. `--write` records the current tree as the new committed state (call it
    after the files of a stage have been committed back). `--stage-list` returns the files to stage instead."""
    root = Path(root)
    if stage:
        return stage_list(root)
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
