"""User stories: generation, review, approval, edit/versioning, defer/revisit."""
from __future__ import annotations

import copy
import re

from . import conflicts, gaps as gapmod, storytrack
from .project import STORY_TRANSITIONS, check_transition, fault
from .util import BAError, ist_now_iso, normalize_ws, require_identity

GWT = re.compile(r"\bgiven\b.+\bwhen\b.+\bthen\b", re.I | re.S)
EDITABLE = ["title", "user_story", "acceptance_criteria", "requirement_ids", "dependencies", "assumptions", "notes",
            "unresolved_decisions"]
EFFECTS = ("NEEDS_CLARIFICATION", "NOT_READY")
READINESS = ("READY_FOR_DEVELOPMENT", "NEEDS_CLARIFICATION", "NOT_READY")
GENERATE_STAGES = ("REQUIREMENTS_BASELINED", "STORIES_READY", "WAITING_FOR_STORY_REVIEW", "COMPLETED")


def _ready_map(p) -> dict:
    b = p.approved_baseline()
    if not b:
        raise BAError("NO_APPROVED_BASELINE", "Stories are generated only from a human-approved baseline (`baseline confirm`).")
    if p.baseline_manifest()["stale"]:
        raise BAError("BASELINE_STALE", "The baseline is stale (inputs changed). Create and confirm a new baseline first.")
    return {e["requirement_id"]: e for e in b["requirements"]}


def open_gap_context(p, requirement_ids: list[str]) -> list[dict]:
    """The unresolved gaps that touch these requirements, with what a story author needs to describe them honestly."""
    out = []
    for g in p.gaps():
        if g["status"] != "RESOLVED" and set(g["impacted_requirement_ids"]) & set(requirement_ids):
            out.append({"gap_id": g["gap_id"], "title": g["title"], "priority": g["priority"], "status": g["status"],
                        "carried_forward": gapmod.is_carried(p, g), "blocks_stories": gapmod.blocks_requirements(p, g),
                        "question": g["question"], "description": g["description"], "why_it_matters": g["why_it_matters"],
                        "evidence": g["evidence"], "affects_requirements": [r for r in g["impacted_requirement_ids"] if r in requirement_ids]})
    return out


def readiness_of(unresolved: list[dict]) -> str:
    if any(u.get("readiness_effect") == "NOT_READY" for u in unresolved):
        return "NOT_READY"
    return "NEEDS_CLARIFICATION" if unresolved else "READY_FOR_DEVELOPMENT"


