"""Approved-story tracking: content hashes, an immutable snapshot of every approved version, field-level diffs and
parsing of the readable Markdown copy (so a hand edit of either file is noticed).

What counts as a *material* story change is what a reviewer approved: title, the user story, the acceptance criteria,
the linked requirements, dependencies, assumptions, declared unresolved decisions and notes. Whitespace, Markdown
decoration, review history, timestamps and derived fields are formatting / bookkeeping and never need re-approval.
"""
from __future__ import annotations

import re

from . import ingest, render
from .util import canonical_json, ist_now_iso, normalize_ws, sha256_text

STORY_DIR = "snapshots/stories"
STORY_INDEX = f"{STORY_DIR}/index.json"
VOLATILE = ("updated_at", "review_history", "impact_review")  # the workflow itself changes these on an approved story
TRACKED, DELETED, REMOVAL_CONFIRMED = "TRACKED", "DELETED_EXTERNALLY", "REMOVAL_CONFIRMED"


def _n(v) -> str:
    return ingest.material_normalize(v if isinstance(v, str) else "")


def _trim(v) -> str:
    return re.sub(r"[\s.,;:]+$", "", _n(v))


def material(s: dict) -> dict:
    us = s.get("user_story") or {}
    ud = sorted((u.get("gap_id", ""), u.get("readiness_effect", ""), _n(u.get("potential_impact")), _n(u.get("interim_assumption") or ""))
                for u in (s.get("unresolved_decisions") or []))
    notes = {k: _n(v) for k, v in sorted((s.get("notes") or {}).items()) if isinstance(v, str) and _n(v)}
    return {"title": _n(s.get("title")), "user_story": {k: _trim(us.get(k)) for k in ("role", "capability", "value")},
            "acceptance_criteria": [_n(a) for a in s.get("acceptance_criteria") or []],
            "requirement_ids": sorted(set(s.get("requirement_ids") or [])),
            "dependencies": sorted(_n(x) for x in s.get("dependencies") or []),
            "assumptions": sorted(_n(x) for x in s.get("assumptions") or []),
            "unresolved_decisions": ud, "notes": notes}


def material_hash(s: dict) -> str:
    return sha256_text(canonical_json(material(s)))


def content_hash(s: dict) -> str:
    return sha256_text(canonical_json({k: v for k, v in s.items() if k not in VOLATILE}))


def approval_hash(s: dict) -> str:
    return sha256_text(canonical_json({"status": s.get("status"), "version": s.get("version"), "approval": s.get("approval"),
                                       "readiness": s.get("readiness")}))


# ------------------------------------------------------------------ index

def index(p) -> dict:
    return p.read_json(STORY_INDEX) if p.exists(STORY_INDEX) else {"schema_version": "1.0", "stories": {}}


def _save(p, idx: dict) -> None:
    p.write_json(STORY_INDEX, idx)


def entry(p, sid: str) -> dict | None:
    return index(p)["stories"].get(sid)


def known_ids(p) -> list[str]:
    return sorted(index(p)["stories"])


def current(e: dict) -> dict:
    return e["versions"][-1]


def snapshot_path(sid: str, version: int) -> str:
    return f"{STORY_DIR}/{sid}.v{version}.json"


def record_approved(p, s: dict, event: str = "APPROVED") -> dict:
    """Remember exactly what was approved: hashes plus an immutable copy of the approved record."""
    ap = s.get("approval") or {}
    snap = snapshot_path(s["story_id"], s["version"])
    if not p.exists(snap):
        p.write_json(snap, s)
        p.write_text(snap[:-5] + ".md", render.story_md(s), backup=False)
    ver = {"version": s["version"], "event": event, "material_hash": material_hash(s), "content_hash": content_hash(s),
           "approval_hash": approval_hash(s), "snapshot": snap, "approved_at_ist": ap.get("at_ist"),
           "approver_name": ap.get("approver_name"), "approver_role": ap.get("approver_role"),
           "statement": ap.get("statement"), "recorded_at_ist": ist_now_iso()}
    idx = index(p)
    e = idx["stories"].setdefault(s["story_id"], {"status": TRACKED, "versions": [], "accepted_md_hash": None})
    e["versions"] = [v for v in e["versions"] if v["version"] != s["version"]] + [ver]
    e["versions"].sort(key=lambda v: v["version"])
    e["status"], e["accepted_md_hash"] = TRACKED, None
    _save(p, idx)
    return ver


def update_entry(p, sid: str, **fields) -> None:
    idx = index(p)
    idx["stories"][sid].update(fields)
    _save(p, idx)


