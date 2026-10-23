"""BRD snapshots: the text of every accepted BRD version, so a later edit can be compared line by line.

The original BRD file is never touched. A snapshot is the *extracted, normalised text* (what the analysis was based on),
stored under `snapshots/brd/` with an index that records the hashes of each accepted version.
"""
from __future__ import annotations

from . import ingest
from .util import ist_now_iso, sha256_text

BRD_DIR = "snapshots/brd"
BRD_INDEX = f"{BRD_DIR}/index.json"


def index(p) -> dict:
    if p.exists(BRD_INDEX):
        return p.read_json(BRD_INDEX)
    return {"schema_version": "1.0", "versions": []}


def latest(p) -> dict | None:
    v = index(p)["versions"]
    return v[-1] if v else None


def text_of(p, entry: dict | None) -> str | None:
    """The snapshot text, or None if it is missing or no longer matches its recorded hash (tampered / damaged)."""
    if not entry or not p.exists(entry["file"]):
        return None
    text = p.path(entry["file"]).read_text(encoding="utf-8")
    return text if sha256_text(text) == entry["text_fingerprint"] else None


def record(p, meta: dict, reason: str, change_id: str | None = None) -> dict:
    """Store `meta['text']` as the next accepted BRD version (idempotent for an identical fingerprint)."""
    idx = index(p)
    last = idx["versions"][-1] if idx["versions"] else None
    if last and last["fingerprint"] == meta["fingerprint"]:
        return last
    n = (last["version"] + 1) if last else 1
    entry = {"version": n, "file": f"{BRD_DIR}/brd-v{n}.txt", "fingerprint": meta["fingerprint"],
             "text_fingerprint": meta["text_fingerprint"], "material_fingerprint": meta.get("material_fingerprint"),
             "format": meta["format"], "sections": len(meta["sections"]), "recorded_at_ist": ist_now_iso(),
             "reason": reason, "change_id": change_id}
    p.write_text(entry["file"], meta["text"], backup=False)
    idx["versions"].append(entry)
    p.write_json(BRD_INDEX, idx)
    p.audit("BRD_SNAPSHOT_RECORDED", version=n, fingerprint=meta["fingerprint"], material_fingerprint=entry["material_fingerprint"],
            reason=reason, change_id=change_id)
    return entry


def accepted(p) -> dict:
    """What the analysis currently rests on: {text, sections, material_fingerprint, snapshot (entry|None), has_snapshot}.
    Sections are re-derived from the snapshot text, so they always carry material hashes even in projects created
    before change detection existed; without a usable snapshot the stored outline is used and nothing can be proved
    formatting-only."""
    entry = latest(p)
    text = text_of(p, entry)
    st = p.state["source"]
    if text is not None and entry["fingerprint"] == st["fingerprint"]:
        sections = ingest.parse_sections(text, entry["format"] if entry.get("format") else st.get("format", "markdown"))
        return {"text": text, "sections": sections, "material_fingerprint": ingest.material_fingerprint(sections),
                "snapshot": entry, "has_snapshot": True}
    meta = p.source_metadata()
    sections = meta["sections"]
    mf = ingest.material_fingerprint(sections) if all(s.get("material_hash") for s in sections) else None
    return {"text": None, "sections": sections, "material_fingerprint": mf, "snapshot": entry, "has_snapshot": False}


def backfill_if_unchanged(p, meta: dict) -> None:
    """Projects created before 1.5.0 have no snapshot. While the BRD still matches the accepted fingerprint, store it now."""
    if latest(p) is None and meta["fingerprint"] == p.state["source"]["fingerprint"]:
        record(p, meta, "backfilled: first change check on a project created before change detection")
