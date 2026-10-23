"""User stories: generation, review, approval, edit/versioning, defer/revisit."""
from __future__ import annotations

import copy
import re

from . import conflicts, gaps as gapmod
from .project import STORY_TRANSITIONS, check_transition, fault
from .util import BAError, ist_now_iso, normalize_ws, require_identity

GWT = re.compile(r"\bgiven\b.+\bwhen\b.+\bthen\b", re.I | re.S)
EDITABLE = ["title", "user_story", "acceptance_criteria", "requirement_ids", "dependencies", "assumptions", "notes"]
GENERATE_STAGES = ("REQUIREMENTS_BASELINED", "STORIES_READY", "WAITING_FOR_STORY_REVIEW", "COMPLETED")


def _ready_map(p) -> dict:
    b = p.approved_baseline()
    if not b:
        raise BAError("NO_APPROVED_BASELINE", "Stories are generated only from a human-approved baseline (`baseline confirm`).")
    if p.baseline_manifest()["stale"]:
        raise BAError("BASELINE_STALE", "The baseline is stale (inputs changed). Create and confirm a new baseline first.")
    return {e["requirement_id"]: e for e in b["requirements"]}


def _check_content(p, c: dict, ready: dict, skip_dup_of: str | None = None, tag: str = "story") -> None:
    for f in ("title", "user_story", "acceptance_criteria", "requirement_ids"):
        if not c.get(f):
            raise BAError("BAD_INPUT", f"{tag}: '{f}' is required.")
    us = c["user_story"]
    if not isinstance(us, dict) or any(not normalize_ws(us.get(k, "")) for k in ("role", "capability", "value")):
        raise BAError("BAD_INPUT", f"{tag}: user_story needs role, capability and value (flag unknown roles; don't invent them).")
    if not isinstance(c["acceptance_criteria"], list) or not all(isinstance(a, str) for a in c["acceptance_criteria"]):
        raise BAError("BAD_INPUT", f"{tag}: acceptance_criteria must be a list of strings.")
    bad = [a for a in c["acceptance_criteria"] if not GWT.search(a)]
    if bad:
        raise BAError("ACCEPTANCE_FORMAT", f"{tag}: every criterion must read 'Given … when … then …'. Offending: {bad[:2]}")
    unknown = [r for r in c["requirement_ids"] if r not in ready]
    if unknown:
        raise BAError("UNLINKED_REQUIREMENT", f"{tag}: requirement(s) {unknown} are not in the approved baseline.")
    blocked = {r: ready[r]["blocked_by_gap_ids"] for r in c["requirement_ids"] if ready[r]["baseline_status"] != "READY"}
    if blocked:
        raise BAError("REQUIREMENT_BLOCKED", f"{tag}: cannot create a story for blocked requirement(s); unresolved gaps: {blocked}",
                      blocked=blocked)
    title_n = normalize_ws(c["title"]).lower()
    for bucket, s in p.all_stories():
        if s["story_id"] == skip_dup_of:
            continue
        same_reqs = set(s["requirement_ids"]) == set(c["requirement_ids"])
        sim = conflicts.jaccard(conflicts.tokens(s["title"] + " " + " ".join(s["acceptance_criteria"])),
                                conflicts.tokens(c["title"] + " " + " ".join(c["acceptance_criteria"])))
        if normalize_ws(s["title"]).lower() == title_n or (same_reqs and sim >= 0.85):
            raise BAError("DUPLICATE_STORY", f"{tag}: duplicates {s['story_id']} ('{s['title']}'). Edit that story instead.")


