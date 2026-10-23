"""Atomic requirements and source-change (impact) handling."""
from __future__ import annotations

import os
import re
from pathlib import Path

from . import conflicts, ingest, schema
from .util import BAError, ist_now_iso, normalize_ws, require_identity

TYPES = ["FUNCTIONAL", "BUSINESS_RULE", "DATA", "VALIDATION", "INTEGRATION", "SECURITY", "NON_FUNCTIONAL",
         "WORKFLOW", "CONSTRAINT", "OTHER"]
LIST_FIELDS = ["actors", "preconditions", "outcomes", "constraints", "dependencies", "acceptance_info", "assumptions"]
EDITABLE = ["title", "statement", "type", "source_excerpt", "source_section_ids", "flags",
            "related_requirement_ids"] + LIST_FIELDS


def _sections_in_force(p) -> list[dict]:
    """Sections used to validate references: the new ones while a source change is pending."""
    if p.exists("state/pending/source-change.json"):
        return p.read_json("state/pending/source-change.json")["sections"]
    return p.source_metadata()["sections"]


def _source_text_for_validation(p) -> str:
    pending = p.state["impact_review"].get("pending")
    return p.load_source(verify=not pending)["text"]


def _section_bundle(p, sids: list[str]) -> tuple[list[dict], str]:
    secs = {s["section_id"]: s for s in _sections_in_force(p)}
    missing = [s for s in sids if s not in secs]
    if missing:
        raise BAError("UNKNOWN_SECTION", f"Unknown source section(s): {missing}. See extracted/brd-outline.md.")
    text = _source_text_for_validation(p)
    chosen = [secs[s] for s in sids]
    return chosen, "\n".join(ingest.section_text(text, s) for s in chosen)


def _reference(p, chosen: list[dict]) -> str:
    name = Path(p.state["source"]["path"]).name
    return "; ".join(f"{name} §{s['section_id']} {s['title']} (lines {s['line_start']}–{s['line_end']})" for s in chosen)


def _clean_list(v, field) -> list[str]:
    if v is None:
        return []
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise BAError("BAD_INPUT", f"'{field}' must be a list of strings.")
    return [normalize_ws(x) for x in v if normalize_ws(x)]


