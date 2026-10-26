"""Change detection and impact analysis for the supplied requirement files, the approved acceptance criteria and the enriched copies.

Lifecycle of a change record (`changes/CHG-nnn.json` + readable `.md`, indexed in `changes/change-register.json`):

  DETECTED    seen and explained (what changed, impact, required actions) - NOTHING downstream has been touched yet
  APPLIED     a named person saw the impact and asked for it to be applied: the new source is accepted and the affected artifacts are
              marked under review (acceptance criteria and gaps flagged, workflow reopened)
  RECONCILED  every required action is done (items below are all DONE / DISMISSED)
  RECORDED    formatting-only change, or a change the BA Agent itself made: logged, no review needed
  SUPERSEDED  the file changed again before this one was applied; REVERTED: the file went back to the accepted content

Nothing is ever overwritten or discarded silently: the supplied files are only read; an enriched copy that was edited by hand is kept
verbatim before it is regenerated; approvals are never invalidated without a recorded reason.

The enriched copy is a pure function of (accepted source, approved AC, gaps), so a hand edit of it is either a material edit of an
approved criterion (kept, becomes the next AC version for review) or text the next render restores.
"""
from __future__ import annotations

import copy
import difflib
import json
import re
from pathlib import Path

from . import actrack as tr, docmodel, docsync, ingest, reqindex, render, snapshots
from .project import ac_fname, physical
from .util import (BAError, atomic_write_bytes, canonical_json, ist_now_iso, is_within, normalize_ws, require_identity, sha256_bytes,
                   sha256_text)

REGISTER = "changes/change-register.json"
OPEN = ("DETECTED", "APPLIED")
MATERIAL, FORMATTING = "MATERIAL", "FORMATTING_ONLY"
DONE, DISMISSED, OPEN_ITEM = "DONE", "DISMISSED", "OPEN"
PENDING_BUCKETS = ("candidates", "unapproved", "deferred")
DISMISSABLE = ("new_requirement", "ac_removal")
REOPENS = ("AC_DRAFTING", "WAITING_FOR_AC_REVIEW", "COMPLETED")


def _clip(s: str, n: int = 240) -> str:
    s = normalize_ws(s)
    return s if len(s) <= n else s[: n - 1] + "…"


# ====================================================================== records

def records(p, status: str | None = None) -> list[dict]:
    out = []
    for f in p.files("changes", "CHG-*.json"):
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
    p.write_json(REGISTER, {"schema_version": "2.0", "updated_at": ist_now_iso(), "changes": rows})


def _event(rec: dict, event: str, note: str | None = None, **extra) -> None:
    rec["history"].append({"at_ist": ist_now_iso(), "event": event, "note": note, **extra})


