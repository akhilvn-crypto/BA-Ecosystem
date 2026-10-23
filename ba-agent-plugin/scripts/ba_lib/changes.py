"""Change detection and impact analysis for the BRD and for approved user stories.

Lifecycle of a change record (`changes/CHG-nnn.json` + readable `.md`, indexed in `changes/change-register.json`):

  DETECTED    seen and explained (what changed, impact, required actions) - NOTHING downstream has been touched yet
  APPLIED     a named person saw the impact and asked for it to be applied: affected artifacts are now marked stale /
              under review (requirements NEEDS_REVIEW, gaps and stories flagged, baseline stale, workflow reopened)
  RECONCILED  every required action is done (items below are all DONE / DISMISSED)
  RECORDED    formatting-only change, or a change the BA Agent itself made: logged, no review needed
  SUPERSEDED  the file changed again before this one was applied; REVERTED: the file went back to the accepted content

Nothing is ever overwritten or discarded silently: the BRD is only read; an edited approved story is kept verbatim
(`snapshots/stories/`) before anything else happens; approvals are never invalidated without a recorded reason.
"""
from __future__ import annotations

import copy
import difflib
import json
import re
from pathlib import Path

from . import ingest, render, reqs as reqmod, snapshots, storytrack as tr
from .util import (BAError, atomic_write_bytes, ist_now_iso, normalize_ws, require_identity, sha256_bytes,
                   sha256_text)

REGISTER = "changes/change-register.json"
OPEN = ("DETECTED", "APPLIED")
MATERIAL, FORMATTING = "MATERIAL", "FORMATTING_ONLY"
DONE, DISMISSED, OPEN_ITEM = "DONE", "DISMISSED", "OPEN"
PENDING_BUCKETS = ("candidates", "unapproved", "deferred")
DISMISSABLE = ("new_section", "story_removal")


def _clip(s: str, n: int = 240) -> str:
    s = normalize_ws(s)
    return s if len(s) <= n else s[: n - 1] + "…"


# ====================================================================== records

def records(p, status: str | None = None) -> list[dict]:
    out = []
    for f in sorted(p.path("changes").glob("CHG-*.json")):
        out.append(p.read_json(f"changes/{f.name}"))
    return [r for r in out if not status or r["status"] == status]


def get(p, cid: str) -> dict:
    if not re.match(r"^CHG-\d{3,}$", cid or "") or not p.exists(f"changes/{cid}.json"):
        raise BAError("UNKNOWN_CHANGE", f"Change record {cid} does not exist (see `changes list`).")
    return p.read_json(f"changes/{cid}.json")


def _row(r: dict) -> dict:
    return {"change_id": r["change_id"], "type": r["type"], "status": r["status"], "classification": r["classification"],
            "origin": r["origin"], "summary": r["summary"], "detected_at_ist": r["detected_at_ist"],
            "open_items": [i["item_id"] for i in r.get("items", []) if i["state"] == OPEN_ITEM] if r["status"] == "APPLIED" else []}


def _save(p, rec: dict) -> None:
    p.write_json(f"changes/{rec['change_id']}.json", rec, "change")
    p.write_text(f"changes/{rec['change_id']}.md", render.change_md(rec), backup=False)
    rows = [_row(r) for r in records(p)]
    p.write_json(REGISTER, {"schema_version": "1.0", "updated_at": ist_now_iso(), "changes": rows})


def _event(rec: dict, event: str, note: str | None = None, **extra) -> None:
    rec["history"].append({"at_ist": ist_now_iso(), "event": event, "note": note, **extra})


def _new(p, ctype: str, classification: str, origin: str, summary: str, subject: dict, changes: list, impact: dict,
         actions: list, items: list, status: str, key: str, during: str, **extra) -> dict:
    ids = [r["change_id"] for r in records(p)]
    cid = p.next_id("CHG", ids)
    now = ist_now_iso()
    rec = {"schema_version": "1.0", "change_id": cid, "type": ctype, "status": status, "classification": classification,
           "origin": origin, "summary": summary, "detected_at_ist": now, "detected_during": during, "dedupe_key": key,
           "subject": subject, "changes": changes, "impact": impact, "required_actions": actions, "items": items,
           "applied": None, "reconciled_at_ist": None, "superseded_by": None,
           "history": [{"at_ist": now, "event": status, "note": summary}], **extra}
    _save(p, rec)
    p.audit("CHANGE_DETECTED" if status == "DETECTED" else "CHANGE_RECORDED", change_id=cid, change_type=ctype,
            classification=classification, origin=origin, status=status, summary=summary, during=during)
    return rec


def _close(p, rec: dict, status: str, note: str, superseded_by: str | None = None) -> None:
    rec["status"], rec["superseded_by"] = status, superseded_by
    _event(rec, status, note)
    _save(p, rec)
    p.audit("CHANGE_" + status, change_id=rec["change_id"], note=note, superseded_by=superseded_by)


# =========================================================== BRD change detection

def detect_source(p) -> dict:
    src = p.state["source"]
    path = Path(src["path"])
    if not path.exists():
        return {"missing": True, "path": str(path)}
    meta = ingest.extract(path)
    if meta["fingerprint"] == src["fingerprint"]:
        return {"unchanged": True, "meta": meta}
    acc = snapshots.accepted(p)
    if meta["text_fingerprint"] == src.get("text_fingerprint"):
        cls, why = FORMATTING, "file bytes changed but the extracted text is identical"
    elif acc["material_fingerprint"] and acc["material_fingerprint"] == meta["material_fingerprint"]:
        cls, why = FORMATTING, "only layout / markup differs: wording, numbers and structure are unchanged"
    else:
        cls, why = MATERIAL, None
    return {"meta": meta, "acc": acc, "classification": cls, "reason": why,
            "diff": ingest.diff_sections(acc["sections"], meta["sections"]), "dedupe_key": "source:" + meta["fingerprint"]}