def add_requirements(p, items: list[dict]) -> dict:
    if p.state["stage"] in ("INITIALIZED", "PAUSED", "BLOCKED"):
        raise BAError("INVALID_STAGE", f"Cannot add requirements while stage is {p.state['stage']}.")
    if not isinstance(items, list) or not items:
        raise BAError("BAD_INPUT", "Provide a non-empty JSON list of requirements.")
    existing = [r for r in p.requirements()]
    ids = [r["requirement_id"] for r in existing]
    errors, built = [], []
    now = ist_now_iso()
    for n, it in enumerate(items):
        tag = f"item[{n}]"
        try:
            for f in ("title", "statement", "type", "source_section_ids", "source_excerpt"):
                if not it.get(f):
                    raise BAError("BAD_INPUT", f"{tag}: '{f}' is required.")
            if it["type"] not in TYPES:
                raise BAError("BAD_INPUT", f"{tag}: type must be one of {TYPES}.")
            if not isinstance(it["source_section_ids"], list):
                raise BAError("BAD_INPUT", f"{tag}: source_section_ids must be a list.")
            chosen, text = _section_bundle(p, it["source_section_ids"])
            if not ingest.excerpt_in_text(it["source_excerpt"], text):
                raise BAError("EXCERPT_NOT_FOUND", f"{tag}: source_excerpt was not found in {it['source_section_ids']}. "
                              "Quote the BRD text verbatim; do not paraphrase.")
            rid = p.next_id("REQ", ids)
            ids.append(rid)
            rec = {
                "requirement_id": rid, "title": normalize_ws(it["title"]), "statement": normalize_ws(it["statement"]),
                "type": it["type"], "source_reference": _reference(p, chosen),
                "source_section_ids": list(it["source_section_ids"]), "source_section_keys": [s["key"] for s in chosen],
                "source_excerpt": normalize_ws(it["source_excerpt"]),
                "source_fingerprint": p.state["source"]["fingerprint"], "status": "ACTIVE", "version": 1,
                "flags": list(it.get("flags") or []),
                "related_requirement_ids": list(it.get("related_requirement_ids") or []),
                "related_gap_ids": [], "related_decision_ids": [], "versions": [],
                "created_at": now, "updated_at": now,
            }
            for f in LIST_FIELDS:
                rec[f] = _clean_list(it.get(f), f)
            if len(re.findall(r"\b(shall|must|should|will)\b", rec["statement"], re.I)) >= 3:
                rec["flags"].append({"kind": "AMBIGUITY", "note": "Statement contains 3+ obligations; consider splitting for atomicity."})
            built.append(rec)
        except BAError as e:
            errors.append(e.message)
    if errors:
        raise BAError("REQUIREMENTS_REJECTED", "No requirements were saved: " + " | ".join(errors), errors=errors)

    # advisory duplicate / conflict flags (against saved + same batch)
    pool = [(r["requirement_id"], r["statement"]) for r in existing if r["status"] != "REMOVED"]
    notes = []
    for rec in built:
        for oid, ostmt in pool:
            k = conflicts.classify(rec["statement"], ostmt)
            if not k:
                continue
            if k[0] == "duplicate":
                rec["flags"].append({"kind": "POSSIBLE_DUPLICATE", "note": f"Very similar to {oid} (similarity {k[1]:.2f}).", "related_ids": [oid]})
                notes.append({"requirement_id": rec["requirement_id"], "duplicate_of": oid})
            else:
                rec["flags"].append({"kind": "POSSIBLE_CONFLICT", "note": f"May conflict with {oid}: {'; '.join(k[1])}.", "related_ids": [oid]})
                notes.append({"requirement_id": rec["requirement_id"], "possible_conflict_with": oid, "reasons": k[1]})
            if oid not in rec["related_requirement_ids"]:
                rec["related_requirement_ids"].append(oid)
        pool.append((rec["requirement_id"], rec["statement"]))
    for rec in built:
        p.save_requirement(rec)
    p.audit("REQUIREMENTS_CREATED", requirement_ids=[r["requirement_id"] for r in built], count=len(built))
    if p.state["stage"] not in ("BRD_PARSED", "REQUIREMENTS_READY"):
        _after_requirement_change(p, {r["requirement_id"] for r in built}, "new requirements added", "requirement-added")
    else:
        p.save_state("requirements-saved")
    return {"created": [r["requirement_id"] for r in built], "advisory": notes}


def _after_requirement_change(p, rids: set[str], reason: str, cause: str) -> dict:
    """Propagate a requirement change: stories → impact review, gaps flagged, baseline stale, analysis reopened."""
    flagged_stories = p.flag_stories(rids, reason, cause) if p.all_stories() else []
    flagged_gaps = []
    gaps = p.gaps()
    for g in gaps:
        if set(g["impacted_requirement_ids"]) & rids and not g.get("needs_impact_review"):
            g["needs_impact_review"] = True
            g["updated_at"] = ist_now_iso()
            flagged_gaps.append(g["gap_id"])
    if flagged_gaps:
        p.save_gaps(gaps, touched=flagged_gaps)
        p.audit("GAP_IMPACT_FLAGGED", gap_ids=flagged_gaps, cause=cause)
    p.mark_baseline_stale(reason)
    p.state["gap_analysis_complete"] = False
    if p.state["stage"] in ("COMPLETED", "WAITING_FOR_STORY_REVIEW", "STORIES_READY", "REQUIREMENTS_BASELINED"):
        p.reopen_to("GAP_ANALYSIS", cause)
    p.save_state(f"impact-{cause}")
    return {"flagged_stories": flagged_stories, "flagged_gaps": flagged_gaps}


