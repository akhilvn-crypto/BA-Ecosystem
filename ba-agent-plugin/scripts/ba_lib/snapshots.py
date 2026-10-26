"""Source snapshots: the exact bytes of every accepted version of every requirement file.

The supplied files are never touched. A snapshot is a verbatim copy kept under `snapshots/source/`, with an index that records
the hash of each accepted version. Two things rest on it: a later edit can be compared with what the analysis was based on, and
the enriched copy is always rendered from the accepted snapshot, never from a file that may have changed since.
"""
from __future__ import annotations

from .util import atomic_write_bytes, ist_now_iso, sha256_bytes

SRC_DIR = "snapshots/source"
SRC_INDEX = f"{SRC_DIR}/index.json"


def index(p) -> dict:
    return p.read_json(SRC_INDEX) if p.exists(SRC_INDEX) else {"schema_version": "2.0", "files": {}}


def snapshot_name(rel: str, n: int) -> str:
    return f"{SRC_DIR}/{rel.replace('/', '__')}@v{n}.md"


def versions(p, rel: str) -> list[dict]:
    return index(p)["files"].get(rel, [])


def latest(p, rel: str) -> dict | None:
    v = versions(p, rel)
    return v[-1] if v else None


def read_bytes(p, entry: dict | None) -> bytes | None:
    """The snapshot bytes, or None when the copy is missing or no longer matches its recorded hash."""
    if not entry or not p.exists(entry["file"]):
        return None
    data = p.path(entry["file"]).read_bytes()
    return data if sha256_bytes(data) == entry["file_hash"] else None


def text_of(p, entry: dict | None) -> str | None:
    data = read_bytes(p, entry)
    return None if data is None else data.decode("utf-8")


def accepted_text(p, rel: str) -> str | None:
    return text_of(p, latest(p, rel))


def record(p, rel: str, data: bytes, reason: str, change_id: str | None = None) -> dict:
    """Store `data` as the next accepted version of `rel` (idempotent for identical bytes)."""
    idx = index(p)
    vs = idx["files"].setdefault(rel, [])
    h = sha256_bytes(data)
    if vs and vs[-1]["file_hash"] == h:
        return vs[-1]
    n = (vs[-1]["version"] + 1) if vs else 1
    entry = {"version": n, "file": snapshot_name(rel, n), "file_hash": h, "bytes": len(data), "recorded_at_ist": ist_now_iso(),
             "reason": reason, "change_id": change_id}
    target = p.path(entry["file"])
    atomic_write_bytes(target, data)
    vs.append(entry)
    p.write_json(SRC_INDEX, idx)
    p.audit("SOURCE_SNAPSHOT_RECORDED", file=rel, version=n, file_hash=h, reason=reason, change_id=change_id)
    return entry