def update_version(p, sid: str, version: int, **fields) -> None:
    idx = index(p)
    for v in idx["stories"][sid]["versions"]:
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
    """Field-level material differences between two story records ({} when only formatting differs)."""
    out: dict = {}
    if _n(old.get("title")) != _n(new.get("title")):
        out["title"] = {"before": old.get("title"), "after": new.get("title")}
    ou, nu = old.get("user_story") or {}, new.get("user_story") or {}
    for k in ("role", "capability", "value"):
        if _trim(ou.get(k)) != _trim(nu.get(k)):
            out[f"user_story.{k}"] = {"before": ou.get(k), "after": nu.get(k)}
    for f in ("acceptance_criteria", "dependencies", "assumptions"):
        d = _list_diff(old.get(f) or [], new.get(f) or [])
        if d and not (f in ("dependencies", "assumptions") and d.get("reordered")):
            out[f] = d
    ro, rn = set(old.get("requirement_ids") or []), set(new.get("requirement_ids") or [])
    if ro != rn:
        out["requirement_ids"] = {"added": sorted(rn - ro), "removed": sorted(ro - rn)}
    mo, mn = material(old)["unresolved_decisions"], material(new)["unresolved_decisions"]
    if mo != mn:
        go, gn = {x[0] for x in mo}, {x[0] for x in mn}
        out["unresolved_decisions"] = {"added": sorted(gn - go), "removed": sorted(go - gn),
                                       "modified": sorted(g for g in go & gn if [x for x in mo if x[0] == g] != [x for x in mn if x[0] == g])}
    if material(old)["notes"] != material(new)["notes"]:
        out["notes"] = {"before": old.get("notes"), "after": new.get("notes")}
    return out


def summarize_diff(d: dict) -> str:
    parts = []
    for k, v in d.items():
        if k == "acceptance_criteria":
            parts.append(f"acceptance criteria (+{len(v['added'])}/-{len(v['removed'])}{', reordered' if v.get('reordered') else ''})")
        elif k == "requirement_ids":
            parts.append(f"linked requirements (+{', '.join(v['added']) or 'none'} / -{', '.join(v['removed']) or 'none'})")
        else:
            parts.append(k.replace("_", " "))
    return "; ".join(parts) or "formatting only"


# --------------------------------------------------------- markdown copy

_RE_TITLE = re.compile(r"^##\s+(US-\d+):\s*(.+?)\s*$", re.M)
_RE_US = re.compile(r"###\s+User Story\s*\n\s*As an?\s+(.*?),?\s*\n\s*I want\s+(.*?),?\s*\n\s*so that\s+(.*?)\.?\s*\n+\s*###\s+Acceptance Criteria",
                    re.S | re.I)
_RE_AC = re.compile(r"###\s+Acceptance Criteria\s*\n(.*?)(?:\n###\s|\Z)", re.S | re.I)


def _field(text: str, name: str) -> str | None:
    m = re.search(rf"^\*\*{re.escape(name)}:\*\*\s*(.*?)\s*$", text, re.M)
    return m.group(1) if m else None


def _split(v: str | None) -> list[str]:
    return [] if v is None or normalize_ws(v).lower() in ("none", "") else [x.strip() for x in v.split(",") if x.strip()]


def parse_md(text: str) -> dict | None:
    """The reviewable fields of a rendered story, or None when its structure is damaged (cannot be trusted)."""
    t, u, a = _RE_TITLE.search(text), _RE_US.search(text), _RE_AC.search(text)
    reqs = _field(text, "Requirement IDs")
    if not (t and u and a) or reqs is None:
        return None
    acs: list[str] = []
    for line in a.group(1).split("\n"):
        m = re.match(r"^\s*\d+[.)]\s+(.*\S)\s*$", line)
        if m:
            acs.append(m.group(1))
        elif line.strip() and acs:
            acs[-1] += " " + line.strip()
    return {"story_id": t.group(1), "title": t.group(2), "user_story": {"role": u.group(1), "capability": u.group(2), "value": u.group(3)},
            "acceptance_criteria": acs, "requirement_ids": _split(reqs), "dependencies": _split(_field(text, "Dependencies")),
            "assumptions": _split(_field(text, "Assumptions"))}


def md_diff(s: dict, text: str) -> tuple[dict, dict | None]:
    """(material differences between the Markdown copy and the JSON record, parsed fields). A damaged structure is
    reported as {"markdown_structure": ...} because nothing in it can be trusted."""
    parsed = parse_md(text)
    if parsed is None:
        return {"markdown_structure": {"before": "readable story layout", "after": "damaged or unrecognised"}}, None
    view = {"title": s.get("title"), "user_story": s.get("user_story"), "acceptance_criteria": s.get("acceptance_criteria"),
            "requirement_ids": s.get("requirement_ids")}
    d = diff(view, {k: parsed[k] for k in view})
    # dependencies / assumptions are comma-joined in the Markdown, so compare the joined text, not the split items
    for f in ("dependencies", "assumptions"):
        if _n(", ".join(s.get(f) or [])) != _n(", ".join(parsed[f])):
            d[f] = _list_diff(s.get(f) or [], parsed[f]) or {"added": [], "removed": []}
    return d, parsed


def apply_md_fields(s: dict, parsed: dict, changed: set[str]) -> dict:
    """A copy of record `s` carrying only the fields an edited Markdown copy actually changed."""
    import copy
    out = copy.deepcopy(s)
    for k in ("title", "acceptance_criteria", "requirement_ids", "dependencies", "assumptions"):
        if k in changed:
            out[k] = copy.deepcopy(parsed[k])
    if any(k.startswith("user_story.") for k in changed):
        out["user_story"] = copy.deepcopy(parsed["user_story"])
    return out