def _derived(p, requirement_ids: list[str]) -> dict:
    rmap = {r["requirement_id"]: r for r in p.requirements()}
    decs = [d["decision_id"] for d in p.decisions().values()
            if d["current_status"] == "ACTIVE" and set(d["related_artifact_ids"]) & set(requirement_ids)]
    open_gaps = sorted(g["gap_id"] for g in p.gaps() if g["status"] != "RESOLVED" and set(g["impacted_requirement_ids"]) & set(requirement_ids))
    return {"decision_ids": sorted(decs), "open_gap_ids": open_gaps,
            "source_references": list(dict.fromkeys(rmap[r]["source_reference"] for r in requirement_ids)),
            "requirement_versions": {r: rmap[r]["version"] for r in requirement_ids}}


def add_stories(p, items: list[dict]) -> dict:
    st = p.state
    if st["stage"] not in GENERATE_STAGES:
        raise BAError("INVALID_STAGE", f"Stories cannot be added from stage {st['stage']}.")
    if st["impact_review"].get("pending"):
        raise BAError("IMPACT_REVIEW_PENDING", "Resolve the pending BRD change first.")
    ready = _ready_map(p)
    if not isinstance(items, list) or not items:
        raise BAError("BAD_INPUT", "Provide a non-empty JSON list of stories.")
    existing_ids = [s["story_id"] for _, s in p.all_stories()] + [s["story_id"] for s in p.archived_stories()]
    built, errors = [], []
    now = ist_now_iso()
    batch_titles = []
    for n, it in enumerate(items):
        tag = f"item[{n}]"
        try:
            if normalize_ws(it.get("title", "")).lower() in batch_titles:
                raise BAError("DUPLICATE_STORY", f"{tag}: duplicate title inside the batch.")
            _check_content(p, it, ready, tag=tag)
            batch_titles.append(normalize_ws(it["title"]).lower())
            sid = p.next_id("US", existing_ids)
            existing_ids.append(sid)
            d = _derived(p, it["requirement_ids"])
            unresolved_ok = d["open_gap_ids"]
            built.append({
                "story_id": sid, "version": 1, "title": normalize_ws(it["title"]), "status": "CANDIDATE",
                "user_story": {k: normalize_ws(it["user_story"][k]) for k in ("role", "capability", "value")},
                "acceptance_criteria": [normalize_ws(a) for a in it["acceptance_criteria"]],
                "requirement_ids": list(dict.fromkeys(it["requirement_ids"])), "requirement_versions": d["requirement_versions"],
                "baseline_version": st["baseline_version"], "source_references": d["source_references"],
                "decision_ids": d["decision_ids"], "open_gap_ids": unresolved_ok,
                "dependencies": list(it.get("dependencies") or []), "assumptions": list(it.get("assumptions") or []),
                "notes": it.get("notes") or {}, "impact_review": None, "approval": None, "deferral": None,
                "supersedes_version": None,
                "review_history": [{"action": "GENERATED", "version": 1, "at_ist": now}], "created_at": now, "updated_at": now})
        except BAError as e:
            errors.append(e.message)
    if errors:
        raise BAError("STORIES_REJECTED", "No stories were saved: " + " | ".join(errors), errors=errors)
    for s in built:
        p.save_story("candidates", s)
    p.audit("STORY_GENERATED", story_ids=[s["story_id"] for s in built], baseline_version=st["baseline_version"])
    if st["stage"] in ("REQUIREMENTS_BASELINED", "COMPLETED"):
        p.set_stage("STORIES_READY", "stories-generated")
    else:
        p.save_state("stories-generated")
    return {"created": [s["story_id"] for s in built],
            "with_open_gaps": [s["story_id"] for s in built if s["open_gap_ids"]]}


def _load(p, sid: str, buckets=("candidates", "unapproved", "deferred", "approved")) -> tuple[str, dict]:
    for b in buckets:
        if p.exists(f"stories/{b}/{sid}.json"):
            return b, p.read_json(f"stories/{b}/{sid}.json")
    raise BAError("UNKNOWN_STORY", f"{sid} not found in {list(buckets)}.")


def _hist(s, action, version, note=None, **extra):
    s["review_history"].append({"action": action, "version": version, "at_ist": ist_now_iso(), "note": note, **extra})
    s["updated_at"] = ist_now_iso()