def _hunks(old_body: str, new_body: str, limit: int = 6) -> list[dict]:
    def lines(b):
        return [(ingest.material_normalize(l), normalize_ws(l)) for l in b.split("\n") if ingest.material_normalize(l)]
    a, b = lines(old_body), lines(new_body)
    sm = difflib.SequenceMatcher(None, [x[0] for x in a], [x[0] for x in b], autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        out.append({"removed": [_clip(x[1]) for x in a[i1:i2][:6]], "added": [_clip(x[1]) for x in b[j1:j2][:6]]})
        if len(out) >= limit:
            break
    return out


def _source_details(det: dict) -> list[dict]:
    d, acc, meta = det["diff"], det["acc"], det["meta"]
    o = {s["key"]: s for s in acc["sections"]}
    n = {s["key"]: s for s in meta["sections"]}
    old_text, new_text = acc["text"], meta["text"]
    out = []
    for k in d["added"]:
        out.append({"change": "ADDED", "section_key": k, "section_id": n[k]["section_id"], "title": n[k]["title"],
                    "excerpt": _clip(ingest.section_text(new_text, n[k]), 400)})
    for k in d["removed"]:
        out.append({"change": "DELETED", "section_key": k, "section_id": o[k]["section_id"], "title": o[k]["title"],
                    "excerpt": _clip(ingest.section_text(old_text, o[k]), 400) if old_text else None})
    for k in d["changed"]:
        hunks = _hunks(ingest.section_text(old_text, o[k]), ingest.section_text(new_text, n[k])) if old_text else []
        out.append({"change": "MODIFIED", "section_key": k, "section_id": n[k]["section_id"], "section_id_before": o[k]["section_id"],
                    "title": n[k]["title"], "hunks": hunks,
                    "note": None if old_text else "previous BRD text was not stored for this project; only the fact of the change is known"})
    for k in d["formatting_only"]:
        out.append({"change": "FORMATTING_ONLY", "section_key": k, "section_id": n[k]["section_id"], "title": n[k]["title"]})
    return out


def _story_row(bucket: str, s: dict, rids: set[str]) -> dict:
    return {"story_id": s["story_id"], "version": s["version"], "bucket": bucket, "status": s["status"], "title": s["title"],
            "shared_requirements": sorted(set(s["requirement_ids"]) & rids), "approved": bucket == "approved"}


def _source_impact(p, det: dict) -> dict:
    d, meta = det["diff"], det["meta"]
    n = {s["key"]: s for s in meta["sections"]}
    touched = set(d["changed"]) | set(d["removed"])
    affected = []
    for r in p.requirements():
        if r["status"] not in ("ACTIVE", "NEEDS_REVIEW"):
            continue
        keys = set(r.get("source_section_keys", []))
        hit = keys & touched
        if not hit:
            continue
        body = "\n".join(ingest.section_text(meta["text"], n[k]) for k in keys if k in n)
        present = bool(r.get("source_excerpt")) and ingest.excerpt_in_text(r["source_excerpt"], body)
        kind = ("SECTION_DELETED" if keys <= set(d["removed"]) else
                "SECTION_CHANGED_QUOTED_TEXT_INTACT" if present else "QUOTED_TEXT_REMOVED_OR_REWORDED")
        affected.append({"requirement_id": r["requirement_id"], "title": r["title"], "version": r["version"], "status": r["status"],
                         "impact": kind, "changed_sections": sorted(hit), "quoted_text_still_present": present,
                         "has_confirmed_decisions": bool(r.get("related_decision_ids"))})
    rids = {a["requirement_id"] for a in affected}
    gaps = p.gaps()
    open_gaps = [{"gap_id": g["gap_id"], "title": g["title"], "priority": g["priority"], "status": g["status"]}
                 for g in gaps if g["status"] != "RESOLVED" and set(g["impacted_requirement_ids"]) & rids]
    resolved = [g for g in gaps if g["status"] == "RESOLVED" and set(g["impacted_requirement_ids"]) & rids]
    decs = p.decisions()
    reval = sorted({g["decision_id"] for g in resolved if g.get("decision_id")} |
                   {k for k, v in decs.items() if v["current_status"] == "ACTIVE" and set(v.get("related_artifact_ids", [])) & rids})
    stories = [_story_row(b, s, rids) for b, s in p.all_stories() if set(s["requirement_ids"]) & rids]
    covered = {k for r in p.requirements() if r["status"] in ("ACTIVE", "NEEDS_REVIEW") for k in r.get("source_section_keys", [])}
    m = p.baseline_manifest()
    stage_reopens = bool(rids) and p.state["stage"] in ("COMPLETED", "WAITING_FOR_STORY_REVIEW", "STORIES_READY", "REQUIREMENTS_BASELINED")
    return {"affected_requirements": affected, "open_gaps": open_gaps,
            "decisions_to_revalidate": reval,  # never invalidated automatically: reassessing the requirement decides
            "linked_stories": stories, "approved_stories_at_risk": [x["story_id"] for x in stories if x["approved"]],
            "new_sections_without_requirements": [{"section_key": k, "title": n[k]["title"], "section_id": n[k]["section_id"]} for k in d["added"]],
            "modified_sections_without_requirements": sorted(k for k in d["changed"] if k not in covered),
            "baseline": {"approved_version": p.state["baseline_version"], "will_be_marked_stale": bool(m["versions"])},
            "workflow": {"stage": p.state["stage"], "will_reopen_to": "GAP_ANALYSIS" if stage_reopens else None}}


def _source_actions(imp: dict) -> list[str]:
    a = []
    if imp["affected_requirements"]:
        a.append("Reassess each affected requirement against the new BRD text: `req update --cause source-change`, `req confirm` "
                 "(still valid as written) or `req remove` (with the authoriser's name and role).")
    if imp["new_sections_without_requirements"]:
        a.append("Add atomic requirements for the new section(s) or record why none is needed (`changes dismiss --item new_section:<key>`).")
    if imp["modified_sections_without_requirements"]:
        a.append("Check modified section(s) that no requirement references: " + ", ".join(imp["modified_sections_without_requirements"]) + ".")
    a.append("Record the reassessment with `source accept` (the BRD fingerprint changes only then).")
    if imp["decisions_to_revalidate"]:
        a.append("Re-validate confirmed decision(s) " + ", ".join(imp["decisions_to_revalidate"]) + " against the new text; they are NOT invalidated automatically.")
    if imp["open_gaps"]:
        a.append("Re-check open gap(s) " + ", ".join(g["gap_id"] for g in imp["open_gaps"]) + ", then `gaps commit --cross-pass-done`.")
    if imp["baseline"]["will_be_marked_stale"]:
        a.append("Create and confirm a new baseline (the current one becomes stale).")
    if imp["linked_stories"]:
        a.append("Review linked stor" + ("ies " if len(imp["linked_stories"]) > 1 else "y ") + ", ".join(x["story_id"] for x in imp["linked_stories"])
                 + ": `story edit` (new version, needs re-approval) or `story confirm-unaffected`. Approvals stay on record until a reviewer decides.")
    return a


def _brd_summary(det: dict, imp: dict) -> str:
    d = det["diff"]
    return (f"BRD changed: {len(d['added'])} section(s) added, {len(d['changed'])} modified, {len(d['removed'])} deleted"
            f"{', ' + str(len(d['formatting_only'])) + ' formatting-only' if d['formatting_only'] else ''}; "
            f"{len(imp['affected_requirements'])} requirement(s), {len(imp['open_gaps'])} open gap(s) and {len(imp['linked_stories'])} stor"
            f"{'y' if len(imp['linked_stories']) == 1 else 'ies'} affected")


# ============================================================ story change detection

def _md_text(p, sid: str) -> str | None:
    f = p.path(f"stories/approved/{sid}.md")
    return f.read_text(encoding="utf-8") if f.exists() else None


def _assess_story(p, sid: str, events: list[dict] | None = None) -> tuple[dict | None, dict]:
    """Compare the approved story on disk with what was approved. Returns (change item | None, context for applying it)."""
    e = tr.entry(p, sid)
    jf = f"stories/approved/{sid}.json"
    has_json = p.exists(jf)
    ctx: dict = {"entry": e}
    if has_json:
        ctx["raw"] = p.path(jf).read_bytes()
        ctx["md_text"] = _md_text(p, sid)
    if e is None:
        if not has_json:
            return None, ctx
        try:
            s = json.loads(ctx["raw"].decode("utf-8"))
        except (ValueError, OSError):
            return {"story_id": sid, "change_type": "UNREADABLE", "classification": MATERIAL, "origin": "MANUAL", "edited_via": "json",
                    "approved_version": None, "diff": {}, "dedupe_key": f"story:{sid}:UNREADABLE"}, ctx
        ev = events if events is not None else p.audit_events()
        if any(x.get("event_type") == "STORY_APPROVED" and x.get("story_id") == sid and x.get("version") == s.get("version") for x in ev):
            ctx.update(adopt=s)
            return None, ctx
        h = tr.content_hash(s)
        ctx.update(record=s)
        return {"story_id": sid, "change_type": "UNTRACKED_APPROVED", "classification": MATERIAL, "origin": "MANUAL", "edited_via": "json",
                "approved_version": None, "diff": {}, "observed": {"content_hash": h},
                "dedupe_key": f"story:{sid}:UNTRACKED:{h}"}, ctx
    ver = tr.current(e)
    snap = tr.snapshot_record(p, ver)
    ctx.update(ver=ver, snap=snap)
    if not has_json:
        if e["status"] != tr.TRACKED:
            return None, ctx
        return {"story_id": sid, "change_type": "DELETED", "classification": MATERIAL, "origin": "MANUAL", "edited_via": None,
                "approved_version": ver["version"], "diff": {}, "dedupe_key": f"story:{sid}:DELETED:v{ver['version']}"}, ctx
    raw = ctx["raw"]
    try:
        s = json.loads(raw.decode("utf-8"))
        assert isinstance(s, dict)
    except (ValueError, AssertionError):
        return {"story_id": sid, "change_type": "UNREADABLE", "classification": MATERIAL, "origin": "MANUAL", "edited_via": "json",
                "approved_version": ver["version"], "diff": {}, "dedupe_key": f"story:{sid}:UNREADABLE:{sha256_bytes(raw)}"}, ctx
    ctx["record"] = s
    obs = tr.content_hash(s)
    ctrl = tr.approval_hash(s) != ver["approval_hash"]
    mat = tr.material_hash(s) != ver["material_hash"]
    json_changed = obs != (ver.get("accepted_content_hash") or ver["content_hash"])
    md = _md_text(p, sid)
    md_hash = sha256_text(md) if md is not None else None
    md_state, md_d, parsed = "missing", {}, None
    if md is not None:
        if md == render.story_md(s):
            md_state = "clean"
        elif md_hash == e.get("accepted_md_hash"):
            md_state = "accepted"
        else:
            md_d, parsed = tr.md_diff(s, md)
            md_state = "material" if md_d else "formatting"
    ctx.update(md_text=md, parsed=parsed, md_diff=md_d)
    base = {"story_id": sid, "origin": "MANUAL", "approved_version": ver["version"], "control_fields_altered": ctrl,
            "observed": {"content_hash": obs, "material_hash": tr.material_hash(s), "markdown_hash": md_hash},
            "approved": {"material_hash": ver["material_hash"], "approved_at_ist": ver.get("approved_at_ist"),
                         "approver_name": ver.get("approver_name"), "approver_role": ver.get("approver_role")}}
    key = f"story:{sid}:{obs}:{md_hash}"
    if ctrl or mat:
        d = tr.diff(snap, s) if snap else {"approved_snapshot": {"before": "available", "after": "missing or damaged"}}
        if ctrl:
            d["approval_record"] = {"before": "as approved", "after": "readiness / status / version / approval fields altered"}
        return {**base, "change_type": "MANUAL_EDIT" if mat else "APPROVAL_ALTERED", "classification": MATERIAL, "edited_via": "json",
                "diff": d, "dedupe_key": key}, ctx
    if md_state == "material":
        return {**base, "change_type": "MANUAL_EDIT", "classification": MATERIAL, "edited_via": "markdown", "diff": md_d, "dedupe_key": key}, ctx
    if json_changed or md_state in ("formatting", "missing"):
        return {**base, "change_type": "FORMATTING_ONLY", "classification": FORMATTING,
                "edited_via": "json" if json_changed else "markdown", "diff": {}, "dedupe_key": key}, ctx
    return None, ctx


def detect_stories(p) -> tuple[list[tuple[dict, dict]], list[dict]]:
    sids = sorted({f.stem for f in p.path("stories/approved").glob("US-*.json")} | set(tr.known_ids(p)))
    events = p.audit_events()
    items, adopt = [], []
    for sid in sids:
        item, ctx = _assess_story(p, sid, events)
        if ctx.get("adopt"):
            adopt.append(ctx["adopt"])
        if item:
            items.append((item, ctx))
    return items, adopt


def _story_impact(p, sid: str, old: dict | None, new: dict | None, diff: dict) -> dict:
    from . import stories as storymod
    live = {r["requirement_id"]: r for r in p.requirements()}
    before = list((old or {}).get("requirement_ids") or [])
    after = list((new or {}).get("requirement_ids") or [])
    rids = set(before) | set(after)
    ac = diff.get("acceptance_criteria") or {"added": [], "removed": []}
    gaps = []
    undeclared = []
    if new and after and all(r in live for r in after):
        ctx = storymod.open_gap_context(p, after)
        gaps = [{"gap_id": g["gap_id"], "title": g["title"], "priority": g["priority"], "status": g["status"]} for g in ctx]
        declared = {u.get("gap_id") for u in new.get("unresolved_decisions") or []}
        undeclared = sorted(g["gap_id"] for g in ctx if g["gap_id"] not in declared) if "unresolved_decisions" in new else []
    drift = sorted(r for r in after if r in live and new.get("requirement_versions", {}).get(r) not in (None, live[r]["version"]))
    others = [_story_row(b, s, rids) for b, s in p.all_stories() if s["story_id"] != sid and set(s["requirement_ids"]) & rids]
    ver = (old or {}).get("approval") or {}
    return {"story_id": sid, "fields_changed": sorted(diff), "summary_of_fields": tr.summarize_diff(diff),
            "requirements": {"before": before, "after": after, "added": (diff.get("requirement_ids") or {}).get("added", []),
                             "removed": (diff.get("requirement_ids") or {}).get("removed", []),
                             "unknown_to_project": sorted(r for r in after if r not in live),
                             "statements": {r: live[r]["statement"] for r in sorted(rids) if r in live}},
            "acceptance_criteria": {"added": ac.get("added", []), "removed": ac.get("removed", []), "reordered": bool(ac.get("reordered"))},
            "open_gaps_on_requirements": gaps, "open_gaps_not_declared_in_story": undeclared,
            "linked_requirements_changed_since_story_written": drift, "stories_sharing_requirements": others,
            "approval_record": {"version": (old or {}).get("version"), "approved_at_ist": ver.get("at_ist"),
                                "approver_name": ver.get("approver_name"), "approver_role": ver.get("approver_role")}}


def _story_actions(item: dict, imp: dict, nv: int | None = None) -> list[str]:
    t = item["change_type"]
    sid = item["story_id"]
    if t == "DELETED":
        return [f"Decide: restore {sid} (`story restore {sid}`) or confirm its removal (`changes dismiss --item story_removal:{sid}`).",
                "Requirements it covered will show as having no approved story until you decide."]
    if t == "FORMATTING_ONLY":
        return []
    a = [f"Review the edit to {sid}. After you apply this change it becomes v{nv or '?'} (CANDIDATE) and the approved version stays "
         f"on record untouched; approve v{nv or '?'} (`story approve`), edit it further, or discard it (`story revert-edit {sid}`)."]
    if imp["requirements"]["removed"]:
        a.append("Requirement(s) " + ", ".join(imp["requirements"]["removed"]) + " are no longer linked: confirm they are covered by another story.")
    if imp["requirements"]["added"]:
        a.append("Newly linked requirement(s) " + ", ".join(imp["requirements"]["added"]) + ": confirm the acceptance criteria cover them.")
    if imp["open_gaps_not_declared_in_story"]:
        a.append("Declare open gap(s) " + ", ".join(imp["open_gaps_not_declared_in_story"]) + " in unresolved_decisions (`story gaps`).")
    if imp["stories_sharing_requirements"]:
        a.append("Check overlap with " + ", ".join(x["story_id"] for x in imp["stories_sharing_requirements"]) + ".")
    return a


# ============================================================== detection facade

def detect(p) -> dict:
    """Read-only: what differs from the accepted BRD and from what reviewers approved. Writes nothing."""
    items, adopt = detect_stories(p)
    return {"source": detect_source(p), "stories": items, "adopt": adopt}


def _open_by_key(p, key: str) -> dict | None:
    return next((r for r in records(p) if r["dedupe_key"] == key and r["status"] in OPEN), None)


def _brd_parts(p, det: dict) -> dict:
    imp = _source_impact(p, det)
    return {"changes": _source_details(det), "impact": imp, "actions": _source_actions(imp), "summary": _brd_summary(det, imp)}


def _story_parts(p, item: dict, ctx: dict) -> dict:
    t = item["change_type"]
    sid = item["story_id"]
    snap = ctx.get("snap")
    new = ctx.get("record")
    if t == "MANUAL_EDIT" and item["edited_via"] == "markdown" and ctx.get("parsed"):
        new = tr.apply_md_fields(new, ctx["parsed"], set(item["diff"]))
    imp = _story_impact(p, sid, snap, new if t not in ("DELETED", "UNREADABLE") else None, item["diff"])
    nv = _next_version(p, sid) if t in ("MANUAL_EDIT", "APPROVAL_ALTERED", "UNTRACKED_APPROVED", "UNREADABLE") and item["diff"] else None
    verb = {"MANUAL_EDIT": f"edited by hand ({item['edited_via']})", "APPROVAL_ALTERED": "approval / readiness fields altered by hand",
            "DELETED": "deleted from stories/approved/", "UNREADABLE": "unreadable", "UNTRACKED_APPROVED": "present in approved/ with no approval on record",
            "FORMATTING_ONLY": "reformatted only"}[t]
    av = item.get("approved_version")
    summary = f"Approved story {sid}" + (f" v{av}" if av else "") + f" {verb}" + (f": {imp['summary_of_fields']}" if item["diff"] else "")
    return {"changes": [{"change": t, "story_id": sid, "edited_via": item["edited_via"], "fields": item["diff"]}],
            "impact": imp, "actions": _story_actions(item, imp, nv), "summary": summary}


def _next_version(p, sid: str) -> int:
    v = [0]
    e = tr.entry(p, sid)
    if e:
        v += [x["version"] for x in e["versions"]]
    v += [s["version"] for s in p.archived_stories() if s["story_id"] == sid]
    for b in ("candidates", "unapproved", "deferred", "approved"):
        if p.exists(f"stories/{b}/{sid}.json"):
            try:
                v.append(p.read_json(f"stories/{b}/{sid}.json")["version"])
            except (ValueError, KeyError, OSError):
                pass
    return max(v) + 1


# ====================================================================== check

def check(p, record: bool = True, during: str = "manual check") -> dict:
    """Detect BRD and approved-story changes, explain their impact and (with record=True) log them. Never changes a
    downstream artifact except to accept a formatting-only difference (a recorded, review-free bookkeeping update)."""
    det = detect(p)
    out = {"checked_at_ist": ist_now_iso(), "during": during, "recorded": record, "warnings": [], "source": "unchanged",
           "changes": [], "formatting_only_accepted": []}
    cur_src_key, cur_story_keys, current_story_ids = None, set(), set()

    s = det["source"]
    if s.get("missing"):
        out["source"] = "missing"
        out["warnings"].append(f"The BRD file is missing: {s['path']}. Restore it; existing artifacts remain valid.")
    elif s.get("unchanged"):
        if record:
            snapshots.backfill_if_unchanged(p, s["meta"])
    else:
        out["source"] = "formatting_only" if s["classification"] == FORMATTING else "changed"
        if s["classification"] == FORMATTING:
            if record:
                out["formatting_only_accepted"].append(_row(accept_source_formatting(p, s, during)))
        else:
            cur_src_key = s["dedupe_key"]
            parts = _brd_parts(p, s)
            rec = _open_by_key(p, cur_src_key)
            if record and rec is None:
                rec = _new(p, "SOURCE_CHANGE", MATERIAL, "SOURCE_FILE", parts["summary"],
                           {"previous_fingerprint": p.state["source"]["fingerprint"], "new_fingerprint": s["meta"]["fingerprint"],
                            "previous_snapshot_version": (snapshots.latest(p) or {}).get("version"),
                            "snapshot_available": s["acc"]["has_snapshot"]},
                           parts["changes"], parts["impact"], parts["actions"], [], "DETECTED", cur_src_key, during)
            out["changes"].append(rec if rec else {"change_id": None, "status": "PREVIEW", "type": "SOURCE_CHANGE", **{
                k: parts[k] for k in ("summary", "changes", "impact")}, "required_actions": parts["actions"]})
            if not s["acc"]["has_snapshot"]:
                out["warnings"].append("The previous BRD text was not stored (project created before change detection): "
                                       "sections are compared by hash, so line-level differences are unavailable.")

    for item, ctx in det["stories"]:
        sid = item["story_id"]
        if item["classification"] == FORMATTING:
            if record:
                out["formatting_only_accepted"].append(_row(accept_story_formatting(p, item, ctx, during)))
            continue
        cur_story_keys.add(item["dedupe_key"])
        current_story_ids.add(sid)
        rec = _open_by_key(p, item["dedupe_key"])
        parts = _story_parts(p, item, ctx)
        if record and rec is None:
            rec = _new(p, "STORY_CHANGE", MATERIAL, "MANUAL", parts["summary"],
                       {"story_id": sid, "change_type": item["change_type"], "edited_via": item["edited_via"],
                        "approved_version": item.get("approved_version"), "observed": item.get("observed"), "approved": item.get("approved"),
                        "control_fields_altered": item.get("control_fields_altered", False)},
                       parts["changes"], parts["impact"], parts["actions"], [], "DETECTED", item["dedupe_key"], during)
        out["changes"].append(rec if rec else {"change_id": None, "status": "PREVIEW", "type": "STORY_CHANGE", "summary": parts["summary"],
                                                  "changes": parts["changes"], "impact": parts["impact"], "required_actions": parts["actions"]})
    if record:
        for s_rec in det["adopt"]:
            tr.record_approved(p, s_rec, event="ADOPTED")
            p.audit("STORY_TRACKING_ADOPTED", story_id=s_rec["story_id"], version=s_rec["version"],
                    note="approved before change tracking existed; current content taken as the approved content")
        _heal_reappeared(p, det)
        _retire_stale(p, cur_src_key, cur_story_keys, current_story_ids)
    out.update(_state_summary(p))
    out["requires_review"] = bool(out["unreviewed_ids"]) or (not record and any(c.get("status") == "PREVIEW" for c in out["changes"]))
    out["next"] = _next_text(out)
    return out


def _heal_reappeared(p, det: dict) -> None:
    flagged = {i["story_id"] for i, _ in det["stories"]}
    for sid, e in tr.index(p)["stories"].items():
        if e["status"] != tr.TRACKED and p.exists(f"stories/approved/{sid}.json") and sid not in flagged:
            tr.update_entry(p, sid, status=tr.TRACKED)
            p.audit("STORY_REAPPEARED", story_id=sid, note="the approved story is back and matches what was approved")


def _retire_stale(p, src_key: str | None, story_keys: set[str], story_ids: set[str]) -> None:
    for r in records(p):
        if r["status"] not in OPEN or r["origin"] == "BA_AGENT" or r["dedupe_key"] in ({src_key} | story_keys):
            continue
        if r["type"] == "SOURCE_CHANGE":
            if src_key:
                _close(p, r, "SUPERSEDED", "the BRD changed again before this change was resolved", _open_by_key(p, src_key)["change_id"])
            elif r["status"] == "DETECTED":
                _close(p, r, "REVERTED", "the BRD matches the accepted version again; nothing was applied")
        elif r["type"] == "STORY_CHANGE" and r["status"] == "DETECTED":
            sid = r["subject"]["story_id"]
            newer = next((x for x in records(p) if x["status"] in OPEN and x["type"] == "STORY_CHANGE" and x["subject"]["story_id"] == sid
                          and x["dedupe_key"] in story_keys and x["change_id"] != r["change_id"]), None)
            if newer:
                _close(p, r, "SUPERSEDED", "the story was edited again before this change was resolved", newer["change_id"])
            elif sid not in story_ids:
                _close(p, r, "REVERTED", "the approved story matches what was approved again; nothing was applied")


def _state_summary(p) -> dict:
    recs = records(p)
    unreviewed = [r["change_id"] for r in recs if r["status"] == "DETECTED"]
    waiting = [_row(r) for r in recs if r["status"] == "APPLIED"]
    return {"unreviewed_ids": unreviewed, "awaiting_reconciliation": waiting}


def _next_text(out: dict) -> str:
    if out["unreviewed_ids"] or any(c.get("status") == "PREVIEW" for c in out["changes"]):
        return ("Show the user each change below - what changed, the impact and the required actions - then ask how to proceed. "
                "NOTHING downstream has been changed yet. When they agree: `changes apply --id CHG-nnn --by-name … --by-role … --statement …`.")
    if out["awaiting_reconciliation"]:
        return "Applied change(s) still have open actions: " + ", ".join(r["change_id"] for r in out["awaiting_reconciliation"]) + " (`changes status`)."
    return "No unreviewed changes."


# ------------------------------------------------- formatting-only (auto, recorded)

def accept_source_formatting(p, det: dict, during: str = "check") -> dict:
    meta, old = det["meta"], p.state["source"]["fingerprint"]
    reason = det.get("reason") or "formatting only"
    p.state["source"].update(fingerprint=meta["fingerprint"], text_fingerprint=meta["text_fingerprint"],
                             material_fingerprint=meta["material_fingerprint"])
    p.write_source_metadata(meta)
    secs = {s["section_id"]: s for s in meta["sections"]}
    for r in p.requirements():
        if r["status"] not in ("ACTIVE", "NEEDS_REVIEW"):
            continue
        r["source_fingerprint"] = meta["fingerprint"]
        if all(i in secs for i in r["source_section_ids"]):
            r["source_reference"] = reqmod._reference(p, [secs[i] for i in r["source_section_ids"]])
        p.save_requirement(r)
    entry = snapshots.record(p, meta, "formatting-only change accepted")
    rec = _new(p, "SOURCE_CHANGE", FORMATTING, "SOURCE_FILE", f"BRD reformatted only ({reason}); no review needed",
               {"previous_fingerprint": old, "new_fingerprint": meta["fingerprint"], "snapshot_version": entry["version"]},
               [{"change": "FORMATTING_ONLY", "section_key": k} for k in det["diff"]["formatting_only"]],
               {"affected_requirements": [], "linked_stories": [], "open_gaps": []}, [], [], "RECORDED", det["dedupe_key"], during)
    p.audit("SOURCE_FORMATTING_CHANGE_RECORDED", change_id=rec["change_id"], previous=old, new=meta["fingerprint"], reason=reason)
    p.save_state("source-formatting-change-recorded")
    return rec


def accept_story_formatting(p, item: dict, ctx: dict, during: str = "check") -> dict:
    sid, ver = item["story_id"], ctx["ver"]
    s = ctx["record"]
    fields: dict = {"accepted_content_hash": item["observed"]["content_hash"]}
    tr.update_version(p, sid, ver["version"], **fields)
    note = "reformatted only; approval unchanged"
    if ctx.get("md_text") is None:
        p.write_text(f"stories/approved/{sid}.md", render.story_md(s), backup=False)
        note = "readable .md copy was missing and has been regenerated; approval unchanged"
    elif item["observed"]["markdown_hash"] and ctx["md_text"] != render.story_md(s):
        tr.update_entry(p, sid, accepted_md_hash=item["observed"]["markdown_hash"])
    rec = _new(p, "STORY_CHANGE", FORMATTING, "MANUAL", f"Approved story {sid} v{ver['version']} {note}",
               {"story_id": sid, "change_type": "FORMATTING_ONLY", "approved_version": ver["version"], "observed": item["observed"]},
               [{"change": "FORMATTING_ONLY", "story_id": sid}], {"story_id": sid}, [], [], "RECORDED", item["dedupe_key"], during)
    p.audit("STORY_FORMATTING_CHANGE_RECORDED", story_id=sid, version=ver["version"], change_id=rec["change_id"], note=note)
    return rec


# ======================================================================= apply

def _ack(by_name, by_role, statement, legacy: bool) -> dict | None:
    if legacy:
        return None
    by_name, by_role = require_identity(by_name, by_role, "Person applying the change")
    if not normalize_ws(statement or ""):
        raise BAError("BAD_INPUT", "State, in the person's own words, that they have seen the impact and want it applied (--statement).")
    return {"by_name": by_name, "by_role": by_role, "statement": normalize_ws(statement)}


def apply(p, cid: str | None, by_name=None, by_role=None, statement=None, _legacy: bool = False) -> dict:
    """Apply a DETECTED change after its impact was shown: mark the affected artifacts stale / under review."""
    if cid is None:
        detected = records(p, "DETECTED")
        if len(detected) != 1:
            raise BAError("CHANGE_ID_REQUIRED", "Name the change with --id. Unreviewed: " + (", ".join(r["change_id"] for r in detected) or "none"))
        cid = detected[0]["change_id"]
    rec = get(p, cid)
    if rec["status"] != "DETECTED":
        raise BAError("NOT_APPLICABLE", f"{cid} is {rec['status']}; only a DETECTED change can be applied.")
    ack = _ack(by_name, by_role, statement, _legacy)
    if rec["type"] == "SOURCE_CHANGE":
        effects, items = _apply_source(p, rec)
    else:
        effects, items = _apply_story(p, rec)
    rec["status"], rec["items"] = "APPLIED", items
    rec["applied"] = {"at_ist": ist_now_iso(), **(ack or {"by_name": None, "by_role": None, "statement": None}), "effects": effects}
    _event(rec, "APPLIED", (ack or {}).get("statement"), by_name=(ack or {}).get("by_name"))
    _save(p, rec)
    p.audit("CHANGE_APPLIED", change_id=cid, change_type=rec["type"], by_name=(ack or {}).get("by_name"), by_role=(ack or {}).get("by_role"),
            statement=(ack or {}).get("statement"), effects={k: v for k, v in effects.items() if isinstance(v, (list, str, bool, int, type(None)))})
    reconcile(p)
    return {"change_id": cid, "status": get(p, cid)["status"], "effects": effects, "open_items": [i for i in get(p, cid)["items"] if i["state"] == OPEN_ITEM],
            "next": "Work through the open items (see `changes show " + cid + "`); each is closed by the normal command (req update/confirm, "
                    "source accept, story edit/approve, baseline create/confirm). Progress: `changes status`."}


def _apply_source(p, rec: dict) -> tuple[dict, list]:
    s = detect_source(p)
    if s.get("missing") or s.get("unchanged") or s["dedupe_key"] != rec["dedupe_key"]:
        raise BAError("CHANGE_RECORD_STALE", f"The BRD is no longer in the state {rec['change_id']} described. Run `changes check` again "
                      "and show the user the new impact before applying anything.")
    res = reqmod._apply_source_change(p, rec["change_id"])
    items = []
    for rid in res.get("affected_requirement_ids", []):
        items.append(_item("requirement", rid, f"Reassess {rid} (req update --cause source-change | req confirm | req remove)"))
    for k in s["diff"]["added"]:
        items.append(_item("new_section", k, f"Add requirement(s) for new section '{k}' or dismiss with a reason"))
    items.append(_item("source_acceptance", "source", "Record the reassessment with `source accept`"))
    for g in res.get("flagged_gaps", []):
        items.append(_item("gap", g, f"Re-check {g}, then `gaps commit --cross-pass-done`"))
    if p.baseline_manifest()["versions"]:
        items.append(_item("baseline", "baseline", "Create and confirm a new baseline"))
    for sid in res.get("flagged_stories", []):
        items.append(_item("story", sid, f"Review {sid}: `story edit` (new version) or `story confirm-unaffected`"))
    return {"requirements_marked_for_review": res.get("affected_requirement_ids", []), "gaps_flagged": res.get("flagged_gaps", []),
            "stories_flagged": res.get("flagged_stories", []), "baseline_marked_stale": bool(p.baseline_manifest()["versions"]),
            "stage": p.state["stage"], "result": res}, items


def _item(kind: str, ref: str, text: str) -> dict:
    return {"item_id": f"{kind}:{ref}", "kind": kind, "ref": ref, "text": text, "state": OPEN_ITEM, "closed_at_ist": None, "closed_by": None, "reason": None}


def _apply_story(p, rec: dict) -> tuple[dict, list]:
    sid = rec["subject"]["story_id"]
    item, ctx = _assess_story(p, sid)
    if not item or item["dedupe_key"] != rec["dedupe_key"]:
        raise BAError("CHANGE_RECORD_STALE", f"{sid} is no longer in the state {rec['change_id']} described. Run `changes check` again.")
    cid, t, now = rec["change_id"], item["change_type"], ist_now_iso()
    eff: dict = {"story_id": sid, "change_type": t}
    if t == "DELETED":
        tr.update_entry(p, sid, status=tr.DELETED, deleted_detected_at_ist=now, deleted_change_id=cid)
        p.audit("STORY_DELETION_DETECTED", story_id=sid, approved_version=item["approved_version"], change_id=cid,
                note="approved story removed from stories/approved/; its approved record is kept in snapshots/stories/")
        _reopen_if_completed(p, "approved-story-deleted")
        p.save_state(f"story-{sid}-deletion-recorded")
        eff.update(snapshot_kept=True, restore=f"story restore {sid}")
        return eff, [_item("story_removal", sid, f"Restore {sid} (`story restore`) or confirm its removal (`changes dismiss`)")]
    # --- everything else: keep the observed files verbatim before any file is touched
    kept = []
    if ctx.get("raw") is not None:
        dst = f"snapshots/stories/{sid}.observed-{cid}.json"
        atomic_write_bytes(p.path(dst), ctx["raw"])
        kept.append(dst)
    if ctx.get("md_text") is not None:
        dst = f"snapshots/stories/{sid}.observed-{cid}.md"
        atomic_write_bytes(p.path(dst), ctx["md_text"].encode("utf-8"))
        kept.append(dst)
    eff["edit_preserved_as"] = kept
    snap, ver, observed = ctx.get("snap"), ctx.get("ver"), ctx.get("record")
    cand_id, cand_err = None, None
    material_diff = {k: v for k, v in item["diff"].items() if k not in ("approval_record", "approved_snapshot")}
    want_cand = t == "UNTRACKED_APPROVED" or bool(material_diff) or (t == "MANUAL_EDIT" and "approved_snapshot" in item["diff"])
    if want_cand:
        existing = next((x for _, x in _all_versions(p, sid) if any(h.get("change_id") == cid for h in x["review_history"])), None)
        if existing:
            cand_id = existing["version"]
        else:
            if t == "MANUAL_EDIT" and item["edited_via"] == "markdown":
                src = tr.apply_md_fields(observed or snap, ctx["parsed"], set(material_diff)) if ctx.get("parsed") and (observed or snap) else None
                cand_err = None if src else "the readable .md copy no longer has the story layout, so its fields cannot be read"
            else:
                src = observed
                cand_err = None if src else "the edited file cannot be read"
            if src:
                cand = _build_candidate(p, src, ver["version"] if ver else None, cid, rec["summary"])
                try:
                    p.save_story("candidates", cand)
                    cand_id = cand["version"]
                except BAError as e:
                    cand_err = e.details.get("errors") or e.message
        if t == "UNTRACKED_APPROVED" and cand_id is None:
            raise BAError("STORY_NOT_CAPTURABLE", f"{sid} sits in stories/approved/ without an approval on record and could not be moved to "
                          f"review: {cand_err}. Nothing was changed; fix the file or remove it by hand, then run `changes check`.")
    if snap:  # put back exactly what was approved (workflow bookkeeping such as review history is carried over)
        restored = copy.deepcopy(snap)
        if observed and observed.get("review_history", [])[:len(snap["review_history"])] == snap["review_history"]:
            restored["review_history"], restored["impact_review"] = observed["review_history"], observed.get("impact_review")
        restored["updated_at"] = now
        p.save_story("approved", restored)
        eff["approved_version_restored"] = restored["version"]
    elif ver is None:  # never had an approval on record: it moves to review instead of staying in approved/
        p.remove_story_files("approved", sid)
    else:
        eff["warning"] = "the approved snapshot is missing or damaged; the edited file was left in place and flagged"
    if ver is not None and (cand_id is None or not snap):  # the approved record must not stay unflagged
        why = ("the approved snapshot is unavailable, so the story cannot be proved to be what was approved"
               + (f"; the edit is also saved as v{cand_id} for review" if cand_id else "") if not snap else
               f"the edit could not be captured as a new version ({cand_err}); it is preserved in {', '.join(kept)}" if want_cand else
               "its approval / readiness fields had been altered by hand and were restored from the approved snapshot")
        _flag_approved(p, sid, cid, why)
    eff.update(new_candidate_version=cand_id, candidate_error=cand_err, approved_version=item.get("approved_version"))
    p.audit("STORY_MANUAL_EDIT_DETECTED", story_id=sid, change_id=cid, change_type=t, edited_via=item["edited_via"],
            approved_version=item.get("approved_version"), new_version=cand_id, fields=sorted(item["diff"]),
            preserved_as=kept, approval_still_on_record=bool(snap))
    if cand_id:
        p.audit("STORY_REAPPROVAL_REQUIRED", story_id=sid, version=cand_id, change_id=cid, reason="material manual edit to an approved story")
        _reopen_if_completed(p, "story-manual-edit")
    p.save_state(f"story-{sid}-manual-edit-recorded")
    return eff, [_item("reapproval", f"{sid}:v{cand_id}", f"Approve, edit or revert {sid} v{cand_id}")] if cand_id else \
        [_item("story", sid, f"Review {sid}: confirm it is unaffected or edit it (`story confirm-unaffected` / `story edit`)")]


def _flag_approved(p, sid: str, cid: str, why: str) -> None:
    s = p.read_json(f"stories/approved/{sid}.json")
    now = ist_now_iso()
    s["impact_review"] = {"pending": True, "reason": why, "cause": "manual-edit", "change_id": cid, "at_ist": now}
    s["review_history"].append({"action": "IMPACT_FLAGGED", "version": s["version"], "at_ist": now, "note": why, "change_id": cid})
    s["updated_at"] = now
    p.save_story("approved", s)


def _all_versions(p, sid: str):
    for b in ("candidates", "unapproved", "deferred", "approved"):
        if p.exists(f"stories/{b}/{sid}.json"):
            yield b, p.read_json(f"stories/{b}/{sid}.json")


def _build_candidate(p, src: dict, approved_version: int | None, cid: str, note: str) -> dict:
    from . import stories as storymod
    c = copy.deepcopy(src)
    sid = c["story_id"]
    nv, now = _next_version(p, sid), ist_now_iso()
    live = {r["requirement_id"]: r for r in p.requirements()}
    if c.get("requirement_ids") and all(r in live for r in c["requirement_ids"]):
        d = storymod._derived(p, c["requirement_ids"])
        c.update(decision_ids=d["decision_ids"], open_gap_ids=d["open_gap_ids"], source_references=d["source_references"],
                 requirement_versions=d["requirement_versions"])
    c.update(version=nv, status="CANDIDATE", approval=None, deferral=None, impact_review=None, supersedes_version=approved_version,
             baseline_version=p.state["baseline_version"], updated_at=now)
    if "unresolved_decisions" in c:
        c["readiness"] = storymod.readiness_of(c["unresolved_decisions"])
    c.setdefault("review_history", []).append({"action": "MANUAL_EDIT_DETECTED", "version": nv, "at_ist": now, "note": _clip(note, 300),
                                               "change_id": cid, "from_version": approved_version})
    return c


def _reopen_if_completed(p, why: str) -> None:
    if p.state["stage"] == "COMPLETED":
        p.reopen_to("WAITING_FOR_STORY_REVIEW", why)


# ============================================================ BA Agent's own story edits

def record_agent_story_edit(p, base: dict, new: dict, reason: str, formatting_only: bool = False) -> dict:
    """Log an edit the BA Agent made to an APPROVED story (via `story edit`). Material edits become a new version that
    needs re-approval; the approved version stays in force. Formatting-only edits keep the approval."""
    sid, d = base["story_id"], tr.diff(base, new)
    imp = _story_impact(p, sid, base, new, d)
    if formatting_only:
        return _new(p, "STORY_CHANGE", FORMATTING, "BA_AGENT", f"BA Agent reformatted approved story {sid} v{base['version']} ({_clip(reason, 120)}); approval unchanged",
                    {"story_id": sid, "change_type": "FORMATTING_ONLY", "approved_version": base["version"]},
                    [{"change": "FORMATTING_ONLY", "story_id": sid}], {"story_id": sid}, [], [], "RECORDED", f"agent:{sid}:{new['updated_at']}", "story edit")
    items = [_item("reapproval", f"{sid}:v{new['version']}", f"Approve, edit or revert {sid} v{new['version']}")]
    return _new(p, "STORY_CHANGE", MATERIAL, "BA_AGENT",
                f"BA Agent edited approved story {sid} v{base['version']} → v{new['version']}: {imp['summary_of_fields']} ({_clip(reason, 120)})",
                {"story_id": sid, "change_type": "AGENT_EDIT", "approved_version": base["version"], "new_version": new["version"], "reason": reason},
                [{"change": "AGENT_EDIT", "story_id": sid, "fields": d}], imp, _story_actions({"change_type": "AGENT_EDIT", "story_id": sid}, imp, new["version"]),
                items, "APPLIED", f"agent:{sid}:v{new['version']}", "story edit")


# ================================================================== reconcile

def _item_states(p, rec: dict) -> dict[str, str]:
    """Pure: the current state of every OPEN item of an APPLIED record."""
    rmap = {r["requirement_id"]: r for r in p.requirements()}
    gaps = {g["gap_id"]: g for g in p.gaps()}
    stories = p.all_stories()
    manifest = p.baseline_manifest()
    covered = {k for r in rmap.values() if r["status"] in ("ACTIVE", "NEEDS_REVIEW") for k in r.get("source_section_keys", [])}
    out = {}
    for it in rec["items"]:
        if it["state"] != OPEN_ITEM:
            continue
        k, ref = it["kind"], it["ref"]
        if k == "requirement":
            done = ref not in rmap or rmap[ref]["status"] != "NEEDS_REVIEW"
        elif k == "source_acceptance":
            done = not p.state["impact_review"].get("pending")
        elif k == "gap":
            done = ref not in gaps or not gaps[ref].get("needs_impact_review") or gaps[ref]["status"] == "RESOLVED"
        elif k == "story":
            done = not any(s["story_id"] == ref and (s.get("impact_review") or {}).get("pending") for _, s in stories)
        elif k == "baseline":
            last = manifest["versions"][-1] if manifest["versions"] else None
            done = bool(last) and not manifest["stale"] and last["status"] == "APPROVED" and last["created_at"] >= rec["applied"]["at_ist"]
        elif k == "new_section":
            done = ref in covered
        elif k == "reapproval":
            sid, ver = ref.split(":v")
            ver = int(ver)
            pending = any(s["story_id"] == sid and s["version"] == ver and b in PENDING_BUCKETS for b, s in stories)
            done = not pending
        elif k == "story_removal":
            e = tr.entry(p, ref)
            done = bool(e) and (e["status"] == tr.REMOVAL_CONFIRMED or (e["status"] == tr.TRACKED and p.exists(f"stories/approved/{ref}.json")))
        else:
            done = False
        out[it["item_id"]] = DONE if done else OPEN_ITEM
    return out


def reconcile(p) -> list[str]:
    """Close items whose underlying work is done; RECONCILE a record when none are left. Returns reconciled ids."""
    changed = []
    for rec in records(p, "APPLIED"):
        states = _item_states(p, rec)
        dirty = False
        for it in rec["items"]:
            if states.get(it["item_id"]) == DONE and it["state"] == OPEN_ITEM:
                it["state"], it["closed_at_ist"], it["closed_by"] = DONE, ist_now_iso(), "workflow"
                dirty = True
        if all(i["state"] in (DONE, DISMISSED) for i in rec["items"]):
            rec["status"], rec["reconciled_at_ist"] = "RECONCILED", ist_now_iso()
            _event(rec, "RECONCILED", "all required actions are complete")
            p.audit("CHANGE_RECONCILED", change_id=rec["change_id"], change_type=rec["type"])
            changed.append(rec["change_id"])
            dirty = True
        if dirty:
            _save(p, rec)
    return changed


def dismiss(p, cid: str, item_id: str, reason: str, by_name: str, by_role: str) -> dict:
    by_name, by_role = require_identity(by_name, by_role, "Person dismissing the item")
    if not normalize_ws(reason or ""):
        raise BAError("REASON_REQUIRED", "A reason is required to dismiss an item.")
    rec = get(p, cid)
    it = next((i for i in rec["items"] if i["item_id"] == item_id), None)
    if not it:
        raise BAError("UNKNOWN_ITEM", f"{cid} has no item {item_id}. Items: {[i['item_id'] for i in rec['items']]}")
    if it["kind"] not in DISMISSABLE:
        raise BAError("NOT_DISMISSABLE", f"{it['kind']} items are closed by doing the work, not by dismissing them. "
                      f"Dismissable: {list(DISMISSABLE)}.")
    if it["state"] != OPEN_ITEM:
        raise BAError("NOT_APPLICABLE", f"{item_id} is already {it['state']}.")
    it.update(state=DISMISSED, closed_at_ist=ist_now_iso(), closed_by=f"{by_name} — {by_role}", reason=normalize_ws(reason))
    if it["kind"] == "story_removal":
        tr.update_entry(p, it["ref"], status=tr.REMOVAL_CONFIRMED)
    _event(rec, "ITEM_DISMISSED", f"{item_id}: {reason}", by_name=by_name)
    _save(p, rec)
    p.audit("CHANGE_ITEM_DISMISSED", change_id=cid, item_id=item_id, reason=reason, by_name=by_name, by_role=by_role)
    reconcile(p)
    return {"change_id": cid, "item": item_id, "state": DISMISSED, "change_status": get(p, cid)["status"]}


def check_and_apply_source(p) -> dict:
    """Detect + record + apply a BRD change in one call (library use; the CLI previews first and applies on request)."""
    s = detect_source(p)
    if s.get("missing"):
        raise BAError("SOURCE_NOT_FOUND", f"The BRD file is missing: {s['path']}. Restore it or point the project at the new location.")
    if s.get("unchanged"):
        return {"changed": False, "fingerprint": p.state["source"]["fingerprint"]}
    if s["classification"] == FORMATTING:
        accept_source_formatting(p, s, "direct call")
        return {"changed": False, "cosmetic_change": True, "formatting_only": True,
                "note": "Only layout / markup changed (wording, numbers and structure are identical); fingerprint refreshed, no impact."}
    check(p, True, "direct call")
    rec = _open_by_key(p, s["dedupe_key"])
    if rec["status"] == "DETECTED":
        apply(p, rec["change_id"], _legacy=True)
        rec = get(p, rec["change_id"])
    return rec["applied"]["effects"]["result"]


# ============================================================ story restore / revert

def restore_story(p, sid: str, reason: str, by_name: str, by_role: str) -> dict:
    by_name, by_role = require_identity(by_name, by_role, "Person restoring the story")
    if not normalize_ws(reason or ""):
        raise BAError("REASON_REQUIRED", "A reason is required to restore an approved story.")
    e = tr.entry(p, sid)
    if not e or e["status"] not in (tr.DELETED, tr.REMOVAL_CONFIRMED) or p.exists(f"stories/approved/{sid}.json"):
        raise BAError("NOTHING_TO_RESTORE", f"{sid} has no recorded deletion to undo (run `changes check` and `changes apply` first).")
    ver = tr.current(e)
    snap = tr.snapshot_record(p, ver)
    if not snap:
        raise BAError("SNAPSHOT_UNAVAILABLE", f"The approved snapshot of {sid} v{ver['version']} is missing or damaged ({ver['snapshot']}).")
    snap["review_history"].append({"action": "RESTORED_AFTER_DELETION", "version": snap["version"], "at_ist": ist_now_iso(), "note": normalize_ws(reason),
                                   "by_name": by_name})
    snap["updated_at"] = ist_now_iso()
    p.save_story("approved", snap)
    tr.update_entry(p, sid, status=tr.TRACKED)
    p.audit("STORY_RESTORED", story_id=sid, version=snap["version"], reason=reason, by_name=by_name, by_role=by_role)
    p.save_state(f"story-{sid}-restored")
    reconcile(p)
    return {"story_id": sid, "version": snap["version"], "status": "APPROVED", "approval_restored_from": ver["snapshot"]}


def revert_edit(p, sid: str, reason: str, by_name: str, by_role: str) -> dict:
    """Discard an edited version (manual or agent) that has not been approved; the approved version stays in force."""
    by_name, by_role = require_identity(by_name, by_role, "Person reverting the edit")
    if not normalize_ws(reason or ""):
        raise BAError("REASON_REQUIRED", "A reason is required to discard an edited story version.")
    if not p.exists(f"stories/approved/{sid}.json"):
        raise BAError("NOTHING_TO_REVERT", f"{sid} has no approved version to fall back to.")
    appr = p.read_json(f"stories/approved/{sid}.json")
    for b in PENDING_BUCKETS:
        if p.exists(f"stories/{b}/{sid}.json"):
            s = p.read_json(f"stories/{b}/{sid}.json")
            if s.get("supersedes_version") == appr["version"] or s["version"] > appr["version"]:
                s["review_history"].append({"action": "EDIT_REVERTED", "version": s["version"], "at_ist": ist_now_iso(),
                                            "note": normalize_ws(reason), "by_name": by_name})
                p.archive_story(s, status="SUPERSEDED")
                p.remove_story_files(b, sid)
                p.audit("STORY_EDIT_REVERTED", story_id=sid, discarded_version=s["version"], approved_version=appr["version"],
                        reason=reason, by_name=by_name, by_role=by_role)
                p.save_state(f"story-{sid}-edit-reverted")
                reconcile(p)
                return {"story_id": sid, "discarded_version": s["version"], "approved_version_in_force": appr["version"],
                        "discarded_copy": f"stories/archive/{sid}.v{s['version']}.json"}
    raise BAError("NOTHING_TO_REVERT", f"{sid} has no unapproved edited version.")


def story_history(p, sid: str) -> dict:
    e = tr.entry(p, sid)
    archived = [{"version": s["version"], "status": s["status"], "file": f"stories/archive/{sid}.v{s['version']}.json"}
                for s in p.archived_stories() if s["story_id"] == sid]
    pending = [{"bucket": b, "version": s["version"], "status": s["status"], "supersedes_version": s.get("supersedes_version"),
                "impact_review": s.get("impact_review")} for b, s in p.all_stories() if s["story_id"] == sid and b != "approved"]
    if not e and not archived and not pending and not p.exists(f"stories/approved/{sid}.json"):
        raise BAError("UNKNOWN_STORY", f"{sid} not found.")
    cur = p.read_json(f"stories/approved/{sid}.json") if p.exists(f"stories/approved/{sid}.json") else None
    return {"story_id": sid, "tracking_status": e["status"] if e else "UNTRACKED",
            "approved_versions": [{"version": v["version"], "event": v["event"], "approved_at_ist": v["approved_at_ist"],
                                   "approver": f"{v['approver_name']} — {v['approver_role']}" if v.get("approver_name") else None,
                                   "approval_statement": v.get("statement"), "material_hash": v["material_hash"], "snapshot": v["snapshot"],
                                   "in_force": bool(cur) and cur["version"] == v["version"]} for v in (e["versions"] if e else [])],
            "superseded_or_discarded": archived, "awaiting_review": pending,
            "changes": [_row(r) for r in records(p) if r["type"] == "STORY_CHANGE" and r["subject"].get("story_id") == sid]}


# ===================================================================== guard

def guard(p, op: str, raise_on_unreviewed: bool = True) -> dict:
    """Run before a command that writes downstream artifacts: re-check, auto-accept formatting-only differences, and
    refuse to continue while a material change has not been shown to the user and applied."""
    out = check(p, True, op)
    if raise_on_unreviewed and out["unreviewed_ids"]:
        recs = [get(p, c) for c in out["unreviewed_ids"]]
        raise BAError("CHANGES_UNREVIEWED",
                      f"Before `{op}`: {len(recs)} change(s) were detected that the user has not seen yet - " + " | ".join(
                          f"{r['change_id']}: {r['summary']}" for r in recs) +
                      ". Show each change summary and impact (changes/CHG-nnn.md), get the user's decision, then run "
                      "`changes apply --id CHG-nnn --by-name … --by-role … --statement …`. Nothing downstream has been changed.",
                      change_ids=out["unreviewed_ids"], changes=[{"change_id": r["change_id"], "summary": r["summary"], "impact": r["impact"],
                                                                  "required_actions": r["required_actions"]} for r in recs])
    return out


def summary(p) -> dict:
    """Pure (writes nothing): changes waiting for a decision, applied changes with open actions, and what is stale."""
    det = detect(p)
    recs = records(p)
    keys = {r["dedupe_key"] for r in recs if r["status"] in OPEN + ("RECORDED",)}
    undetected_src = None
    s = det["source"]
    if not s.get("missing") and not s.get("unchanged") and s["dedupe_key"] not in keys:
        undetected_src = s["classification"]
    undetected_st = sorted({i["story_id"] for i, _ in det["stories"] if i["dedupe_key"] not in keys})
    waiting = []
    for r in recs:
        if r["status"] == "APPLIED":
            st = _item_states(p, r)
            opn = [i["item_id"] for i in r["items"] if i["state"] == OPEN_ITEM and st.get(i["item_id"]) != DONE]
            if opn:
                waiting.append({"change_id": r["change_id"], "summary": r["summary"], "open_items": opn})
    stories = p.all_stories()
    m = p.baseline_manifest()
    return {"unreviewed": [{"change_id": r["change_id"], "type": r["type"], "summary": r["summary"]} for r in recs if r["status"] == "DETECTED"],
            "awaiting_reconciliation": waiting,
            "undetected": {"source": undetected_src, "stories": undetected_st, "source_missing": bool(s.get("missing"))},
            "stale": {"requirements_needing_review": sorted(r["requirement_id"] for r in p.requirements() if r["status"] == "NEEDS_REVIEW"),
                      "stories_flagged": sorted({x["story_id"] for _, x in stories if (x.get("impact_review") or {}).get("pending")}),
                      "gaps_flagged": sorted(g["gap_id"] for g in p.gaps() if g.get("needs_impact_review")),
                      "baseline_stale": bool(m["stale"])},
            "total_records": len(recs)}


def status(p) -> dict:
    return {**summary(p), "records": [_row(r) for r in records(p)]}