def apply_change(p, r: dict, changes: dict, reason: str, cause: str, decision_id: str | None = None,
                 by_name: str | None = None, by_role: str | None = None) -> dict:
    """Pure-ish: return a NEW version of requirement `r` (prior version preserved). Does not save."""
    import copy
    r = copy.deepcopy(r)
    bad = [k for k in changes if k not in EDITABLE]
    if bad:
        raise BAError("BAD_INPUT", f"Fields not editable: {bad}. Editable: {EDITABLE}")
    if not changes:
        raise BAError("BAD_INPUT", "No changes supplied.")
    now = ist_now_iso()
    r["versions"].append({"version": r["version"], "title": r["title"], "statement": r["statement"], "changed_at": now,
                          "reason": reason, "cause": cause, "decision_id": decision_id, "by_name": by_name, "by_role": by_role,
                          "source_excerpt": r.get("source_excerpt")})
    for k, v in changes.items():
        r[k] = _clean_list(v, k) if k in LIST_FIELDS else v
    if "source_section_ids" in changes or "source_excerpt" in changes:
        chosen, text = _section_bundle(p, r["source_section_ids"])
        if r.get("source_excerpt") and not ingest.excerpt_in_text(r["source_excerpt"], text):
            raise BAError("EXCERPT_NOT_FOUND", "source_excerpt was not found in the referenced section(s).")
        r["source_reference"], r["source_section_keys"] = _reference(p, chosen), [s["key"] for s in chosen]
    if cause == "source-change":
        r["source_fingerprint"] = p.state["impact_review"]["new_fingerprint"]
    r["version"] += 1
    r["status"] = "ACTIVE"
    r["flags"] = [f for f in r["flags"] if f["kind"] != "IMPACT_REVIEW"]
    if decision_id and decision_id not in r["related_decision_ids"]:
        r["related_decision_ids"].append(decision_id)
    r["updated_at"] = now
    return r


def update_requirement(p, rid: str, changes: dict, reason: str, cause: str, decision_id: str | None = None,
                       by_name: str | None = None, by_role: str | None = None) -> dict:
    """Create a NEW version of a requirement. Prior versions are preserved in `versions`."""
    r = p.requirement(rid)
    if cause not in ("decision", "source-change", "correction"):
        raise BAError("BAD_INPUT", "cause must be decision | source-change | correction")
    if not normalize_ws(reason):
        raise BAError("BAD_INPUT", "A reason is required for every requirement change.")
    if cause == "decision":
        d = p.decisions().get(decision_id or "")
        if not d or d["current_status"] != "ACTIVE":
            raise BAError("UNKNOWN_DECISION", "A requirement change from a decision needs an existing ACTIVE --decision-id.")
    elif cause == "correction":
        by_name, by_role = require_identity(by_name, by_role, "Correction authoriser")
    elif cause == "source-change" and not p.state["impact_review"].get("pending"):
        raise BAError("NO_PENDING_SOURCE_CHANGE", "cause=source-change requires a pending source change (`source check`).")
    r = apply_change(p, r, changes, reason, cause, decision_id, by_name, by_role)
    p.save_requirement(r)
    p.audit("REQUIREMENT_UPDATED", requirement_id=rid, new_version=r["version"], cause=cause, decision_id=decision_id, reason=reason)
    impact = _after_requirement_change(p, {rid}, f"{rid} changed to v{r['version']}: {reason}", f"requirement-{cause}")
    return {"requirement_id": rid, "version": r["version"], **impact}


def confirm_requirement(p, rid: str, note: str) -> dict:
    """After a source change: confirm the requirement is still valid as written."""
    r = p.requirement(rid)
    if r["status"] != "NEEDS_REVIEW":
        raise BAError("NOT_PENDING_REVIEW", f"{rid} is {r['status']}, not NEEDS_REVIEW.")
    if not normalize_ws(note):
        raise BAError("BAD_INPUT", "A note explaining why it is unaffected is required.")
    r["status"] = "ACTIVE"
    r["flags"] = [f for f in r["flags"] if f["kind"] != "IMPACT_REVIEW"]
    r["updated_at"] = ist_now_iso()
    p.save_requirement(r)
    p.audit("REQUIREMENT_CONFIRMED_UNAFFECTED", requirement_id=rid, note=note)
    return {"requirement_id": rid, "status": "ACTIVE"}


def remove_requirement(p, rid: str, reason: str, by_name: str, by_role: str) -> dict:
    by_name, by_role = require_identity(by_name, by_role, "Removal authoriser")
    r = p.requirement(rid)
    now = ist_now_iso()
    r["versions"].append({"version": r["version"], "title": r["title"], "statement": r["statement"], "changed_at": now,
                          "reason": f"REMOVED: {reason}", "cause": "removal", "by_name": by_name, "by_role": by_role})
    r["status"], r["updated_at"] = "REMOVED", now
    p.save_requirement(r)
    p.audit("REQUIREMENT_REMOVED", requirement_id=rid, reason=reason, by_name=by_name, by_role=by_role)
    return {"requirement_id": rid, **_after_requirement_change(p, {rid}, f"{rid} removed: {reason}", "requirement-removed")}