def _new(p, ctype: str, classification: str, origin: str, summary: str, subject: dict, changes: list, impact: dict,
         actions: list, items: list, status: str, key: str, during: str, **extra) -> dict:
    ids = [r["change_id"] for r in records(p)]
    cid = p.next_id("CHG", ids)
    now = ist_now_iso()
    rec = {"schema_version": "2.0", "change_id": cid, "type": ctype, "status": status, "classification": classification,
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


def _item(kind: str, ref: str, text: str) -> dict:
    return {"item_id": f"{kind}:{ref}", "kind": kind, "ref": ref, "text": text, "state": OPEN_ITEM, "closed_at_ist": None,
            "closed_by": None, "reason": None}


# ===================================================== requirement-source change detection

def _outputs(p) -> tuple[Path, ...]:
    return (p.root, p.enriched_root)


def detect_source(p) -> dict:
    """Compare the supplied files with the accepted index. Reads only; writes nothing."""
    src = p.state["source"]
    root = Path(src["root"])
    if not root.exists():
        return {"missing": True, "path": str(root)}
    doc = reqindex.load(p)
    sc = reqindex.scan(root, src["kind"], src["recursive"], src["locator"]["id_pattern"], exclude=tuple(x for x in _outputs(p) if is_within(root, x)),
                       name=Path(src["path"]).name if src["kind"] == "file" else None)
    if sc["enriched_files"]:
        return {"enriched_as_source": sc["enriched_files"]}
    if sc["unreadable"]:
        return {"unreadable": sc["unreadable"]}
    old_files = {r: f for r, f in doc["files"].items() if f["status"] == "ACTIVE"}
    new_hash = {r: f["file_hash"] for r, f in sc["files"].items()}
    old_hash = {r: f["file_hash"] for r, f in old_files.items()}
    new_doc = {"files": {r: {"file_hash": h, "status": "ACTIVE"} for r, h in new_hash.items()}}
    if new_hash == old_hash:
        return {"unchanged": True, "scan": sc}
    new_ents = {e["req_key"]: e for e in reqindex.make_entries(sc, src["locator"]["id_pattern"])}
    old_ents = {e["req_key"]: e for e in doc["requirements"] if e["status"] == "ACTIVE"}
    both = [k for k in new_ents if k in old_ents and new_ents[k]["text_hash"] != old_ents[k]["text_hash"]]
    diff = {"added": sorted(k for k in new_ents if k not in old_ents), "removed": sorted(k for k in old_ents if k not in new_ents),
            "changed": sorted(k for k in both if new_ents[k]["material_hash"] != old_ents[k]["material_hash"]),
            "formatting_only": sorted(k for k in both if new_ents[k]["material_hash"] == old_ents[k]["material_hash"]),
            "added_files": sorted(set(new_hash) - set(old_hash)), "removed_files": sorted(set(old_hash) - set(new_hash)),
            "changed_files": sorted(r for r in new_hash if r in old_hash and new_hash[r] != old_hash[r])}
    material = bool(diff["added"] or diff["removed"] or diff["changed"] or diff["added_files"] or diff["removed_files"])
    if not material:
        cls, why = FORMATTING, "only layout / markup (or text outside the requirements) differs: wording, numbers and structure of every requirement are unchanged"
    else:
        cls, why = MATERIAL, None
    return {"scan": sc, "classification": cls, "reason": why, "diff": diff, "new_entries": new_ents, "old_entries": old_ents,
            "new_fingerprint": reqindex.tree_fingerprint(new_doc), "dedupe_key": "source:" + reqindex.tree_fingerprint(new_doc)}


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


def _new_text(det: dict, e: dict) -> str:
    f = det["scan"]["files"][e["file"]]
    return docmodel.unit_text(docmodel.split_lines(f["text"]), e["line_start"], e["line_end"])


def _source_details(p, det: dict) -> list[dict]:
    d, old, new = det["diff"], det["old_entries"], det["new_entries"]
    out = []
    for rel in d["added_files"]:
        out.append({"change": "FILE_ADDED", "file": rel})
    for rel in d["removed_files"]:
        out.append({"change": "FILE_REMOVED", "file": rel, "note": "its requirements are removed; the enriched copy is removed too (kept in state/backups/)"})
    for k in d["added"]:
        out.append({"change": "ADDED", "req_key": k, "title": new[k]["title"], "excerpt": _clip(_new_text(det, new[k]), 400)})
    for k in d["removed"]:
        out.append({"change": "DELETED", "req_key": k, "title": old[k]["title"], "excerpt": _clip(_safe_text(p, old[k]), 400)})
    for k in d["changed"]:
        out.append({"change": "MODIFIED", "req_key": k, "title": new[k]["title"], "hunks": _hunks(_safe_text(p, old[k]), _new_text(det, new[k]))})
    for k in d["formatting_only"]:
        out.append({"change": "FORMATTING_ONLY", "req_key": k, "title": new[k]["title"]})
    return out


def _safe_text(p, e: dict) -> str:
    try:
        return reqindex.text(p, e)
    except BAError:
        return ""


def _ac_row(bucket: str, a: dict) -> dict:
    return {"req_key": a["req_key"], "version": a["version"], "bucket": bucket, "status": a["status"], "approved": bucket == "approved"}


def _source_impact(p, det: dict) -> dict:
    d, new, old = det["diff"], det["new_entries"], det["old_entries"]
    touched = set(d["changed"]) | set(d["removed"])
    decs = p.decisions()
    affected = []
    for k in sorted(touched):
        e = old[k]
        affected.append({"req_key": k, "title": e["title"], "impact": "REMOVED" if k in d["removed"] else "CHANGED_MATERIALLY",
                         "has_confirmed_decisions": any(k in v.get("related_artifact_ids", []) and v["current_status"] == "ACTIVE" for v in decs.values())})
    gaps = p.gaps()
    open_gaps = [{"gap_id": g["gap_id"], "title": g["title"], "priority": g["priority"], "status": g["status"]}
                 for g in gaps if g["status"] != "RESOLVED" and set(g["impacted_req_keys"]) & touched]
    resolved = [g for g in gaps if g["status"] == "RESOLVED" and set(g["impacted_req_keys"]) & touched]
    reval = sorted({g["decision_id"] for g in resolved if g.get("decision_id")} |
                   {k for k, v in decs.items() if v["current_status"] == "ACTIVE" and set(v.get("related_artifact_ids", [])) & touched})
    acs = [_ac_row(b, a) for b, a in p.all_acs() if a["req_key"] in touched]
    reopens = bool(d["changed"] or d["added"] or d["removed"]) and p.state["stage"] in REOPENS
    return {"affected_requirements": affected, "new_requirements": [{"req_key": k, "title": new[k]["title"]} for k in d["added"]],
            "open_gaps": open_gaps, "decisions_to_revalidate": reval,  # never invalidated automatically: reassessing the requirement decides
            "linked_acs": acs, "approved_acs_at_risk": [x["req_key"] for x in acs if x["approved"]],
            "workflow": {"stage": p.state["stage"], "will_reopen_to": "GAP_ANALYSIS" if reopens else None}}


def _source_actions(p, imp: dict) -> list[str]:
    a = []
    if imp["affected_requirements"] or imp["new_requirements"]:
        a.append("Re-run gap analysis for the changed and new requirements (`req show`, `gaps add`, then `gaps commit --cross-pass-done`).")
    if imp["open_gaps"]:
        a.append("Re-check open gap(s) " + ", ".join(g["gap_id"] for g in imp["open_gaps"]) + ": does the question still stand?")
    if imp["decisions_to_revalidate"]:
        a.append("Re-validate confirmed decision(s) " + ", ".join(imp["decisions_to_revalidate"]) + " against the new text; they are NOT invalidated automatically.")
    if imp["linked_acs"]:
        a.append("Review the linked acceptance criteria (" + ", ".join(x["req_key"] for x in imp["linked_acs"]) + "): `ac edit` (new version, "
                 "needs re-approval) or `ac confirm-unaffected`; criteria of a removed requirement are archived (`ac archive`). "
                 "Approvals stay on record until a reviewer decides.")
    if imp["new_requirements"]:
        a.append("Write acceptance criteria for the new requirement(s) once their gaps are known, or dismiss with a reason "
                 "(`changes dismiss --item new_requirement:<key>`).")
    a.append("The enriched copies are re-generated from the new text as soon as the change is applied.")
    return a


def _source_summary(det: dict, imp: dict) -> str:
    d = det["diff"]
    return (f"Requirements changed: {len(d['added'])} added, {len(d['changed'])} modified, {len(d['removed'])} removed"
            f"{', ' + str(len(d['formatting_only'])) + ' formatting-only' if d['formatting_only'] else ''}"
            f"{', files +' + str(len(d['added_files'])) + '/-' + str(len(d['removed_files'])) if d['added_files'] or d['removed_files'] else ''}; "
            f"{len(imp['open_gaps'])} open gap(s) and {len(imp['linked_acs'])} acceptance-criteria record(s) affected")


# ================================================ acceptance-criteria record detection (json)

def _assess_ac(p, key: str, events: list[dict] | None = None) -> tuple[dict | None, dict]:
    """Compare the approved record on disk with what was approved. Returns (change item | None, context for applying it)."""
    e = tr.entry(p, key)
    name = ac_fname(key)
    jf = f"acceptance/approved/{name}.json"
    has_json = p.exists(jf)
    ctx: dict = {"entry": e}
    if has_json:
        ctx["raw"] = p.path(jf).read_bytes()
    if e is None:
        if not has_json:
            return None, ctx
        try:
            a = json.loads(ctx["raw"].decode("utf-8"))
        except (ValueError, OSError):
            return {"req_key": key, "change_type": "UNREADABLE", "classification": MATERIAL, "origin": "MANUAL", "edited_via": "json",
                    "approved_version": None, "diff": {}, "dedupe_key": f"ac:{key}:UNREADABLE"}, ctx
        ev = events if events is not None else p.audit_events()
        if any(x.get("event_type") == "AC_APPROVED" and x.get("req_key") == key and x.get("version") == a.get("version") for x in ev):
            ctx.update(adopt=a)
            return None, ctx
        h = tr.content_hash(a)
        ctx.update(record=a)
        return {"req_key": key, "change_type": "UNTRACKED_APPROVED", "classification": MATERIAL, "origin": "MANUAL", "edited_via": "json",
                "approved_version": None, "diff": {}, "observed": {"content_hash": h}, "dedupe_key": f"ac:{key}:UNTRACKED:{h}"}, ctx
    ver = tr.current(e)
    snap = tr.snapshot_record(p, ver)
    ctx.update(ver=ver, snap=snap)
    if not has_json:
        if e["status"] != tr.TRACKED:
            return None, ctx
        return {"req_key": key, "change_type": "DELETED", "classification": MATERIAL, "origin": "MANUAL", "edited_via": None,
                "approved_version": ver["version"], "diff": {}, "dedupe_key": f"ac:{key}:DELETED:v{ver['version']}"}, ctx
    raw = ctx["raw"]
    try:
        a = json.loads(raw.decode("utf-8"))
        assert isinstance(a, dict)
    except (ValueError, AssertionError):
        return {"req_key": key, "change_type": "UNREADABLE", "classification": MATERIAL, "origin": "MANUAL", "edited_via": "json",
                "approved_version": ver["version"], "diff": {}, "dedupe_key": f"ac:{key}:UNREADABLE:{sha256_bytes(raw)}"}, ctx
    ctx["record"] = a
    obs = tr.content_hash(a)
    ctrl = tr.approval_hash(a) != ver["approval_hash"]
    mat = tr.material_hash(a) != ver["material_hash"]
    json_changed = obs != (ver.get("accepted_content_hash") or ver["content_hash"])
    base = {"req_key": key, "origin": "MANUAL", "approved_version": ver["version"], "control_fields_altered": ctrl,
            "observed": {"content_hash": obs, "material_hash": tr.material_hash(a)},
            "approved": {"material_hash": ver["material_hash"], "approved_at_ist": ver.get("approved_at_ist"),
                         "approver_name": ver.get("approver_name"), "approver_role": ver.get("approver_role")}}
    dk = f"ac:{key}:{obs}"
    if ctrl or mat:
        d = tr.diff(snap, a) if snap else {"approved_snapshot": {"before": "available", "after": "missing or damaged"}}
        if ctrl:
            d["approval_record"] = {"before": "as approved", "after": "readiness / status / version / approval fields altered"}
        return {**base, "change_type": "MANUAL_EDIT" if mat else "APPROVAL_ALTERED", "classification": MATERIAL, "edited_via": "json",
                "diff": d, "dedupe_key": dk}, ctx
    if json_changed:
        return {**base, "change_type": "FORMATTING_ONLY", "classification": FORMATTING, "edited_via": "json", "diff": {}, "dedupe_key": dk}, ctx
    return None, ctx


def detect_acs(p) -> tuple[list[tuple[dict, dict]], list[dict]]:
    keys = sorted({a["req_key"] for _, a in p.all_acs() if _ == "approved"} | set(tr.known_keys(p)))
    events = p.audit_events()
    items, adopt = [], []
    for k in keys:
        item, ctx = _assess_ac(p, k, events)
        if ctx.get("adopt"):
            adopt.append(ctx["adopt"])
        if item:
            items.append((item, ctx))
    return items, adopt


# ==================================================== enriched-copy detection

def detect_enriched(p) -> list[tuple[dict, dict]]:
    """What differs between each enriched copy and what was last generated. A hand edit of an approved criterion inside a BA block is
    a MATERIAL `MANUAL_EDIT`; anything else (text outside the blocks, damaged markers, generated parts, a deleted file) is restored by
    the next render and only recorded."""
    man = docsync.manifest(p)["files"]
    if not man:
        return []
    try:
        exp = docsync.expected(p)
    except BAError:
        return []
    approved = {a["req_key"]: a for b, a in p.all_acs() if b == "approved"}
    items = []
    for rel, ent in sorted(man.items()):
        e = exp.get(rel)
        if e is None:
            continue
        path = docsync.disk_path(p, rel)
        if not path.exists():
            items.append(({"file": rel, "change_type": "FILE_DELETED", "classification": FORMATTING, "origin": "MANUAL",
                           "reasons": ["the enriched copy was deleted"], "dedupe_key": f"enriched:{rel}:deleted"}, {"expected": e}))
            continue
        raw = path.read_bytes()
        h = sha256_bytes(raw)
        if h == ent["expected_hash"] or h == sha256_bytes(e["text"].encode("utf-8")):
            continue
        text = raw.decode("utf-8", errors="replace")
        ctx = {"expected": e, "raw": raw, "text": text}
        blocks = {b["key"]: b for b in docmodel.find_blocks(text)}
        silent = []
        n_before = len(items)
        if docmodel.strip_blocks(text) != e["source_text"]:
            silent.append("text outside the BA blocks was edited (change the source document instead)")
        if not docmodel.markers_balanced(text):
            silent.append("a BA marker was damaged")
        silent += [f"an unexpected BA block for {k}" for k in blocks if k not in e["blocks"]]
        for key, body in e["blocks"].items():
            b = blocks.get(key)
            if b is None:
                silent.append(f"the block for {key} was removed")
                continue
            if docmodel.normalize_body(b["body"]) == docmodel.normalize_body("\n".join(body)):
                continue
            obs = docmodel.criteria_from_body(b["body"])
            ac = approved.get(key)
            d = tr.diff({"acceptance_criteria": ac["acceptance_criteria"]}, {"acceptance_criteria": obs}) if ac and obs is not None else {}
            if d:
                items.append(({"req_key": key, "file": rel, "change_type": "MANUAL_EDIT", "classification": MATERIAL, "origin": "MANUAL",
                               "edited_via": "enriched", "approved_version": ac["version"], "diff": d,
                               "observed": {"criteria": obs, "block": b["body"]},
                               "dedupe_key": f"enriched:{key}:{sha256_text(canonical_json(obs))}"}, ctx))
            else:
                silent.append(f"the generated text of the block for {key} was edited")
        if not silent and len(items) == n_before:  # differs from what was generated in a way none of the checks above names (marker attributes, spacing)
            silent.append("the generated markers or spacing were changed")
        if silent:
            items.append(({"file": rel, "change_type": "ENRICHED_TEXT_EDITED", "classification": FORMATTING, "origin": "MANUAL",
                           "reasons": silent, "dedupe_key": f"enriched:{rel}:{h}"}, ctx))
    return items


def _ac_impact(p, key: str, old: dict | None, new: dict | None, diff: dict) -> dict:
    live = reqindex.by_key(p)
    gaps = []
    if key in live and live[key]["status"] == "ACTIVE":
        from . import acs
        gaps = [{"gap_id": g["gap_id"], "title": g["title"], "priority": g["priority"], "status": g["status"]} for g in acs.open_gap_context(p, [key])]
    ac = diff.get("acceptance_criteria") or {"added": [], "removed": []}
    ver = (old or {}).get("approval") or {}
    return {"req_key": key, "fields_changed": sorted(diff), "summary_of_fields": tr.summarize_diff(diff),
            "criteria": {"added": ac.get("added", []), "removed": ac.get("removed", []), "reordered": bool(ac.get("reordered"))},
            "open_gaps_on_requirement": gaps,
            "approval_record": {"version": (old or {}).get("version"), "approved_at_ist": ver.get("at_ist"),
                                "approver_name": ver.get("approver_name"), "approver_role": ver.get("approver_role")}}


def _ac_actions(item: dict, imp: dict, nv: int | None = None) -> list[str]:
    t, key = item["change_type"], item["req_key"]
    if t == "DELETED":
        return [f"Decide: restore the approved criteria for {key} (`ac restore {key}`) or confirm their removal (`changes dismiss --item ac_removal:{key}`).",
                "The requirement will show as having no approved criteria until you decide."]
    if t == "FORMATTING_ONLY":
        return []
    return [f"Review the edit to the criteria of {key}. After you apply this change it becomes v{nv or '?'} (CANDIDATE) and the approved version "
            f"stays on record untouched (and in the enriched copy); approve v{nv or '?'} (`ac approve`), edit it further, or discard it "
            f"(`ac revert-edit {key}`)."]


# ============================================================== detection facade

def detect(p) -> dict:
    """Read-only: what differs from the accepted source, from what reviewers approved and from what was generated. Writes nothing."""
    items, adopt = detect_acs(p)
    return {"source": detect_source(p), "acs": items, "adopt": adopt, "enriched": detect_enriched(p)}


def _open_by_key(p, key: str) -> dict | None:
    return next((r for r in records(p) if r["dedupe_key"] == key and r["status"] in OPEN), None)


def _source_parts(p, det: dict) -> dict:
    imp = _source_impact(p, det)
    return {"changes": _source_details(p, det), "impact": imp, "actions": _source_actions(p, imp), "summary": _source_summary(det, imp)}


def _ac_parts(p, item: dict, ctx: dict) -> dict:
    t, key = item["change_type"], item["req_key"]
    snap, new = ctx.get("snap"), ctx.get("record")
    if item.get("edited_via") == "enriched":
        snap = next((a for b, a in p.all_acs() if b == "approved" and a["req_key"] == key), None)
        new = dict(snap or {}, acceptance_criteria=item["observed"]["criteria"])
    imp = _ac_impact(p, key, snap, new if t not in ("DELETED", "UNREADABLE") else None, item["diff"])
    nv = _next_version(p, key) if t not in ("DELETED", "FORMATTING_ONLY") else None
    summary = {
        "DELETED": f"Approved criteria for {key} v{item['approved_version']} were deleted from acceptance/approved/",
        "UNREADABLE": f"The approved criteria file for {key} can no longer be read",
        "UNTRACKED_APPROVED": f"Acceptance criteria for {key} sit in acceptance/approved/ with no approval on record",
        "APPROVAL_ALTERED": f"The approval fields of {key} v{item['approved_version']} were altered by hand",
        "MANUAL_EDIT": f"Approved criteria for {key} v{item['approved_version']} were edited"
                       f"{' in the enriched copy' if item.get('edited_via') == 'enriched' else ''}: {imp['summary_of_fields']}",
    }.get(t, f"Approved criteria for {key} changed")
    changes = [{"change": t, "req_key": key, "fields": item["diff"]}] if item["diff"] else [{"change": t, "req_key": key}]
    return {"changes": changes, "impact": imp, "actions": _ac_actions(item, imp, nv), "summary": summary}


def _next_version(p, key: str) -> int:
    from . import acs
    return acs.next_version(p, key)


def _enriched_summary(item: dict) -> str:
    return (f"The enriched copy {item['file']} was changed by hand ({'; '.join(item['reasons'])}); it was restored to the generated "
            "content and the edit kept in state/backups/. The supplied source is the place to change requirement text.")


# ====================================================================== check

def check(p, record: bool = True, during: str = "manual check") -> dict:
    """Detect source, approved-AC and enriched-copy changes, explain their impact and (with record=True) log them. Never changes a
    downstream artifact except to accept a formatting-only difference or restore generated text (recorded, review-free bookkeeping)."""
    det = detect(p)
    out = {"checked_at_ist": ist_now_iso(), "during": during, "recorded": record, "warnings": [], "source": "unchanged",
           "changes": [], "formatting_only_accepted": []}
    cur_keys: set[str] = set()
    cur_src_key = None

    s = det["source"]
    if s.get("missing"):
        out["source"] = "missing"
        out["warnings"].append(f"The requirements folder is missing: {s['path']}. Restore it; existing artifacts remain valid.")
    elif s.get("enriched_as_source"):
        out["source"] = "invalid"
        out["warnings"].append(f"{s['enriched_as_source']} now carry BA Agent markers: they look like enriched copies, not the original requirements. Nothing was changed.")
    elif s.get("unreadable"):
        out["source"] = "unreadable"
        out["warnings"].append("These requirement files cannot be read, so no change was assessed: "
                               + "; ".join(f"{u['file']}: {u['error']}" for u in s["unreadable"]))
    elif s.get("unchanged"):
        pass
    else:
        out["source"] = "formatting_only" if s["classification"] == FORMATTING else "changed"
        if s["classification"] == FORMATTING:
            if record:
                out["formatting_only_accepted"].append(_row(accept_source_formatting(p, s, during)))
        else:
            cur_src_key = s["dedupe_key"]
            parts = _source_parts(p, s)
            rec = _open_by_key(p, cur_src_key)
            if record and rec is None:
                rec = _new(p, "SOURCE_CHANGE", MATERIAL, "SOURCE_FILE", parts["summary"],
                           {"previous_fingerprint": p.state["source"]["fingerprint"], "new_fingerprint": s["new_fingerprint"], "files": {
                               "added": s["diff"]["added_files"], "removed": s["diff"]["removed_files"], "changed": s["diff"]["changed_files"]}},
                           parts["changes"], parts["impact"], parts["actions"], [], "DETECTED", cur_src_key, during)
            out["changes"].append(rec if rec else {"change_id": None, "status": "PREVIEW", "type": "SOURCE_CHANGE", **{
                k: parts[k] for k in ("summary", "changes", "impact")}, "required_actions": parts["actions"]})

    material_files = {i["file"] for i, _ in det["enriched"] if i["classification"] == MATERIAL}
    for item, ctx in det["acs"]:
        key = item["req_key"]
        if item["classification"] == FORMATTING:
            if record:
                out["formatting_only_accepted"].append(_row(accept_ac_formatting(p, item, ctx, during)))
            continue
        cur_keys.add(item["dedupe_key"])
        rec = _open_by_key(p, item["dedupe_key"])
        parts = _ac_parts(p, item, ctx)
        if record and rec is None:
            rec = _new(p, "AC_CHANGE", MATERIAL, "MANUAL", parts["summary"],
                       {"req_key": key, "change_type": item["change_type"], "edited_via": item["edited_via"],
                        "approved_version": item.get("approved_version"), "observed": item.get("observed"), "approved": item.get("approved"),
                        "control_fields_altered": item.get("control_fields_altered", False)},
                       parts["changes"], parts["impact"], parts["actions"], [], "DETECTED", item["dedupe_key"], during)
        out["changes"].append(rec if rec else {"change_id": None, "status": "PREVIEW", "type": "AC_CHANGE", "summary": parts["summary"],
                                                  "changes": parts["changes"], "impact": parts["impact"], "required_actions": parts["actions"]})
    for item, ctx in det["enriched"]:
        if item["classification"] == MATERIAL:
            key = item["req_key"]
            cur_keys.add(item["dedupe_key"])
            rec = _open_by_key(p, item["dedupe_key"])
            parts = _ac_parts(p, item, ctx)
            if record and rec is None:
                rec = _new(p, "AC_CHANGE", MATERIAL, "MANUAL", parts["summary"],
                           {"req_key": key, "file": item["file"], "change_type": "MANUAL_EDIT", "edited_via": "enriched",
                            "approved_version": item["approved_version"], "observed": item["observed"]},
                           parts["changes"], parts["impact"], parts["actions"], [], "DETECTED", item["dedupe_key"], during)
            out["changes"].append(rec if rec else {"change_id": None, "status": "PREVIEW", "type": "AC_CHANGE", "summary": parts["summary"],
                                                      "changes": parts["changes"], "impact": parts["impact"], "required_actions": parts["actions"]})
        elif record and item["file"] not in material_files:
            out["formatting_only_accepted"].append(_row(accept_enriched_silent(p, item, ctx, during)))
    if record:
        for a in det["adopt"]:
            tr.record_approved(p, a, event="ADOPTED")
            p.audit("AC_TRACKING_ADOPTED", req_key=a["req_key"], version=a["version"],
                    note="approved before change tracking existed; current content taken as the approved content")
        _heal_reappeared(p, det)
        _retire_stale(p, cur_src_key, cur_keys)
    out.update(_state_summary(p))
    out["requires_review"] = bool(out["unreviewed_ids"]) or (not record and any(c.get("status") == "PREVIEW" for c in out["changes"]))
    out["next"] = _next_text(out)
    return out


def _heal_reappeared(p, det: dict) -> None:
    flagged = {i["req_key"] for i, _ in det["acs"]}
    for key, e in tr.index(p)["acs"].items():
        if e["status"] != tr.TRACKED and p.exists(f"acceptance/approved/{ac_fname(key)}.json") and key not in flagged:
            tr.update_entry(p, key, status=tr.TRACKED)
            p.audit("AC_REAPPEARED", req_key=key, note="the approved criteria are back and match what was approved")


def _retire_stale(p, src_key: str | None, ac_keys: set[str]) -> None:
    for r in records(p):
        if r["status"] not in OPEN or r["origin"] == "BA_AGENT" or r["dedupe_key"] in ({src_key} | ac_keys):
            continue
        if r["type"] == "SOURCE_CHANGE":
            if src_key:
                _close(p, r, "SUPERSEDED", "the requirements changed again before this change was resolved", _open_by_key(p, src_key)["change_id"])
            elif r["status"] == "DETECTED":
                _close(p, r, "REVERTED", "the requirement files match the accepted version again; nothing was applied")
        elif r["type"] == "AC_CHANGE" and r["status"] == "DETECTED" and r["subject"].get("change_type") not in ("ENRICHED_TEXT_EDITED",):
            key = r["subject"]["req_key"]
            newer = next((x for x in records(p) if x["status"] in OPEN and x["type"] == "AC_CHANGE" and x["subject"].get("req_key") == key
                          and x["dedupe_key"] in ac_keys and x["change_id"] != r["change_id"]), None)
            if newer:
                _close(p, r, "SUPERSEDED", "the criteria were edited again before this change was resolved", newer["change_id"])
            else:
                _close(p, r, "REVERTED", "the approved criteria match what was approved again; nothing was applied")


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

def _accept_scan(p, det: dict, reason: str, change_id: str | None) -> None:
    """Make the scanned files the accepted source: snapshots, the index (line extents move), the fingerprint, the enriched copies."""
    sc = det["scan"]
    for rel in det["diff"]["changed_files"] + det["diff"]["added_files"]:
        snapshots.record(p, rel, sc["files"][rel]["data"], reason, change_id)
    doc = reqindex.build_doc(sc, p.state["source"]["locator"], previous=reqindex.load(p))
    reqindex.write(p, doc)
    p._index = None
    p.state["source"]["fingerprint"] = reqindex.tree_fingerprint(doc)
    p.save_state("source-accepted")
    p.sync_docs()


def accept_source_formatting(p, det: dict, during: str = "check") -> dict:
    old = p.state["source"]["fingerprint"]
    reason = det.get("reason") or "formatting only"
    _accept_scan(p, det, "formatting-only change accepted", None)
    rec = _new(p, "SOURCE_CHANGE", FORMATTING, "SOURCE_FILE", f"Requirement files reformatted only ({reason}); no review needed",
               {"previous_fingerprint": old, "new_fingerprint": p.state["source"]["fingerprint"],
                "files": {"added": [], "removed": [], "changed": det["diff"]["changed_files"]}},
               [{"change": "FORMATTING_ONLY", "req_key": k, "title": det["new_entries"][k]["title"]} for k in det["diff"]["formatting_only"]],
               {"affected_requirements": [], "linked_acs": [], "open_gaps": []}, [], [], "RECORDED", det["dedupe_key"], during)
    p.audit("SOURCE_FORMATTING_CHANGE_RECORDED", change_id=rec["change_id"], previous=old, new=p.state["source"]["fingerprint"], reason=reason)
    return rec


def accept_ac_formatting(p, item: dict, ctx: dict, during: str = "check") -> dict:
    key, ver = item["req_key"], ctx["ver"]
    tr.update_version(p, key, ver["version"], accepted_content_hash=item["observed"]["content_hash"])
    rec = _new(p, "AC_CHANGE", FORMATTING, "MANUAL", f"Approved criteria for {key} v{ver['version']} reformatted only; approval unchanged",
               {"req_key": key, "change_type": "FORMATTING_ONLY", "approved_version": ver["version"], "observed": item["observed"]},
               [{"change": "FORMATTING_ONLY", "req_key": key}], {"req_key": key}, [], [], "RECORDED", item["dedupe_key"], during)
    p.audit("AC_FORMATTING_CHANGE_RECORDED", req_key=key, version=ver["version"], change_id=rec["change_id"])
    return rec


def accept_enriched_silent(p, item: dict, ctx: dict, during: str = "check") -> dict:
    """An edit to the enriched copy that is not a change to an approved criterion: keep it verbatim, restore the generated content."""
    rel = item["file"]
    keep = None
    if ctx.get("raw") is not None:
        keep = f"state/backups/enriched__{rel.replace('/', '__')}.observed-{sha256_bytes(ctx['raw'])[7:15]}.md"
        atomic_write_bytes(p.path(keep), ctx["raw"])
    docsync.refresh(p, force=True, only={rel})
    rec = _new(p, "AC_CHANGE", FORMATTING, "MANUAL", _enriched_summary(item),
               {"req_key": None, "file": rel, "change_type": item["change_type"], "reasons": item["reasons"], "kept_as": keep},
               [{"change": item["change_type"], "file": rel, "note": "; ".join(item["reasons"])}], {"file": rel}, [], [], "RECORDED",
               item["dedupe_key"], during)
    p.audit("ENRICHED_COPY_RESTORED", file=rel, change_id=rec["change_id"], change_type=item["change_type"], reasons=item["reasons"], kept_as=keep)
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
    """Apply a DETECTED change after its impact was shown: accept the new source / keep the edit, mark the affected artifacts under review."""
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
        effects, items = _apply_ac(p, rec)
    rec["status"], rec["items"] = "APPLIED", items
    rec["applied"] = {"at_ist": ist_now_iso(), **(ack or {"by_name": None, "by_role": None, "statement": None}), "effects": effects}
    _event(rec, "APPLIED", (ack or {}).get("statement"), by_name=(ack or {}).get("by_name"))
    _save(p, rec)
    p.audit("CHANGE_APPLIED", change_id=cid, change_type=rec["type"], by_name=(ack or {}).get("by_name"), by_role=(ack or {}).get("by_role"),
            statement=(ack or {}).get("statement"), effects={k: v for k, v in effects.items() if isinstance(v, (list, str, bool, int, type(None)))})
    reconcile(p)
    return {"change_id": cid, "status": get(p, cid)["status"], "effects": effects, "open_items": [i for i in get(p, cid)["items"] if i["state"] == OPEN_ITEM],
            "next": "Work through the open items (see `changes show " + cid + "`); each is closed by the normal command (gaps add/commit, "
                    "ac edit/approve/confirm-unaffected/archive). Progress: `changes status`."}


def _apply_source(p, rec: dict) -> tuple[dict, list]:
    s = detect_source(p)
    if s.get("missing") or s.get("unchanged") or s.get("unreadable") or s.get("enriched_as_source") or s["dedupe_key"] != rec["dedupe_key"]:
        raise BAError("CHANGE_RECORD_STALE", f"The requirement files are no longer in the state {rec['change_id']} described. Run `changes check` "
                      "again and show the user the new impact before applying anything.")
    d, old, new = s["diff"], s["old_entries"], s["new_entries"]
    cid = rec["change_id"]
    touched = set(d["changed"]) | set(d["removed"])
    p.audit("SOURCE_CHANGED", previous=p.state["source"]["fingerprint"], new=s["new_fingerprint"], change_id=cid,
            diff={k: len(v) for k, v in d.items()})
    _accept_scan(p, s, "requirement change accepted after impact review", cid)
    flagged_acs = []
    for k in sorted(touched):
        why = f"{p.label(k)} was removed from the source document" if k in d["removed"] else f"the text of {p.label(k)} changed in the source document"
        flagged_acs += p.flag_acs({k}, why, "source-change", change_id=cid)
    gaps = p.gaps()
    flagged_gaps = []
    for g in gaps:
        if set(g["impacted_req_keys"]) & touched and not g.get("needs_impact_review") and g["status"] != "RESOLVED":
            g["needs_impact_review"], g["updated_at"] = True, ist_now_iso()
            flagged_gaps.append(g["gap_id"])
    if flagged_gaps:
        p.save_gaps(gaps, touched=flagged_gaps)
        p.audit("GAP_IMPACT_FLAGGED", gap_ids=flagged_gaps, cause="source-change", change_id=cid)
    needs_analysis = bool(d["changed"] or d["added"])
    if needs_analysis:
        p.state["gap_analysis_complete"] = False
    if p.state["stage"] in REOPENS and (needs_analysis or d["removed"]):
        p.reopen_to("GAP_ANALYSIS", "source-change")
    p.save_state("source-change-applied")
    items = []
    for k in d["changed"]:
        items.append(_item("requirement", k, f"Reassess {p.label(k)}: re-run gap analysis for it (`gaps commit --cross-pass-done` when done)"))
    for k in d["added"]:
        items.append(_item("new_requirement", k, f"New requirement {p.label(k)}: find its gaps and write its criteria, or dismiss with a reason"))
    for g in flagged_gaps:
        items.append(_item("gap", g, f"Re-check {g}, then `gaps commit --cross-pass-done`"))
    for k in sorted({a["req_key"] for _, a in p.all_acs() if a["req_key"] in touched}):
        how = "archive them (`ac archive`)" if k in d["removed"] else "`ac edit` (new version) or `ac confirm-unaffected`"
        items.append(_item("ac", k, f"Review the acceptance criteria of {p.label(k)}: {how}"))
    return {"requirements_changed": d["changed"], "requirements_added": d["added"], "requirements_removed": d["removed"],
            "acs_flagged": flagged_acs, "gaps_flagged": flagged_gaps, "stage": p.state["stage"],
            "enriched_copies": "regenerated from the new text"}, items


def _all_versions(p, key: str):
    for b in ("candidates", "unapproved", "deferred", "approved"):
        f = f"acceptance/{b}/{ac_fname(key)}.json"
        if p.exists(f):
            yield b, p.read_json(f)


def _flag_approved(p, key: str, cid: str, why: str) -> None:
    a = p.read_json(f"acceptance/approved/{ac_fname(key)}.json")
    now = ist_now_iso()
    a["impact_review"] = {"pending": True, "reason": why, "cause": "manual-edit", "change_id": cid, "at_ist": now}
    a["review_history"].append({"action": "IMPACT_FLAGGED", "version": a["version"], "at_ist": now, "note": why, "change_id": cid})
    a["updated_at"] = now
    p.save_ac("approved", a)


def _build_candidate(p, src: dict, approved_version: int | None, cid: str, note: str) -> dict:
    from . import acs
    c = copy.deepcopy(src)
    key = c["req_key"]
    nv, now = _next_version(p, key), ist_now_iso()
    live = reqindex.by_key(p).get(key)
    if live and live["status"] == "ACTIVE":
        d = acs._derived(p, key)
        c.update(decision_ids=d["decision_ids"], requirement_hash=d["requirement_hash"], requirement_title=d["title"])
    c.update(version=nv, status="CANDIDATE", approval=None, deferral=None, impact_review=None, supersedes_version=approved_version, updated_at=now)
    c["readiness"] = acs.readiness_of(c.get("open_gap_refs") or [])
    c.setdefault("review_history", []).append({"action": "MANUAL_EDIT_DETECTED", "version": nv, "at_ist": now, "note": _clip(note, 300),
                                               "change_id": cid, "from_version": approved_version})
    return c


def _reopen_if_completed(p, why: str) -> None:
    if p.state["stage"] == "COMPLETED":
        p.reopen_to("WAITING_FOR_AC_REVIEW", why)


def _apply_ac(p, rec: dict) -> tuple[dict, list]:
    sub = rec["subject"]
    key = sub["req_key"]
    if sub.get("edited_via") == "enriched":
        return _apply_enriched_edit(p, rec)
    item, ctx = _assess_ac(p, key)
    if not item or item["dedupe_key"] != rec["dedupe_key"]:
        raise BAError("CHANGE_RECORD_STALE", f"The criteria for {key} are no longer in the state {rec['change_id']} described. Run `changes check` again.")
    cid, t, now = rec["change_id"], item["change_type"], ist_now_iso()
    eff: dict = {"req_key": key, "change_type": t}
    if t == "DELETED":
        tr.update_entry(p, key, status=tr.DELETED, deleted_detected_at_ist=now, deleted_change_id=cid)
        p.audit("AC_DELETION_DETECTED", req_key=key, approved_version=item["approved_version"], change_id=cid,
                note="approved criteria removed from acceptance/approved/; the approved record is kept in snapshots/ac/")
        _reopen_if_completed(p, "approved-ac-deleted")
        p.sync_docs()
        p.save_state(f"ac-{ac_fname(key)}-deletion-recorded")
        eff.update(snapshot_kept=True, restore=f"ac restore {key}")
        return eff, [_item("ac_removal", key, f"Restore the criteria for {p.label(key)} (`ac restore`) or confirm their removal (`changes dismiss`)")]
    kept = []
    if ctx.get("raw") is not None:
        dst = f"snapshots/ac/{ac_fname(key)}.observed-{cid}.json"
        atomic_write_bytes(p.path(dst), ctx["raw"])
        kept.append(dst)
    eff["edit_preserved_as"] = kept
    snap, ver, observed = ctx.get("snap"), ctx.get("ver"), ctx.get("record")
    cand_id, cand_err = None, None
    material_diff = {k: v for k, v in item["diff"].items() if k not in ("approval_record", "approved_snapshot")}
    want_cand = t == "UNTRACKED_APPROVED" or bool(material_diff) or (t == "MANUAL_EDIT" and "approved_snapshot" in item["diff"])
    if want_cand:
        existing = next((x for _, x in _all_versions(p, key) if any(h.get("change_id") == cid for h in x["review_history"])), None)
        if existing:
            cand_id = existing["version"]
        else:
            src = observed
            cand_err = None if src else "the edited file cannot be read"
            if src:
                cand = _build_candidate(p, src, ver["version"] if ver else None, cid, rec["summary"])
                try:
                    p.save_ac("candidates", cand)
                    cand_id = cand["version"]
                except BAError as e:
                    cand_err = e.details.get("errors") or e.message
        if t == "UNTRACKED_APPROVED" and cand_id is None:
            raise BAError("AC_NOT_CAPTURABLE", f"The criteria for {key} sit in acceptance/approved/ without an approval on record and could not be "
                          f"moved to review: {cand_err}. Nothing was changed; fix the file or remove it by hand, then run `changes check`.")
    if snap:  # put back exactly what was approved (workflow bookkeeping such as review history is carried over)
        restored = copy.deepcopy(snap)
        if observed and observed.get("review_history", [])[:len(snap["review_history"])] == snap["review_history"]:
            restored["review_history"], restored["impact_review"] = observed["review_history"], observed.get("impact_review")
        restored["updated_at"] = now
        p.save_ac("approved", restored)
        eff["approved_version_restored"] = restored["version"]
    elif ver is None:  # never had an approval on record: it moves to review instead of staying in approved/
        p.remove_ac_files("approved", key)
    else:
        eff["warning"] = "the approved snapshot is missing or damaged; the edited file was left in place and flagged"
    if ver is not None and (cand_id is None or not snap):  # the approved record must not stay unflagged
        why = ("the approved snapshot is unavailable, so the criteria cannot be proved to be what was approved"
               + (f"; the edit is also saved as v{cand_id} for review" if cand_id else "") if not snap else
               f"the edit could not be captured as a new version ({cand_err}); it is preserved in {', '.join(kept)}" if want_cand else
               "its approval / readiness fields had been altered by hand and were restored from the approved snapshot")
        _flag_approved(p, key, cid, why)
    eff.update(new_candidate_version=cand_id, candidate_error=cand_err, approved_version=item.get("approved_version"))
    p.audit("AC_MANUAL_EDIT_DETECTED", req_key=key, change_id=cid, change_type=t, edited_via=item["edited_via"],
            approved_version=item.get("approved_version"), new_version=cand_id, fields=sorted(item["diff"]),
            preserved_as=kept, approval_still_on_record=bool(snap))
    if cand_id:
        p.audit("AC_REAPPROVAL_REQUIRED", req_key=key, version=cand_id, change_id=cid, reason="material manual edit to approved criteria")
        _reopen_if_completed(p, "ac-manual-edit")
    p.save_state(f"ac-{ac_fname(key)}-manual-edit-recorded")
    return eff, [_item("reapproval", f"{key}:v{cand_id}", f"Approve, edit or revert the criteria of {p.label(key)} v{cand_id}")] if cand_id else \
        [_item("ac", key, f"Review the criteria of {p.label(key)}: confirm they are unaffected or edit them (`ac confirm-unaffected` / `ac edit`)")]


def _apply_enriched_edit(p, rec: dict) -> tuple[dict, list]:
    """A hand edit of an approved criterion inside the enriched copy: kept verbatim, becomes the next version for review; the approved
    version stays in force and in the document."""
    sub = rec["subject"]
    key, rel, cid, now = sub["req_key"], sub["file"], rec["change_id"], ist_now_iso()
    appr_path = f"acceptance/approved/{ac_fname(key)}.json"
    if not p.exists(appr_path):
        raise BAError("CHANGE_RECORD_STALE", f"{key} has no approved criteria any more. Run `changes check` again.")
    approved = p.read_json(appr_path)
    obs = sub["observed"]
    cur = next((i for i, _ in detect_enriched(p) if i.get("req_key") == key and i["change_type"] == "MANUAL_EDIT"), None)
    if cur is not None and cur["dedupe_key"] != rec["dedupe_key"]:
        raise BAError("CHANGE_RECORD_STALE", f"The criteria of {key} in {rel} were edited again after {cid} was recorded. Run `changes check` again.")
    kept = f"snapshots/ac/{ac_fname(key)}.observed-{cid}.md"
    atomic_write_bytes(p.path(kept), (obs["block"] + "\n").encode("utf-8"))
    existing = next((x for _, x in _all_versions(p, key) if any(h.get("change_id") == cid for h in x["review_history"])), None)
    cand_id, cand_err = None, None
    if existing:
        cand_id = existing["version"]
    else:
        src = dict(approved, acceptance_criteria=[normalize_ws(c) for c in obs["criteria"]])
        cand = _build_candidate(p, src, approved["version"], cid, rec["summary"])
        try:
            p.save_ac("candidates", cand)
            cand_id = cand["version"]
        except BAError as e:
            cand_err = e.details.get("errors") or e.message
    others = [r for r in records(p, "DETECTED") if r["change_id"] != cid and r["type"] == "AC_CHANGE" and r["subject"].get("edited_via") == "enriched"
              and r["subject"].get("file") == rel]
    restored = not others
    if restored:  # the last pending edit of this copy: bring the generated content back (the edits are kept in the records above)
        docsync.refresh(p, force=True, only={rel})
    p.audit("AC_MANUAL_EDIT_DETECTED", req_key=key, change_id=cid, change_type="MANUAL_EDIT", edited_via="enriched", approved_version=approved["version"],
            new_version=cand_id, fields=sorted(rec["impact"].get("fields_changed", [])), preserved_as=[kept], approval_still_on_record=True)
    if cand_id:
        p.audit("AC_REAPPROVAL_REQUIRED", req_key=key, version=cand_id, change_id=cid, reason="material manual edit of an approved criterion in the enriched copy")
        _reopen_if_completed(p, "ac-manual-edit")
    p.save_state(f"ac-{ac_fname(key)}-manual-edit-recorded")
    eff = {"req_key": key, "change_type": "MANUAL_EDIT", "edited_via": "enriched", "edit_preserved_as": [kept], "new_candidate_version": cand_id,
           "candidate_error": cand_err, "approved_version": approved["version"], "approved_version_restored": approved["version"],
           "enriched_copy_restored": restored}
    return eff, [_item("reapproval", f"{key}:v{cand_id}", f"Approve, edit or revert the criteria of {p.label(key)} v{cand_id}")] if cand_id else \
        [_item("ac", key, f"Review the criteria of {p.label(key)}: the edit could not be captured as a new version ({cand_err}); it is kept in {kept}")]


# ============================================== BA Agent's own AC edits

def record_agent_ac_edit(p, base: dict, new: dict, reason: str, formatting_only: bool = False) -> dict:
    """Log an edit the BA Agent made to APPROVED criteria (via `ac edit`). Material edits become a new version that needs
    re-approval; the approved version stays in force. Formatting-only edits keep the approval."""
    key, d = base["req_key"], tr.diff(base, new)
    imp = _ac_impact(p, key, base, new, d)
    if formatting_only:
        return _new(p, "AC_CHANGE", FORMATTING, "BA_AGENT", f"BA Agent reformatted approved criteria for {key} v{base['version']} ({_clip(reason, 120)}); approval unchanged",
                    {"req_key": key, "change_type": "FORMATTING_ONLY", "approved_version": base["version"]},
                    [{"change": "FORMATTING_ONLY", "req_key": key}], {"req_key": key}, [], [], "RECORDED", f"agent:{key}:{new['updated_at']}", "ac edit")
    items = [_item("reapproval", f"{key}:v{new['version']}", f"Approve, edit or revert the criteria of {p.label(key)} v{new['version']}")]
    return _new(p, "AC_CHANGE", MATERIAL, "BA_AGENT",
                f"BA Agent edited approved criteria for {key} v{base['version']} → v{new['version']}: {imp['summary_of_fields']} ({_clip(reason, 120)})",
                {"req_key": key, "change_type": "AGENT_EDIT", "approved_version": base["version"], "new_version": new["version"], "reason": reason},
                [{"change": "AGENT_EDIT", "req_key": key, "fields": d}], imp, _ac_actions({"change_type": "AGENT_EDIT", "req_key": key}, imp, new["version"]),
                items, "APPLIED", f"agent:{key}:v{new['version']}", "ac edit")


# ================================================================== reconcile

def _item_states(p, rec: dict) -> dict[str, str]:
    """Pure: the current state of every OPEN item of an APPLIED record."""
    gaps = {g["gap_id"]: g for g in p.gaps()}
    acs = p.all_acs()
    out = {}
    for it in rec["items"]:
        if it["state"] != OPEN_ITEM:
            continue
        k, ref = it["kind"], it["ref"]
        if k in ("requirement", "new_requirement", "analysis"):
            done = bool(p.state.get("gap_analysis_complete")) or (ref in reqindex.by_key(p) and reqindex.by_key(p)[ref]["status"] != "ACTIVE")
        elif k == "gap":
            done = ref not in gaps or not gaps[ref].get("needs_impact_review") or gaps[ref]["status"] == "RESOLVED"
        elif k == "ac":
            mine = [a for _, a in acs if a["req_key"] == ref]
            done = not any((a.get("impact_review") or {}).get("pending") for a in mine)
        elif k == "reapproval":
            key, ver = ref.rsplit(":v", 1)
            ver = int(ver)
            pending = any(a["req_key"] == key and a["version"] == ver and b in PENDING_BUCKETS for b, a in acs)
            done = not pending
        elif k == "ac_removal":
            e = tr.entry(p, ref)
            done = bool(e) and (e["status"] == tr.REMOVAL_CONFIRMED or (e["status"] == tr.TRACKED and p.exists(f"acceptance/approved/{ac_fname(ref)}.json")))
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
    if it["kind"] == "ac_removal":
        tr.update_entry(p, it["ref"], status=tr.REMOVAL_CONFIRMED)
    _event(rec, "ITEM_DISMISSED", f"{item_id}: {reason}", by_name=by_name)
    _save(p, rec)
    p.audit("CHANGE_ITEM_DISMISSED", change_id=cid, item_id=item_id, reason=reason, by_name=by_name, by_role=by_role)
    reconcile(p)
    return {"change_id": cid, "item": item_id, "state": DISMISSED, "change_status": get(p, cid)["status"]}


def check_and_apply_source(p) -> dict:
    """Detect + record + apply a source change in one call (library use; the CLI previews first and applies on request)."""
    s = detect_source(p)
    if s.get("missing"):
        raise BAError("SOURCE_NOT_FOUND", f"The requirements folder is missing: {s['path']}.")
    if s.get("unchanged") or s.get("unreadable") or s.get("enriched_as_source"):
        return {"changed": False, "fingerprint": p.state["source"]["fingerprint"]}
    if s["classification"] == FORMATTING:
        accept_source_formatting(p, s, "direct call")
        return {"changed": False, "formatting_only": True}
    check(p, True, "direct call")
    rec = _open_by_key(p, s["dedupe_key"])
    if rec["status"] == "DETECTED":
        apply(p, rec["change_id"], _legacy=True)
        rec = get(p, rec["change_id"])
    return rec["applied"]["effects"]


# ============================================================ AC restore / revert

def restore_ac(p, ref: str, reason: str, by_name: str, by_role: str) -> dict:
    by_name, by_role = require_identity(by_name, by_role, "Person restoring the criteria")
    if not normalize_ws(reason or ""):
        raise BAError("REASON_REQUIRED", "A reason is required to restore approved criteria.")
    key = reqindex.resolve(p, ref)
    e = tr.entry(p, key)
    if not e or e["status"] not in (tr.DELETED, tr.REMOVAL_CONFIRMED) or p.exists(f"acceptance/approved/{ac_fname(key)}.json"):
        raise BAError("NOTHING_TO_RESTORE", f"{p.label(key)} has no recorded deletion to undo (run `changes check` and `changes apply` first).")
    ver = tr.current(e)
    snap = tr.snapshot_record(p, ver)
    if not snap:
        raise BAError("SNAPSHOT_UNAVAILABLE", f"The approved snapshot of {p.label(key)} v{ver['version']} is missing or damaged ({ver['snapshot']}).")
    snap["review_history"].append({"action": "RESTORED_AFTER_DELETION", "version": snap["version"], "at_ist": ist_now_iso(), "note": normalize_ws(reason),
                                   "by_name": by_name})
    snap["updated_at"] = ist_now_iso()
    p.save_ac("approved", snap)
    tr.update_entry(p, key, status=tr.TRACKED)
    p.audit("AC_RESTORED", req_key=key, version=snap["version"], reason=reason, by_name=by_name, by_role=by_role)
    p.save_state(f"ac-{ac_fname(key)}-restored")
    reconcile(p)
    return {"req_key": key, "version": snap["version"], "status": "APPROVED", "approval_restored_from": ver["snapshot"]}


def revert_edit(p, ref: str, reason: str, by_name: str, by_role: str) -> dict:
    """Discard an edited version (manual or agent) that has not been approved; the approved version stays in force."""
    by_name, by_role = require_identity(by_name, by_role, "Person reverting the edit")
    if not normalize_ws(reason or ""):
        raise BAError("REASON_REQUIRED", "A reason is required to discard an edited version.")
    key = reqindex.resolve(p, ref)
    name = ac_fname(key)
    if not p.exists(f"acceptance/approved/{name}.json"):
        raise BAError("NOTHING_TO_REVERT", f"{p.label(key)} has no approved version to fall back to.")
    appr = p.read_json(f"acceptance/approved/{name}.json")
    for b in PENDING_BUCKETS:
        if p.exists(f"acceptance/{b}/{name}.json"):
            a = p.read_json(f"acceptance/{b}/{name}.json")
            if a.get("supersedes_version") == appr["version"] or a["version"] > appr["version"]:
                a["review_history"].append({"action": "EDIT_REVERTED", "version": a["version"], "at_ist": ist_now_iso(),
                                            "note": normalize_ws(reason), "by_name": by_name})
                p.archive_ac(a, status="SUPERSEDED")
                p.remove_ac_files(b, key)
                p.audit("AC_EDIT_REVERTED", req_key=key, discarded_version=a["version"], approved_version=appr["version"],
                        reason=reason, by_name=by_name, by_role=by_role)
                p.save_state(f"ac-{name}-edit-reverted")
                reconcile(p)
                return {"req_key": key, "discarded_version": a["version"], "approved_version_in_force": appr["version"],
                        "discarded_copy": physical(f"acceptance/archive/{name}.v{a['version']}.json")}
    raise BAError("NOTHING_TO_REVERT", f"{p.label(key)} has no unapproved edited version.")


def ac_history(p, ref: str) -> dict:
    key = reqindex.resolve(p, ref)
    name = ac_fname(key)
    e = tr.entry(p, key)
    archived = [{"version": a["version"], "status": a["status"], "file": physical(f"acceptance/archive/{name}.v{a['version']}.json")}
                for a in p.archived_acs() if a["req_key"] == key]
    pending = [{"bucket": b, "version": a["version"], "status": a["status"], "supersedes_version": a.get("supersedes_version"),
                "impact_review": a.get("impact_review")} for b, a in p.all_acs() if a["req_key"] == key and b != "approved"]
    if not e and not archived and not pending and not p.exists(f"acceptance/approved/{name}.json"):
        raise BAError("UNKNOWN_AC", f"There are no acceptance criteria for {p.label(key)}.")
    cur = p.read_json(f"acceptance/approved/{name}.json") if p.exists(f"acceptance/approved/{name}.json") else None
    return {"req_key": key, "tracking_status": e["status"] if e else "UNTRACKED",
            "approved_versions": [{"version": v["version"], "event": v["event"], "approved_at_ist": v["approved_at_ist"],
                                   "approver": f"{v['approver_name']} — {v['approver_role']}" if v.get("approver_name") else None,
                                   "approval_statement": v.get("statement"), "material_hash": v["material_hash"], "snapshot": v["snapshot"],
                                   "in_force": bool(cur) and cur["version"] == v["version"]} for v in (e["versions"] if e else [])],
            "superseded_or_discarded": archived, "awaiting_review": pending,
            "changes": [_row(r) for r in records(p) if r["type"] == "AC_CHANGE" and r["subject"].get("req_key") == key]}


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
    if "classification" in s and s["dedupe_key"] not in keys:
        undetected_src = s["classification"]
    undetected_ac = sorted({i["req_key"] for i, _ in det["acs"] if i["dedupe_key"] not in keys})
    undetected_enr = sorted({i["file"] for i, _ in det["enriched"] if i["dedupe_key"] not in keys})
    waiting = []
    for r in recs:
        if r["status"] == "APPLIED":
            st = _item_states(p, r)
            opn = [i["item_id"] for i in r["items"] if i["state"] == OPEN_ITEM and st.get(i["item_id"]) != DONE]
            if opn:
                waiting.append({"change_id": r["change_id"], "summary": r["summary"], "open_items": opn})
    return {"unreviewed": [{"change_id": r["change_id"], "type": r["type"], "summary": r["summary"]} for r in recs if r["status"] == "DETECTED"],
            "awaiting_reconciliation": waiting,
            "undetected": {"source": undetected_src, "acs": undetected_ac, "enriched": undetected_enr, "source_missing": bool(s.get("missing"))},
            "stale": {"acs_flagged": sorted({a["req_key"] for _, a in p.all_acs() if (a.get("impact_review") or {}).get("pending")}),
                      "gaps_flagged": sorted(g["gap_id"] for g in p.gaps() if g.get("needs_impact_review"))},
            "total_records": len(recs)}


def status(p) -> dict:
    return {**summary(p), "records": [_row(r) for r in records(p)]}