def next_story(p, story_id: str | None = None, deferred: bool = False, batch: int = 1, unapproved: bool = False) -> dict:
    buckets = ("deferred",) if deferred else ("unapproved",) if unapproved else ("candidates",)
    pool = [s for b, s in p.all_stories() if b in buckets]
    if story_id:
        _, one = _load(p, story_id, buckets + ("approved",))
        pool = [one]
    elif unapproved and not pool:
        return {"stories": [], "remaining": 0, "note": "No unapproved stories."}
    order = {"IN_REVIEW": 0, "CANDIDATE": 1, "CHANGES_REQUESTED": 2, "DEFERRED": 3}
    pool.sort(key=lambda s: (order.get(s["status"], 9), s["story_id"]))
    chosen = pool[:max(1, batch)]
    if not chosen:
        return {"stories": [], "remaining": 0, "note": "No stories awaiting review."}
    for s in chosen:
        if s["status"] == "CANDIDATE":
            check_transition(STORY_TRANSITIONS, "Story", "CANDIDATE", "IN_REVIEW")
            s["status"] = "IN_REVIEW"
            _hist(s, "PRESENTED", s["version"])
            p.save_story("candidates", s)
    if p.state["stage"] == "STORIES_READY":
        p.set_stage("WAITING_FOR_STORY_REVIEW", "story-review-started")
    live = {r["requirement_id"]: r for r in p.requirements()}
    out = []
    for s in chosen:
        d = dict(s)
        d["live_open_gap_ids"] = _derived(p, s["requirement_ids"])["open_gap_ids"]
        d["requirement_statements"] = {r: live[r]["statement"] for r in s["requirement_ids"] if r in live}
        out.append(d)
    return {"stories": out, "remaining": len(pool) - len(chosen),
            "options": ["approve", "suggest changes (free text)", "not approved", "defer"] + (["continue deferring"] if deferred else [])}


def request_changes(p, sid: str, version: int, note: str) -> dict:
    b, s = _load(p, sid, ("candidates",))
    if s["version"] != version:
        raise BAError("VERSION_MISMATCH", f"{sid} is at v{s['version']}.")
    check_transition(STORY_TRANSITIONS, "Story", s["status"], "CHANGES_REQUESTED")
    s["status"] = "CHANGES_REQUESTED"
    _hist(s, "CHANGES_REQUESTED", version, note)
    p.save_story("unapproved", s)
    p.remove_story_files("candidates", sid)
    p.audit("STORY_CHANGES_REQUESTED", story_id=sid, version=version, note=note)
    p.save_state(f"story-{sid}-unapproved")
    return {"story_id": sid, "status": s["status"], "location": f"stories/unapproved/{sid}.md"}


