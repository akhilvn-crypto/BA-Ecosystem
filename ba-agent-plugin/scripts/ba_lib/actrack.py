"""Approved-AC tracking: content hashes, an immutable snapshot of every approved version and field-level diffs.

What counts as a *material* change is what a reviewer approved: the acceptance criteria, the declared open gaps, assumptions
and notes. Whitespace, Markdown decoration, review history, timestamps and derived fields are formatting / bookkeeping and never
need re-approval.
"""
from __future__ import annotations

import re

from . import ingest, render
from .project import ac_fname
from .util import canonical_json, ist_now_iso, sha256_text

AC_DIR = "snapshots/ac"
AC_INDEX = f"{AC_DIR}/index.json"
VOLATILE = ("updated_at", "review_history", "impact_review")  # the workflow itself changes these on an approved record
TRACKED, DELETED, REMOVAL_CONFIRMED = "TRACKED", "DELETED_EXTERNALLY", "REMOVAL_CONFIRMED"


def _n(v) -> str:
    return ingest.material_normalize(v if isinstance(v, str) else "")


def material(a: dict) -> dict:
    ud = sorted((u.get("gap_id", ""), u.get("readiness_effect", ""), _n(u.get("potential_impact")), _n(u.get("interim_assumption") or ""))
                for u in (a.get("open_gap_refs") or []))
    notes = {k: _n(v) for k, v in sorted((a.get("notes") or {}).items()) if isinstance(v, str) and _n(v)}
    return {"acceptance_criteria": [_n(c) for c in a.get("acceptance_criteria") or []],
            "assumptions": sorted(_n(x) for x in a.get("assumptions") or []), "open_gap_refs": ud, "notes": notes}


def material_hash(a: dict) -> str:
    return sha256_text(canonical_json(material(a)))


def content_hash(a: dict) -> str:
    return sha256_text(canonical_json({k: v for k, v in a.items() if k not in VOLATILE}))


def approval_hash(a: dict) -> str:
    return sha256_text(canonical_json({"status": a.get("status"), "version": a.get("version"), "approval": a.get("approval"),
                                       "readiness": a.get("readiness")}))


# ------------------------------------------------------------------ index
def index(p) -> dict:
    return p.read_json(AC_INDEX) if p.exists(AC_INDEX) else {"schema_version": "2.0", "acs": {}}


def _save(p, idx: dict) -> None:
    p.write_json(AC_INDEX, idx)


def entry(p, key: str) -> dict | None:
    return index(p)["acs"].get(key)


def known_keys(p) -> list[str]:
    return sorted(index(p)["acs"])


def current(e: dict) -> dict:
    return e["versions"][-1]


def snapshot_path(key: str, version: int) -> str:
    return f"{AC_DIR}/{ac_fname(key)}.v{version}.json"


def record_approved(p, a: dict, event: str = "APPROVED") -> dict:
    """Remember exactly what was approved: hashes plus an immutable copy of the approved record."""
    ap = a.get("approval") or {}
    snap = snapshot_path(a["req_key"], a["version"])
    if not p.exists(snap):
        p.write_json(snap, a)
        p.write_text(snap[:-5] + ".md", render.ac_md(a, p), backup=False)
    ver = {"version": a["version"], "event": event, "material_hash": material_hash(a), "content_hash": content_hash(a),
           "approval_hash": approval_hash(a), "snapshot": snap, "approved_at_ist": ap.get("at_ist"),
           "approver_name": ap.get("approver_name"), "approver_role": ap.get("approver_role"),
           "statement": ap.get("statement"), "recorded_at_ist": ist_now_iso()}
    idx = index(p)
    e = idx["acs"].setdefault(a["req_key"], {"status": TRACKED, "versions": []})
    e["versions"] = [v for v in e["versions"] if v["version"] != a["version"]] + [ver]
    e["versions"].sort(key=lambda v: v["version"])
    e["status"] = TRACKED
    _save(p, idx)
    return ver


def update_entry(p, key: str, **fields) -> None:
    idx = index(p)
    idx["acs"][key].update(fields)
    _save(p, idx)


def update_version(p, key: str, version: int, **fields) -> None:
    idx = index(p)
    for v in idx["acs"][key]["versions"]:
        if v["version"] == version:
            v.update(fields)
    _save(p, idx)


def snapshot_record(p, ver: dict) -> dict | None:
    """The approved record as it was approved, or None when the snapshot is missing or fails its hash."""
    if not p.exists(ver["snapshot"]):
        return None
    rec = p.read_json(ver["snapshot"])
    return rec if content_hash(rec) == ver["content_hash"] else None


# ------------------------------------------------------------------ diff
def _list_diff(old: list[str], new: list[str]) -> dict | None:
    on, nn = [_n(x) for x in old], [_n(x) for x in new]
    added = [x for x, k in zip(new, nn) if k not in on]
    removed = [x for x, k in zip(old, on) if k not in nn]
    if added or removed:
        return {"added": added, "removed": removed}
    if on != nn and sorted(on) == sorted(nn):
        return {"added": [], "removed": [], "reordered": True}
    return None


def diff(old: dict, new: dict) -> dict:
    """Field-level material differences between two AC records ({} when only formatting differs)."""
    out: dict = {}
    for f in ("acceptance_criteria", "assumptions"):
        d = _list_diff(old.get(f) or [], new.get(f) or [])
        if d and not (f == "assumptions" and d.get("reordered")):
            out[f] = d
    mo, mn = material(old)["open_gap_refs"], material(new)["open_gap_refs"]
    if mo != mn:
        go, gn = {x[0] for x in mo}, {x[0] for x in mn}
        out["open_gap_refs"] = {"added": sorted(gn - go), "removed": sorted(go - gn),
                                "modified": sorted(g for g in go & gn if [x for x in mo if x[0] == g] != [x for x in mn if x[0] == g])}
    if material(old)["notes"] != material(new)["notes"]:
        out["notes"] = {"before": old.get("notes"), "after": new.get("notes")}
    return out


def summarize_diff(d: dict) -> str:
    parts = []
    for k, v in d.items():
        if k == "acceptance_criteria":
            parts.append(f"acceptance criteria (+{len(v['added'])}/-{len(v['removed'])}{', reordered' if v.get('reordered') else ''})")
        else:
            parts.append(k.replace("_", " "))
    return "; ".join(parts) or "formatting only"