def commit_requirements(p) -> dict:
    reqs = p.requirements()
    active = [r for r in reqs if r["status"] in ("ACTIVE", "NEEDS_REVIEW")]
    if not active:
        raise BAError("NO_REQUIREMENTS", "Save at least one requirement before committing the decomposition.")
    secs = {s["section_id"] for s in _sections_in_force(p)}
    problems = []
    for r in reqs:
        for e in schema.validate(r, "requirement"):
            problems.append(f"{r['requirement_id']}: {e}")
        for s in r["source_section_ids"]:
            if s not in secs:
                problems.append(f"{r['requirement_id']}: unknown source section {s}")
    if problems:
        raise BAError("REQUIREMENTS_INVALID", "Decomposition failed validation.", errors=problems)
    if p.state["stage"] == "BRD_PARSED":
        p.audit("REQUIREMENTS_COMMITTED", count=len(active))
        p.set_stage("REQUIREMENTS_READY", "atomic-requirements-saved")
    return {"requirements": len(active), "stage": p.state["stage"]}


def pairs_report(p) -> dict:
    reqs = [r for r in p.requirements() if r["status"] in ("ACTIVE", "NEEDS_REVIEW")]
    stm = [(r["requirement_id"], r["statement"]) for r in reqs]
    dups = [{"a": a, "b": b, "similarity": s} for a, b, s in conflicts.duplicate_pairs(stm)]
    cons = []
    for i in range(len(stm)):
        for j in range(i + 1, len(stm)):
            why = conflicts.conflict_reasons(stm[i][1], stm[j][1])
            if why:
                cons.append({"a": stm[i][0], "b": stm[j][0], "reasons": why})
    return {"possible_duplicates": dups, "possible_conflicts": cons,
            "note": "Advisory candidates only. Confirm real contradictions as a 'Contradiction, duplication, or dependency' gap."}


# ------------------------------------------------------------- source changes

def source_section(p, section_id: str, allow_changed: bool = False) -> dict:
    meta = p.load_source(verify=not allow_changed)
    secs = {s["section_id"]: s for s in (_sections_in_force(p) if allow_changed else p.source_metadata()["sections"])}
    if section_id not in secs:
        raise BAError("UNKNOWN_SECTION", f"Section {section_id} not found.")
    s = secs[section_id]
    return {"section_id": section_id, "title": s["title"], "lines": [s["line_start"], s["line_end"]],
            "text": ingest.section_text(meta["text"], s)}