def approve(p, sid: str, version: int, statement: str, approver_name: str | None = None, approver_role: str | None = None) -> dict:
    now = ist_now_iso()
    bucket, s = _load(p, sid, ("candidates", "unapproved", "deferred"))
    if bucket == "deferred":
        raise BAError("STORY_DEFERRED", f"{sid} is deferred. Revisit it first (`story revisit`) so it is reviewed again.")
    if bucket == "unapproved":
        raise BAError("STORY_UNAPPROVED", f"{sid} was marked not approved. Apply the requested changes with `story edit` "
                      "(new version) and approve that version.")
    if s["version"] != version:
        raise BAError("VERSION_MISMATCH", f"Approval must name the exact version under review; {sid} is at v{s['version']}.")
    check_transition(STORY_TRANSITIONS, "Story", s["status"], "APPROVED")
    if not normalize_ws(statement or ""):
        raise BAError("APPROVAL_EVIDENCE_REQUIRED", "Record the human's explicit approval wording (--statement). "
                      "Silence, timeouts and quality scores never approve a story.")
    if approver_name or approver_role:
        approver_name, approver_role = require_identity(approver_name, approver_role, "Approver")
    if (s.get("impact_review") or {}).get("pending"):
        raise BAError("IMPACT_REVIEW_PENDING", f"{sid} is flagged for impact review: {s['impact_review']['reason']}. "
                      "Edit it (new version) or confirm it is unaffected.")
    if p.state["impact_review"].get("pending"):
        raise BAError("IMPACT_REVIEW_PENDING", "A BRD change is awaiting impact review.")
    ready = _ready_map(p)
    _check_content(p, s, ready, skip_dup_of=sid, tag=sid)
    live = _derived(p, s["requirement_ids"])
    if any(live["requirement_versions"][r] != s["requirement_versions"].get(r) for r in s["requirement_ids"]):
        raise BAError("REQUIREMENT_CHANGED", f"{sid}: a linked requirement changed since the story was written. Edit the story.")
    if live["open_gap_ids"]:
        pol = p.state["policy"]
        gmap = {g["gap_id"]: g for g in p.gaps()}
        hard = [g for g in live["open_gap_ids"] if gmap[g]["priority"] in pol["blocking_priorities"] or not pol["accept_nonblocking_carry_forward"]]
        if hard:
            raise BAError("OPEN_GAPS", f"{sid} depends on unresolved gaps {hard}; resolve them or (for non-blocking only) record the "
                          "carry-forward policy acceptance.", gap_ids=hard)
    s["open_gap_ids"], s["decision_ids"] = live["open_gap_ids"], live["decision_ids"]
    approval = {"version": version, "decision": "APPROVED", "at_ist": now, "approver_name": approver_name,
                "approver_role": approver_role, "statement": statement.strip()}
    s["status"], s["approval"] = "APPROVED", approval
    _hist(s, "APPROVED", version, statement.strip(), approver_name=approver_name)
    prev = p.read_json(f"stories/approved/{sid}.json") if p.exists(f"stories/approved/{sid}.json") else None
    if prev and prev["version"] != version:
        p.archive_story(prev)
    fault("story_after_archive")
    p.save_story("approved", s)
    fault("story_after_approved_write")
    p.audit("STORY_APPROVED", story_id=sid, version=version, approver_name=approver_name, approver_role=approver_role,
            superseded_version=prev["version"] if prev else None, statement=statement.strip())
    p.remove_story_files("candidates", sid)
    p.save_state(f"story-{sid}-approved")
    return {"story_id": sid, "version": version, "status": "APPROVED", "approved_at_ist": now,
            "superseded_version": prev["version"] if prev else None, "location": f"stories/approved/{sid}.md"}


def edit(p, sid: str, changes: dict, reason: str) -> dict:
    if not normalize_ws(reason or ""):
        raise BAError("BAD_INPUT", "State why the story is being changed.")
    bad = [k for k in changes if k not in EDITABLE]
    if bad or not changes:
        raise BAError("BAD_INPUT", f"Provide changes using only {EDITABLE}.")
    bucket, base = _load(p, sid)
    ready = _ready_map(p)
    new = copy.deepcopy(base)
    for k, v in changes.items():
        new[k] = v
    _check_content(p, new, ready, skip_dup_of=sid, tag=sid)
    versions = [base["version"]] + [s["version"] for s in p.archived_stories() if s["story_id"] == sid]
    for b in ("candidates", "unapproved", "deferred", "approved"):
        if p.exists(f"stories/{b}/{sid}.json"):
            versions.append(p.read_json(f"stories/{b}/{sid}.json")["version"])
    nv = max(versions) + 1
    d = _derived(p, new["requirement_ids"])
    new.update({"version": nv, "status": "CANDIDATE", "approval": None, "deferral": None, "impact_review": None,
                "supersedes_version": base["version"], "decision_ids": d["decision_ids"], "open_gap_ids": d["open_gap_ids"],
                "source_references": d["source_references"], "requirement_versions": d["requirement_versions"],
                "baseline_version": p.state["baseline_version"]})
    new["title"] = normalize_ws(new["title"])
    _hist(new, "EDITED", nv, reason, from_version=base["version"])
    if bucket in ("candidates", "unapproved", "deferred"):
        p.archive_story(base)
        if bucket != "candidates":
            p.remove_story_files(bucket, sid)
    p.save_story("candidates", new)
    p.audit("STORY_EDITED", story_id=sid, from_version=base["version"], new_version=nv, reason=reason,
            previous_status=base["status"], requires_reapproval=True)
    if p.state["stage"] == "COMPLETED":
        p.reopen_to("WAITING_FOR_STORY_REVIEW", "story-edited")
    p.save_state(f"story-{sid}-edited")
    return {"story_id": sid, "new_version": nv, "status": "CANDIDATE", "requires_approval": True,
            "approved_version_still_in_force": bucket == "approved" or p.exists(f"stories/approved/{sid}.json")}