def _unresolved(p, c: dict, tag: str) -> list[dict] | None:
    """Validate the story's declared unresolved decisions against the live open gaps and return them normalised.
    Every open gap that touches the story's requirements must be named, with a potential impact written by the author;
    nothing is invented here, and gaps that are no longer open must be removed (and their decision applied)."""
    if "unresolved_decisions" not in c:
        return None  # story saved before carry-forward support; enforced as soon as it is edited
    ud = c["unresolved_decisions"]
    if not isinstance(ud, list) or not all(isinstance(u, dict) for u in ud):
        raise BAError("BAD_INPUT", f"{tag}: unresolved_decisions must be a list of objects "
                      "{gap_id, potential_impact, readiness_effect, interim_assumption?}.")
    gmap = {g["gap_id"]: g for g in p.gaps()}
    live = {g["gap_id"]: g for g in open_gap_context(p, c["requirement_ids"])}
    out, seen = [], set()
    for u in ud:
        gid = u.get("gap_id")
        if gid in seen:
            raise BAError("BAD_INPUT", f"{tag}: {gid} is listed twice in unresolved_decisions.")
        seen.add(gid)
        if gid in gmap and gmap[gid]["status"] == "RESOLVED":
            raise BAError("UNRESOLVED_DECISION_RESOLVED", f"{tag}: {gid} is resolved ({gmap[gid]['decision_id']}: "
                          f"{gmap[gid]['resolution']}). Remove it from unresolved_decisions and apply the decision to the story.",
                          gap_id=gid, decision_id=gmap[gid]["decision_id"])
        if gid not in live:
            raise BAError("UNKNOWN_GAP_LINK", f"{tag}: {gid} is not an unresolved gap of requirement(s) {c['requirement_ids']}. "
                          "Only real open gaps can be linked; never invent one.")
        impact = normalize_ws(u.get("potential_impact") or "")
        if len(impact) < 5:
            raise BAError("BAD_INPUT", f"{tag}: {gid} needs a potential_impact: what in this story may change or be wrong "
                          "depending on the answer.")
        effect = u.get("readiness_effect") or "NEEDS_CLARIFICATION"
        if effect not in EFFECTS:
            raise BAError("BAD_INPUT", f"{tag}: readiness_effect must be one of {list(EFFECTS)}.")
        assumption = normalize_ws(u.get("interim_assumption") or "") or None
        out.append({"gap_id": gid, "priority": live[gid]["priority"], "question": live[gid]["question"],
                    "carried_forward": live[gid]["carried_forward"], "potential_impact": impact, "readiness_effect": effect,
                    "interim_assumption": assumption})
    missing = [live[g] for g in live if g not in seen]
    if missing:
        raise BAError("UNRESOLVED_DECISION_MISSING", f"{tag}: the story depends on open gap(s) {[m['gap_id'] for m in missing]} "
                      "but does not declare them. Add one unresolved_decisions entry per gap (gap_id, potential_impact, "
                      "readiness_effect NEEDS_CLARIFICATION|NOT_READY, optional interim_assumption); see `story gaps`.",
                      missing=missing)
    return out


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
    _unresolved(p, c, tag)
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
    existing_ids = [s["story_id"] for _, s in p.all_stories()] + [s["story_id"] for s in p.archived_stories()] + storytrack.known_ids(p)
    built, errors = [], []
    now = ist_now_iso()
    batch_titles = []
    for n, it in enumerate(items):
        tag = f"item[{n}]"
        try:
            if normalize_ws(it.get("title", "")).lower() in batch_titles:
                raise BAError("DUPLICATE_STORY", f"{tag}: duplicate title inside the batch.")
            it.setdefault("unresolved_decisions", [])  # new stories always declare their open gaps (possibly none)
            _check_content(p, it, ready, tag=tag)
            batch_titles.append(normalize_ws(it["title"]).lower())
            sid = p.next_id("US", existing_ids)
            existing_ids.append(sid)
            d = _derived(p, it["requirement_ids"])
            unresolved_ok = d["open_gap_ids"]
            unresolved = _unresolved(p, it, tag)
            built.append({
                "story_id": sid, "version": 1, "title": normalize_ws(it["title"]), "status": "CANDIDATE",
                "user_story": {k: normalize_ws(it["user_story"][k]) for k in ("role", "capability", "value")},
                "acceptance_criteria": [normalize_ws(a) for a in it["acceptance_criteria"]],
                "requirement_ids": list(dict.fromkeys(it["requirement_ids"])), "requirement_versions": d["requirement_versions"],
                "baseline_version": st["baseline_version"], "source_references": d["source_references"],
                "decision_ids": d["decision_ids"], "open_gap_ids": unresolved_ok,
                "unresolved_decisions": unresolved, "readiness": readiness_of(unresolved),
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
            "with_open_gaps": [s["story_id"] for s in built if s["open_gap_ids"]],
            "needs_clarification": [s["story_id"] for s in built if s["readiness"] == "NEEDS_CLARIFICATION"],
            "not_ready": [s["story_id"] for s in built if s["readiness"] == "NOT_READY"]}


def gap_context(p, requirement_ids: list[str]) -> dict:
    """`story gaps`: what to declare in unresolved_decisions for stories that cover these requirements."""
    known = {x["requirement_id"] for x in p.requirements()}
    unknown = [r for r in requirement_ids if r not in known]
    if unknown:
        raise BAError("UNKNOWN_REQUIREMENT", f"Unknown requirement(s): {unknown}")
    ctx = open_gap_context(p, requirement_ids)
    return {"requirement_ids": requirement_ids, "open_gaps": ctx,
            "rule": "Declare every gap below in the story's unresolved_decisions with a potential_impact you can support from the "
                    "gap and the BRD. Do not invent requirements; a working assumption must be labelled interim_assumption and is "
                    "never an approved decision. readiness_effect NOT_READY = the core behaviour cannot be built until the gap is "
                    "answered; NEEDS_CLARIFICATION = buildable, details may change.",
            "blocking_gaps_present": [g["gap_id"] for g in ctx if g["blocks_stories"]]}


def _load(p, sid: str, buckets=("candidates", "unapproved", "deferred", "approved")) -> tuple[str, dict]:
    for b in buckets:
        if p.exists(f"stories/{b}/{sid}.json"):
            return b, p.read_json(f"stories/{b}/{sid}.json")
    raise BAError("UNKNOWN_STORY", f"{sid} not found in {list(buckets)}.")


def _hist(s, action, version, note=None, **extra):
    s["review_history"].append({"action": action, "version": version, "at_ist": ist_now_iso(), "note": note, **extra})
    s["updated_at"] = ist_now_iso()


PENDING_BUCKETS = ("candidates", "unapproved", "deferred")


def _blockers(p, s: dict, baseline_problem: str | None) -> list[str]:
    """Why this story cannot be approved as it stands (the same rules `approve` enforces). Empty = approvable."""
    out = []
    if baseline_problem:
        out.append(baseline_problem)
    if (s.get("impact_review") or {}).get("pending"):
        out.append(f"flagged for impact review: {s['impact_review'].get('reason')}")
    if p.state["impact_review"].get("pending"):
        out.append("a BRD change is awaiting impact review")
    live = _derived(p, s["requirement_ids"])
    changed = [r for r in s["requirement_ids"] if live["requirement_versions"].get(r) != s["requirement_versions"].get(r)]
    if changed:
        out.append(f"linked requirement(s) changed since this version: {', '.join(changed)} (edit the story)")
    gmap = {g["gap_id"]: g for g in p.gaps()}
    hard = [g for g in live["open_gap_ids"] if gapmod.blocks_requirements(p, gmap[g])]
    if hard:
        out.append(f"blocked by unresolved gap(s): {', '.join(hard)}")
    return out


def triage(p) -> dict:
    """`story triage`: every story that is not yet approved - pending review, in review, not approved, deferred - with what
    stops it being approved right now. Read-only: nothing is presented, moved or re-versioned."""
    baseline_problem = None
    if not p.approved_baseline():
        baseline_problem = "no approved baseline"
    elif p.baseline_manifest()["stale"]:
        baseline_problem = "the baseline is stale"
    order = {"IN_REVIEW": 0, "CANDIDATE": 1, "CHANGES_REQUESTED": 2, "DEFERRED": 3}
    rows = []
    for bucket, s in p.all_stories():
        if bucket not in PENDING_BUCKETS:
            continue
        blockers = _blockers(p, s, baseline_problem)
        last = next((h for h in reversed(s["review_history"]) if h["action"] in ("CHANGES_REQUESTED", "DEFERRED")), None)
        rows.append({
            "story_id": s["story_id"], "version": s["version"], "title": s["title"], "status": s["status"], "bucket": bucket,
            "location": f"stories/{bucket}/{s['story_id']}.md", "readiness": s.get("readiness"),
            "requirement_ids": s["requirement_ids"], "open_gap_ids": _derived(p, s["requirement_ids"])["open_gap_ids"],
            "deferral": s.get("deferral") if s["status"] == "DEFERRED" else None, "last_review_note": last and {"action": last["action"], "note": last.get("note"),
                                                                       "at_ist": last["at_ist"]},
            "impact_review": s.get("impact_review"), "eligible": not blockers, "blockers": blockers})
    rows.sort(key=lambda r: (order.get(r["status"], 9), r["story_id"]))
    counts = {k: sum(1 for r in rows if r["bucket"] == b) for k, b in (("pending_review", "candidates"),
              ("not_approved", "unapproved"), ("deferred", "deferred"))}
    return {"stories": rows, "total": len(rows), "counts": counts,
            "eligible": [r["story_id"] for r in rows if r["eligible"]],
            "not_eligible": [r["story_id"] for r in rows if not r["eligible"]],
            "next": "Present the stories one at a time in this order: `story next --id US-00x`." if rows
                    else "No unapproved stories."}


def next_story(p, story_id: str | None = None, deferred: bool = False, batch: int = 1, unapproved: bool = False) -> dict:
    buckets = ("deferred",) if deferred else ("unapproved",) if unapproved else ("candidates",)
    pool = [s for b, s in p.all_stories() if b in buckets]
    if story_id:
        _, one = _load(p, story_id, PENDING_BUCKETS + ("approved",))
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
        gmap = {g["gap_id"]: g for g in p.gaps()}
        hard = [g for g in live["open_gap_ids"] if gapmod.blocks_requirements(p, gmap[g])]
        if hard:
            raise BAError("OPEN_GAPS", f"{sid} depends on unresolved gaps {hard}; resolve them or (for non-blocking only) record the "
                          "carry-forward policy acceptance.", gap_ids=hard)
    s["open_gap_ids"], s["decision_ids"] = live["open_gap_ids"], live["decision_ids"]
    if "unresolved_decisions" in s:
        s["readiness"] = readiness_of(s["unresolved_decisions"])
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
            superseded_version=prev["version"] if prev else None, statement=statement.strip(),
            material_hash=storytrack.material_hash(s), content_hash=storytrack.content_hash(s))
    storytrack.record_approved(p, s)  # hashes + an immutable copy of exactly what was approved, so later edits are detected
    p.remove_story_files("candidates", sid)
    p.save_state(f"story-{sid}-approved")
    return {"story_id": sid, "version": version, "status": "APPROVED", "approved_at_ist": now,
            "readiness": s.get("readiness"), "open_gap_ids": s["open_gap_ids"],
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
    new.setdefault("unresolved_decisions", [])  # editing a story saved before carry-forward support brings it under the open-gap rules
    for k, v in changes.items():
        new[k] = v
    _check_content(p, new, ready, skip_dup_of=sid, tag=sid)
    new["unresolved_decisions"] = _unresolved(p, new, sid)
    new["readiness"] = readiness_of(new["unresolved_decisions"])
    if (bucket == "approved" and base["status"] == "APPROVED" and not (base.get("impact_review") or {}).get("pending")
            and storytrack.material_hash(new) == storytrack.material_hash(base)):
        return _edit_formatting_only(p, base, new, changes, reason)  # same meaning: the approval is not disturbed
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
    if bucket == "approved":
        from . import changes
        changes.record_agent_story_edit(p, base, new, reason)
    if p.state["stage"] == "COMPLETED":
        p.reopen_to("WAITING_FOR_STORY_REVIEW", "story-edited")
    p.save_state(f"story-{sid}-edited")
    return {"story_id": sid, "new_version": nv, "status": "CANDIDATE", "requires_approval": True,
            "readiness": new["readiness"], "open_gap_ids": new["open_gap_ids"],
            "approved_version_still_in_force": bucket == "approved" or p.exists(f"stories/approved/{sid}.json")}


def _edit_formatting_only(p, base: dict, new: dict, changes: dict, reason: str) -> dict:
    """An edit to an APPROVED story that changes wording layout but not meaning: keep the version and the approval."""
    from . import changes as changemod
    sid = base["story_id"]
    if storytrack.entry(p, sid) is None:
        storytrack.record_approved(p, base, event="ADOPTED")
    fmt = copy.deepcopy(base)
    for k in changes:
        fmt[k] = new[k]
    fmt["title"] = normalize_ws(fmt["title"])
    _hist(fmt, "FORMATTING_UPDATED", fmt["version"], reason)
    p.save_story("approved", fmt)
    storytrack.update_version(p, sid, fmt["version"], accepted_content_hash=storytrack.content_hash(fmt))
    storytrack.update_entry(p, sid, accepted_md_hash=None)
    p.audit("STORY_FORMATTING_UPDATED", story_id=sid, version=fmt["version"], reason=reason, approval_preserved=True)
    changemod.record_agent_story_edit(p, base, fmt, reason, formatting_only=True)
    p.save_state(f"story-{sid}-formatting-updated")
    return {"story_id": sid, "new_version": fmt["version"], "status": "APPROVED", "formatting_only": True, "requires_approval": False,
            "readiness": fmt.get("readiness"), "open_gap_ids": fmt["open_gap_ids"], "approved_version_still_in_force": True,
            "note": "The edit changes layout / wording style only, not the meaning, so the approval is unchanged (recorded in the audit log)."}


def defer(p, sid: str, version: int, reason: str | None = None) -> dict:
    bucket, s = _load(p, sid, ("candidates", "unapproved"))
    if s["version"] != version:
        raise BAError("VERSION_MISMATCH", f"{sid} is at v{s['version']}.")
    check_transition(STORY_TRANSITIONS, "Story", s["status"], "DEFERRED")
    prev_status = s["status"]
    s["status"], s["deferral"] = "DEFERRED", {"at_ist": ist_now_iso(), "reason": reason}
    _hist(s, "DEFERRED", version, reason)
    p.save_story("deferred", s)
    p.remove_story_files(bucket, sid)
    p.audit("STORY_DEFERRED", story_id=sid, version=version, reason=reason, from_status=prev_status)
    p.save_state(f"story-{sid}-deferred")
    return {"story_id": sid, "status": "DEFERRED"}


def revisit(p, sid: str) -> dict:
    """Bring a deferred - or a not-approved - story back for review at the same version (it is not approved by this)."""
    bucket, s = _load(p, sid, ("deferred", "unapproved"))
    prev_status = s["status"]
    check_transition(STORY_TRANSITIONS, "Story", prev_status, "CANDIDATE")
    s["status"] = "CANDIDATE"
    _hist(s, "REVISITED", s["version"], from_status=prev_status)
    p.save_story("candidates", s)
    p.remove_story_files(bucket, sid)
    p.audit("STORY_REVISITED", story_id=sid, version=s["version"], from_status=prev_status)
    if p.state["stage"] == "COMPLETED":
        p.reopen_to("WAITING_FOR_STORY_REVIEW", "story-revisited")
    p.save_state(f"story-{sid}-revisited")
    return {"story_id": sid, "status": "CANDIDATE"}


def keep(p, sid: str) -> dict:
    """Triage: leave a not-yet-approved story exactly as it is (it stays available for later review); only the decision is audited."""
    bucket, s = _load(p, sid, PENDING_BUCKETS)
    p.audit("STORY_TRIAGE_KEPT", story_id=sid, version=s["version"], status=s["status"], location=f"stories/{bucket}/{sid}.md")
    return {"story_id": sid, "version": s["version"], "status": s["status"], "location": f"stories/{bucket}/{sid}.md",
            "note": "Left as it is; it stays available for later review. Nothing was approved."}


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