def source_check(p) -> dict:
    src = p.state["source"]
    path = Path(src["path"])
    if not path.exists():
        raise BAError("SOURCE_NOT_FOUND", f"The BRD file is missing: {path}. Restore it or point the project at the new location.")
    meta = ingest.extract(path)
    if meta["fingerprint"] == src["fingerprint"]:
        return {"changed": False, "fingerprint": src["fingerprint"]}
    old = p.source_metadata()
    if meta["text_fingerprint"] == old["text_fingerprint"]:
        p.audit("SOURCE_FINGERPRINT_CHANGED", previous=src["fingerprint"], new=meta["fingerprint"], content_changed=False)
        src["fingerprint"] = meta["fingerprint"]
        p.write_source_metadata(meta)
        p.save_state("source-fingerprint-refreshed")
        return {"changed": False, "cosmetic_change": True,
                "note": "File bytes changed but extracted text is identical; fingerprint refreshed, no impact."}
    diff = ingest.diff_sections(old["sections"], meta["sections"])
    touched_keys = set(diff["changed"]) | set(diff["removed"])
    affected, intact = [], {}
    new_secs = {s["key"]: s for s in meta["sections"]}
    for r in p.requirements():
        if r["status"] in ("ACTIVE", "NEEDS_REVIEW") and touched_keys & set(r.get("source_section_keys", [])):
            body = "\n".join(ingest.section_text(meta["text"], new_secs[k]) for k in r["source_section_keys"] if k in new_secs)
            intact[r["requirement_id"]] = bool(r.get("source_excerpt")) and ingest.excerpt_in_text(r["source_excerpt"], body)
            r["status"] = "NEEDS_REVIEW"
            if not any(f["kind"] == "IMPACT_REVIEW" for f in r["flags"]):
                r["flags"].append({"kind": "IMPACT_REVIEW", "note": "Source section changed or removed; reassess.",
                                   "related_ids": sorted(touched_keys & set(r["source_section_keys"]))})
            r["updated_at"] = ist_now_iso()
            p.save_requirement(r)
            affected.append(r["requirement_id"])
    p.write_json("state/pending/source-change.json", {"sections": meta["sections"], "fingerprint": meta["fingerprint"],
                 "text_fingerprint": meta["text_fingerprint"], "format": meta["format"], "parser": meta["parser"],
                 "injection_suspects": meta["injection_suspects"], "warnings": meta["warnings"]})
    p.state["impact_review"] = {"pending": True, "detected_at_ist": ist_now_iso(), "previous_fingerprint": src["fingerprint"],
                                "new_fingerprint": meta["fingerprint"], "diff": diff, "affected_requirement_ids": affected,
                                "added_section_keys": diff["added"]}
    p.audit("SOURCE_CHANGED", previous=src["fingerprint"], new=meta["fingerprint"], diff={k: len(v) for k, v in diff.items()},
            affected_requirement_ids=affected)
    impact = _after_requirement_change(p, set(affected), "BRD content changed", "source-change") if affected else {}
    if not affected:
        p.mark_baseline_stale("BRD changed (only new sections)")
        p.state["gap_analysis_complete"] = False
        p.save_state("source-change-detected")
    return {"changed": True, "diff": diff, "affected_requirement_ids": affected,
            "source_excerpt_still_present": intact, **impact,
            "next": "Reassess each affected requirement (req update --cause source-change | req confirm | req remove), "
                    "add requirements for new sections, then run `source accept`."}


def source_accept(p, by_name: str, by_role: str, statement: str) -> dict:
    ir = p.state["impact_review"]
    if not ir.get("pending"):
        raise BAError("NO_PENDING_SOURCE_CHANGE", "There is no pending source change.")
    by_name, by_role = require_identity(by_name, by_role, "Source-change acceptor")
    if not normalize_ws(statement):
        raise BAError("BAD_INPUT", "A confirmation statement is required.")
    open_reqs = [r["requirement_id"] for r in p.requirements() if r["status"] == "NEEDS_REVIEW"]
    if open_reqs:
        raise BAError("IMPACT_REVIEW_INCOMPLETE", f"Requirements still need review: {open_reqs}", requirement_ids=open_reqs)
    new = p.read_json("state/pending/source-change.json")
    by_key = {s["key"]: s["section_id"] for s in new["sections"]}
    for r in p.requirements():
        if r["status"] != "ACTIVE":
            continue
        ids = [by_key.get(k) for k in r.get("source_section_keys", [])]
        if None in ids:
            raise BAError("IMPACT_REVIEW_INCOMPLETE", f"{r['requirement_id']} still points at a removed section; update or remove it.")
        if ids and ids != r["source_section_ids"]:
            chosen = [s for s in new["sections"] if s["section_id"] in ids]
            r["source_section_ids"], r["source_reference"] = ids, _reference(p, chosen)
            r["source_fingerprint"] = new["fingerprint"]
            r["updated_at"] = ist_now_iso()
            p.save_requirement(r)
        elif r["source_fingerprint"] != new["fingerprint"]:
            r["source_fingerprint"] = new["fingerprint"]
            p.save_requirement(r)
    p.state["source"]["fingerprint"], p.state["source"]["text_fingerprint"] = new["fingerprint"], new["text_fingerprint"]
    p.write_source_metadata({**new, "format": new["format"]})
    p.state["impact_review"] = {"pending": False, "reasons": [], "last_accepted_at_ist": ist_now_iso()}
    pend = p.path("state/pending/source-change.json")
    pend.unlink()
    p.audit("SOURCE_CHANGE_ACCEPTED", by_name=by_name, by_role=by_role, statement=statement, new_fingerprint=new["fingerprint"])
    p.save_state("source-change-accepted")
    return {"accepted": True, "fingerprint": new["fingerprint"],
            "next": "Add requirements for new sections, re-run gap analysis (`gaps commit`), then create a new baseline. "
                    "Flagged stories need `story edit` or `story confirm-unaffected`."}