def defer(p, sid: str, version: int, reason: str | None = None) -> dict:
    bucket, s = _load(p, sid, ("candidates",))
    if s["version"] != version:
        raise BAError("VERSION_MISMATCH", f"{sid} is at v{s['version']}.")
    check_transition(STORY_TRANSITIONS, "Story", s["status"], "DEFERRED")
    s["status"], s["deferral"] = "DEFERRED", {"at_ist": ist_now_iso(), "reason": reason}
    _hist(s, "DEFERRED", version, reason)
    p.save_story("deferred", s)
    p.remove_story_files("candidates", sid)
    p.audit("STORY_DEFERRED", story_id=sid, version=version, reason=reason)
    p.save_state(f"story-{sid}-deferred")
    return {"story_id": sid, "status": "DEFERRED"}


def revisit(p, sid: str) -> dict:
    bucket, s = _load(p, sid, ("deferred",))
    check_transition(STORY_TRANSITIONS, "Story", "DEFERRED", "CANDIDATE")
    s["status"] = "CANDIDATE"
    _hist(s, "REVISITED", s["version"])
    p.save_story("candidates", s)
    p.remove_story_files("deferred", sid)
    p.audit("STORY_REVISITED", story_id=sid, version=s["version"])
    if p.state["stage"] == "COMPLETED":
        p.reopen_to("WAITING_FOR_STORY_REVIEW", "story-revisited")
    p.save_state(f"story-{sid}-revisited")
    return {"story_id": sid, "status": "CANDIDATE"}


def confirm_unaffected(p, sid: str, statement: str, by_name: str | None = None, by_role: str | None = None) -> dict:
    bucket, s = _load(p, sid)
    if not (s.get("impact_review") or {}).get("pending"):
        raise BAError("NOT_FLAGGED", f"{sid} has no pending impact review.")
    if not normalize_ws(statement or ""):
        raise BAError("BAD_INPUT", "A confirmation statement is required.")
    if by_name or by_role:
        by_name, by_role = require_identity(by_name, by_role, "Reviewer")
    ready = _ready_map(p)
    _check_content(p, s, ready, skip_dup_of=sid, tag=sid)
    live = _derived(p, s["requirement_ids"])
    changed = [r for r in s["requirement_ids"] if live["requirement_versions"][r] != s["requirement_versions"].get(r)]
    if changed:
        raise BAError("REQUIREMENT_CHANGED", f"{changed} changed since {sid} v{s['version']} was written; use `story edit` to create a new version.")
    s["impact_review"] = None
    _hist(s, "IMPACT_CLEARED", s["version"], statement, by_name=by_name)
    p.save_story(bucket, s)
    p.audit("STORY_IMPACT_CLEARED", story_id=sid, version=s["version"], bucket=bucket, by_name=by_name, by_role=by_role, statement=statement)
    return {"story_id": sid, "version": s["version"], "impact_review": "cleared"}
