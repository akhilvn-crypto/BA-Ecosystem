"""Decision owner, gap lifecycle, answer validation, and audited gap closure."""
from __future__ import annotations

import copy
import uuid

from . import conflicts, reqs
from .project import GAP_TRANSITIONS, check_transition, fault
from .util import (BAError, append_jsonl, atomic_write_text, ist_now_iso, normalize_ws, read_jsonl,
                   require_identity, same_person)

import json

CATEGORIES = ["Functional behavior", "Business rules", "Data requirements", "Validation and exception handling",
              "Integration", "Security and access", "Non-functional requirements", "Workflow/state transitions",
              "Acceptance criteria and testability", "Contradiction, duplication, or dependency"]
PRIORITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
UNRESOLVED = ("OPEN", "WAITING_FOR_ANSWER", "DEFERRED", "REOPENED", "BLOCKED")
ACTIVE_REQ = ("ACTIVE", "NEEDS_REVIEW")


# --------------------------------------------------------------- owner

def effective_owner(p, gap: dict | None = None) -> dict | None:
    if gap and gap.get("decision_owner_override"):
        return gap["decision_owner_override"]
    return p.state.get("decision_owner")


def require_owner(p, gap: dict | None = None) -> dict:
    st = p.state
    if st.get("decision_owner_unknown") and not effective_owner(p, gap):
        raise BAError("OWNER_UNKNOWN", "The decision owner is not yet known (user said so). Gap triage cannot start. "
                      "Ask the user for the owner's name and role, or leave the workflow paused.")
    o = effective_owner(p, gap)
    if not o:
        raise BAError("OWNER_REQUIRED", "Before gap triage, ask the user: 'Who will make/confirm the business decisions "
                      "for these gaps? Please provide their name and role (e.g. Priya Nair — Product Owner).' "
                      "Then run `owner set`. Never infer the name from account metadata.")
    return o


def set_owner(p, name: str, role: str, gap_id: str | None = None, statement: str | None = None) -> dict:
    name, role = require_identity(name, role, "Decision owner")
    now = ist_now_iso()
    entry = {"name": name, "role": role, "set_at_ist": now, "statement": statement}
    if gap_id:
        gaps = p.gaps()
        g = next((x for x in gaps if x["gap_id"] == gap_id), None)
        if not g:
            raise BAError("UNKNOWN_GAP", f"Gap {gap_id} does not exist.")
        if g["status"] == "RESOLVED":
            raise BAError("GAP_ALREADY_RESOLVED", "A resolved gap's owner is part of its audit record and is not rewritten.")
        prior = g.get("decision_owner_override")
        g["decision_owner_override"] = entry
        g["decision_owner_name"], g["decision_owner_role"], g["updated_at"] = name, role, now
        p.save_gaps(gaps, touched=[gap_id])
        p.audit("GAP_DECISION_OWNER_OVERRIDE", gap_id=gap_id, owner={"name": name, "role": role},
                previous=prior and {"name": prior["name"], "role": prior["role"]})
        return {"gap_id": gap_id, "decision_owner": entry}
    st = p.state
    prior = st.get("decision_owner")
    if prior:
        st["decision_owner_history"].append(dict(prior, replaced_at_ist=now))
    st["decision_owner"] = entry
    was_unknown = st.get("decision_owner_unknown")
    st["decision_owner_unknown"] = None
    gaps = p.gaps()
    touched = []
    for g in gaps:
        if g["status"] != "RESOLVED" and not g.get("decision_owner_override"):
            g["decision_owner_name"], g["decision_owner_role"], g["updated_at"] = name, role, now
            touched.append(g["gap_id"])
    if touched:
        p.save_gaps(gaps, touched=touched)
    p.audit("DECISION_OWNER_CHANGED" if prior else "DECISION_OWNER_SET", owner={"name": name, "role": role},
            previous=prior and {"name": prior["name"], "role": prior["role"]}, statement=statement)
    p.save_state("decision-owner-set")
    if st["stage"] == "PAUSED" and was_unknown and was_unknown.get("choice") == "pause":
        p.set_stage(st["resume_stage"], "resumed-after-owner-identified")
    return {"decision_owner": entry, "gaps_updated": touched, "previous_owner": prior}


def owner_unknown(p, choice: str) -> dict:
    if choice not in ("pause", "analysis-only"):
        raise BAError("BAD_INPUT", "choice must be 'pause' or 'analysis-only'")
    p.state["decision_owner_unknown"] = {"choice": choice, "at_ist": ist_now_iso()}
    p.audit("DECISION_OWNER_UNKNOWN", choice=choice)
    if choice == "pause" and p.state["stage"] not in ("PAUSED", "BLOCKED"):
        p.set_stage("PAUSED", "paused-owner-unknown")
    else:
        p.save_state("owner-unknown-recorded")
    return {"decision_owner": None, "choice": choice,
            "note": "No gap will be presented for triage until a decision owner is identified."}


# ----------------------------------------------------------------- add

def add_gaps(p, items: list[dict]) -> dict:
    st = p.state
    if st["stage"] in ("INITIALIZED", "BRD_PARSED", "PAUSED", "BLOCKED"):
        raise BAError("INVALID_STAGE", f"Gaps can be added once requirements are ready; stage is {st['stage']}.")
    if not isinstance(items, list) or not items:
        raise BAError("BAD_INPUT", "Provide a non-empty JSON list of gaps.")
    reqmap = {r["requirement_id"]: r for r in p.requirements()}
    gaps = p.gaps()
    ids = [g["gap_id"] for g in gaps]
    owner = st.get("decision_owner")
    now = ist_now_iso()
    built, skipped, errors = [], [], []
    seen_q = {normalize_ws(g["question"]).lower() for g in gaps if g["status"] != "RESOLVED"}
    for n, it in enumerate(items):
        tag = f"item[{n}]"
        try:
            for f in ("category", "title", "description", "evidence", "why_it_matters", "question",
                      "impacted_requirement_ids", "priority", "priority_rationale"):
                if not it.get(f):
                    raise BAError("BAD_INPUT", f"{tag}: '{f}' is required (priority must be justified).")
            if it["category"] not in CATEGORIES:
                raise BAError("BAD_INPUT", f"{tag}: category must be one of {CATEGORIES}")
            if it["priority"] not in PRIORITIES:
                raise BAError("BAD_INPUT", f"{tag}: priority must be one of {PRIORITIES}")
            bad = [r for r in it["impacted_requirement_ids"] if r not in reqmap or reqmap[r]["status"] not in ACTIVE_REQ]
            if bad:
                raise BAError("UNKNOWN_REQUIREMENT", f"{tag}: unknown/inactive requirement(s) {bad}")
            qn = normalize_ws(it["question"]).lower()
            if qn in seen_q:
                skipped.append({"item": n, "reason": "same question already open"})
                continue
            seen_q.add(qn)
            gid = p.next_id("GAP", ids)
            ids.append(gid)
            built.append({
                "gap_id": gid, "category": it["category"], "title": normalize_ws(it["title"]),
                "description": it["description"].strip(), "evidence": it["evidence"].strip(),
                "why_it_matters": it["why_it_matters"].strip(), "question": it["question"].strip(),
                "impacted_requirement_ids": list(dict.fromkeys(it["impacted_requirement_ids"])),
                "priority": it["priority"], "priority_rationale": it["priority_rationale"].strip(),
                "material": bool(it.get("material", it["category"] == "Security and access")),
                "status": "OPEN", "owner": it.get("owner"), "decision_owner_override": None, "resolution": None,
                "decision_id": None, "decision_owner_name": owner["name"] if owner else None,
                "decision_owner_role": owner["role"] if owner else None, "closed_by_name": None, "closed_by_role": None,
                "respondent_name": None, "respondent_role": None, "closed_at_ist": None, "closure_summary": None,
                "closure_validation": None, "answers": [], "conflicts": [], "deferral": None, "skipped_count": 0,
                "needs_impact_review": False, "history": [{"from": None, "to": "OPEN", "at_ist": now, "note": "gap opened"}],
                "created_at": now, "updated_at": now})
        except BAError as e:
            errors.append(e.message)
    if errors:
        raise BAError("GAPS_REJECTED", "No gaps were saved: " + " | ".join(errors), errors=errors)
    if built:
        p.save_gaps(gaps + built, touched=[g["gap_id"] for g in built])
        for g in built:
            for rid in g["impacted_requirement_ids"]:
                r = reqmap[rid]
                if g["gap_id"] not in r["related_gap_ids"]:
                    r["related_gap_ids"].append(g["gap_id"])
                    p.save_requirement(r)
        p.audit("GAP_OPENED", gap_ids=[g["gap_id"] for g in built])
    stage = st["stage"]
    if stage == "REQUIREMENTS_READY":
        p.set_stage("GAP_ANALYSIS", "gaps-saved")
    elif stage in ("REQUIREMENTS_BASELINED", "STORIES_READY", "WAITING_FOR_STORY_REVIEW", "COMPLETED") and built:
        p.mark_baseline_stale("new gaps opened")
        p.reopen_to("WAITING_FOR_GAP_INPUT", "new-gaps")
    else:
        p.save_state("gaps-saved")
    return {"created": [g["gap_id"] for g in built], "skipped": skipped}


def commit_analysis(p, cross_pass_done: bool) -> dict:
    if not cross_pass_done:
        raise BAError("CROSS_PASS_REQUIRED", "Run the cross-requirement pass (`req pairs` + contradiction/dependency review) "
                      "and pass --cross-pass-done to confirm.")
    st = p.state
    if st["stage"] == "REQUIREMENTS_READY":
        p.set_stage("GAP_ANALYSIS", "gap-analysis-started")
    if p.state["stage"] not in ("GAP_ANALYSIS", "WAITING_FOR_GAP_INPUT"):
        raise BAError("INVALID_STAGE", f"Cannot commit gap analysis from {p.state['stage']}.")
    if any(r["status"] == "NEEDS_REVIEW" for r in p.requirements()):
        raise BAError("IMPACT_REVIEW_INCOMPLETE", "Some requirements still need review after a source change.")
    p.state["gap_analysis_complete"] = True
    p.audit("GAP_ANALYSIS_COMPLETED", gap_count=len(p.gaps()), cross_pass=True)
    unresolved = [g for g in p.gaps() if g["status"] != "RESOLVED"]
    if unresolved and p.state["stage"] == "GAP_ANALYSIS":
        p.set_stage("WAITING_FOR_GAP_INPUT", "gap-analysis-committed")
    else:
        p.save_state("gap-analysis-committed")
    return {"gaps": len(p.gaps()), "unresolved": len(unresolved), "stage": p.state["stage"]}


# -------------------------------------------------------------- triage

def _packet(p, g: dict, reqmap: dict) -> dict:
    owner = effective_owner(p, g)
    return {
        "gap_id": g["gap_id"], "category": g["category"], "priority": g["priority"], "title": g["title"],
        "status": g["status"], "question": g["question"], "evidence": g["evidence"], "why_it_matters": g["why_it_matters"],
        "material": g["material"], "decision_owner": owner and {"name": owner["name"], "role": owner["role"]},
        "impacted_requirements": [{"id": r, "title": reqmap[r]["title"], "statement": reqmap[r]["statement"],
                                   "source": reqmap[r]["source_reference"]} for r in g["impacted_requirement_ids"] if r in reqmap],
        "previous_answers": [{"answer_id": a["answer_id"], "response": a["response"], "assessment": a.get("assessment"),
                              "note": a.get("assessment_note")} for a in g["answers"]],
        "open_conflicts": [c for c in g["conflicts"] if c["status"] == "OPEN"],
        "needs_impact_review": g.get("needs_impact_review", False),
        "options": ["answer (free text)", "ask for rewording/clarification", "defer", "skip for now"],
    }


def next_gaps(p, limit: int = 1) -> dict:
    require_owner(p)
    gaps = p.gaps()
    reqmap = {r["requirement_id"]: r for r in p.requirements()}
    rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    cand = [g for g in gaps if g["status"] in ("WAITING_FOR_ANSWER", "OPEN", "REOPENED")]
    cand.sort(key=lambda g: (g["status"] != "WAITING_FOR_ANSWER", g["skipped_count"], rank[g["priority"]], g["gap_id"]))
    chosen = cand[:max(1, limit)]
    now = ist_now_iso()
    touched = []
    for g in chosen:
        if g["status"] in ("OPEN", "REOPENED"):
            check_transition(GAP_TRANSITIONS, "Gap", g["status"], "WAITING_FOR_ANSWER")
            g["history"].append({"from": g["status"], "to": "WAITING_FOR_ANSWER", "at_ist": now, "note": "presented to user"})
            g["status"], g["updated_at"] = "WAITING_FOR_ANSWER", now
            touched.append(g["gap_id"])
    if touched:
        p.save_gaps(gaps, touched=touched)
        p.audit("GAP_PRESENTED", gap_ids=touched)
    if p.state["stage"] == "GAP_ANALYSIS" and chosen:
        p.set_stage("WAITING_FOR_GAP_INPUT", "gaps-presented")
    elif p.state["stage"] in ("REQUIREMENTS_BASELINED", "STORIES_READY", "WAITING_FOR_STORY_REVIEW", "COMPLETED") and chosen:
        p.reopen_to("WAITING_FOR_GAP_INPUT", "gap-revisit")
    remaining = len(cand) - len(chosen)
    return {"gaps": [_packet(p, g, reqmap) for g in chosen], "remaining_unpresented": remaining,
            "unresolved_total": sum(1 for g in gaps if g["status"] != "RESOLVED")}


def _get(gaps, gid):
    g = next((x for x in gaps if x["gap_id"] == gid), None)
    if not g:
        raise BAError("UNKNOWN_GAP", f"Gap {gid} does not exist.")
    return g


def precheck(p, gap: dict, texts: list[str]) -> list[dict]:
    against = [(r["requirement_id"], r["statement"]) for r in p.requirements() if r["status"] in ACTIVE_REQ]
    against += [(d["decision_id"], d["validated_interpretation"]) for d in p.decisions().values() if d["current_status"] == "ACTIVE"]
    for g in p.gaps():
        if g["gap_id"] != gap["gap_id"]:
            for a in g["answers"]:
                if a.get("assessment") == "ACCEPTED":
                    against.append((f"{g['gap_id']}/{a['answer_id']}", a["response"]))
    merged: dict[str, dict] = {}
    for t in texts:
        for c in conflicts.candidate_conflicts(t, against):
            merged.setdefault(c["artifact_id"], c)
    return list(merged.values())


def record_answer(p, gap_id: str, response: str, respondent_name: str | None = None, respondent_role: str | None = None) -> dict:
    gaps = p.gaps()
    g = _get(gaps, gap_id)
    require_owner(p, g)
    if g["status"] != "WAITING_FOR_ANSWER":
        raise BAError("NOT_PRESENTED", f"{gap_id} is {g['status']}; present it first (`gaps next`) or revisit it.")
    if not normalize_ws(response):
        raise BAError("BAD_INPUT", "The response is empty.")
    if respondent_name or respondent_role:
        respondent_name, respondent_role = require_identity(respondent_name, respondent_role, "Respondent")
    now = ist_now_iso()
    aid = f"A{len(g['answers']) + 1}"
    g["answers"].append({"answer_id": aid, "response": response.strip(), "respondent_name": respondent_name,
                         "respondent_role": respondent_role, "received_at_ist": now, "assessment": None})
    g["updated_at"] = now
    p.save_gaps(gaps, touched=[gap_id])
    p.audit("GAP_ANSWER_RECEIVED", gap_id=gap_id, answer_id=aid, respondent_name=respondent_name, respondent_role=respondent_role)
    cands = precheck(p, g, [response])
    return {"gap_id": gap_id, "answer_id": aid, "received_at_ist": now, "candidate_conflicts": cands,
            "next": "Decide: complete? contradictory? If incomplete → `gap followup`. If conflicting → `gap conflict`. "
                    "Otherwise `gap resolve` (review every candidate conflict explicitly)."}


def _find_answer(g, aid):
    a = next((x for x in g["answers"] if x["answer_id"] == aid), None)
    if not a:
        raise BAError("UNKNOWN_ANSWER", f"{g['gap_id']} has no answer {aid}.")
    return a


def followup(p, gap_id: str, answer_id: str, note: str) -> dict:
    gaps = p.gaps()
    g = _get(gaps, gap_id)
    a = _find_answer(g, answer_id)
    if g["status"] != "WAITING_FOR_ANSWER":
        raise BAError("NOT_PRESENTED", f"{gap_id} is {g['status']}.")
    if not normalize_ws(note):
        raise BAError("BAD_INPUT", "State what is still missing.")
    a["assessment"], a["assessment_note"] = "INCOMPLETE", note
    g["updated_at"] = ist_now_iso()
    p.save_gaps(gaps, touched=[gap_id])
    p.audit("GAP_ANSWER_INCOMPLETE", gap_id=gap_id, answer_id=answer_id, note=note)
    return {"gap_id": gap_id, "answer_id": answer_id, "status": g["status"], "note": "Raw answer retained. Ask the follow-up."}


def record_conflict(p, gap_id: str, answer_id: str, with_ids: list[str], description: str) -> dict:
    gaps = p.gaps()
    g = _get(gaps, gap_id)
    a = _find_answer(g, answer_id)
    if not with_ids or not normalize_ws(description):
        raise BAError("BAD_INPUT", "Cite the conflicting artifact IDs and describe the contradiction.")
    now = ist_now_iso()
    cid = f"C{len(g['conflicts']) + 1}"
    g["conflicts"].append({"conflict_id": cid, "answer_id": answer_id, "with": with_ids, "description": description,
                           "status": "OPEN", "detected_at_ist": now})
    a["assessment"], a["assessment_note"] = "CONFLICT", description
    g["updated_at"] = now
    p.save_gaps(gaps, touched=[gap_id])
    p.audit("CONFLICT_DETECTED", gap_id=gap_id, answer_id=answer_id, conflicting_artifact_ids=with_ids, description=description)
    return {"gap_id": gap_id, "conflict_id": cid, "status": g["status"],
            "next": "Ask the user to clarify, explicitly confirm a change (then `gap resolve --conflict-status CHANGE_CONFIRMED`), "
                    "or defer. The original requirement and the raw answer are both preserved."}


# --------------------------------------------------------- closure (WAL)

def resolve(p, gap_id: str, answer_id: str, interpretation: str, closed_by_name: str, closed_by_role: str,
            closure_summary: str, confirmation: str, conflict_status: str = "PASSED", conflicting_ids: list[str] | None = None,
            reviewed_ids: list[str] | None = None, change_by_name: str | None = None, change_by_role: str | None = None,
            change_statement: str | None = None, requirement_updates: dict | None = None,
            owner_confirmation: str | None = None) -> dict:
    now = ist_now_iso()  # fail early if the clock is unreliable
    conflicting_ids, reviewed_ids = list(conflicting_ids or []), list(reviewed_ids or [])
    requirement_updates = requirement_updates or {}
    for jf in p.path("state/pending").glob("*.closure.json"):
        if json.loads(jf.read_text(encoding="utf-8")).get("gap_id") == gap_id:
            raise BAError("CLOSURE_IN_PROGRESS", f"An interrupted closure of {gap_id} exists. Run `recover` before retrying.")
    gaps = p.gaps()
    g = _get(gaps, gap_id)
    if g["status"] != "WAITING_FOR_ANSWER":
        raise BAError("NOT_PRESENTED", f"{gap_id} is {g['status']}; only a presented (WAITING_FOR_ANSWER) gap can be resolved.")
    owner = require_owner(p, g)
    owner_name, owner_role = require_identity(owner["name"], owner["role"], "Decision owner")
    closer_name, closer_role = require_identity(closed_by_name, closed_by_role, "Gap closer")
    a = _find_answer(g, answer_id)
    if a.get("assessment") == "INCOMPLETE":
        raise BAError("ANSWER_INCOMPLETE", "That answer was marked incomplete; record the follow-up answer first.")
    for label, val in (("interpretation", interpretation), ("closure_summary", closure_summary),
                       ("confirmation (the closer's own words confirming closure)", confirmation)):
        if not normalize_ws(val or ""):
            raise BAError("MISSING_METADATA", f"Closure requires {label}.")
    if conflict_status not in ("PASSED", "CHANGE_CONFIRMED"):
        raise BAError("BAD_INPUT", "conflict-status must be PASSED or CHANGE_CONFIRMED (use `gap conflict` to record a conflict).")

    cands = precheck(p, g, [a["response"], interpretation])
    open_conf = [c for c in g["conflicts"] if c["status"] == "OPEN" and c["answer_id"] == answer_id]
    change_auth = None
    supersede: list[str] = []
    reopen: list[str] = []
    if conflict_status == "PASSED":
        if conflicting_ids or open_conf or a.get("assessment") == "CONFLICT":
            raise BAError("CONFLICT_UNRESOLVED", "This answer has a recorded conflict. Obtain an explicit change confirmation "
                          "and use --conflict-status CHANGE_CONFIRMED, or defer/clarify.", conflicts=open_conf)
        unreviewed = [c for c in cands if c["artifact_id"] not in reviewed_ids]
        if unreviewed:
            raise BAError("CONFLICT_REVIEW_REQUIRED", "Possible conflicts must be reviewed before closing: "
                          + "; ".join(f"{c['artifact_id']} ({', '.join(c['reasons'])})" for c in unreviewed)
                          + ". If genuinely compatible, pass them via --reviewed; if not, run `gap conflict`.", candidates=unreviewed)
    else:
        if not conflicting_ids:
            raise BAError("BAD_INPUT", "CHANGE_CONFIRMED requires --conflicting ids of the artifacts being changed.")
        cn, cr = require_identity(change_by_name, change_by_role, "Change authoriser")
        if not normalize_ws(change_statement or ""):
            raise BAError("MISSING_METADATA", "A change authorisation statement (verbatim) is required.")
        change_auth = {"name": cn, "role": cr, "statement": change_statement.strip(), "at_ist": now}
        decs = p.decisions()
        for cid in conflicting_ids:
            if cid.startswith("REQ-"):
                p.requirement(cid)
                if cid not in requirement_updates:
                    raise BAError("REQUIREMENT_UPDATE_REQUIRED", f"{cid} is being changed; supply its new content in --requirement-updates "
                                  "so the baseline does not keep the contradicted text.")
            elif cid.startswith("DEC-"):
                if cid not in decs or decs[cid]["current_status"] != "ACTIVE":
                    raise BAError("UNKNOWN_DECISION", f"{cid} is not an ACTIVE decision.")
                supersede.append(cid)
                for og in gaps:
                    if og["decision_id"] == cid and og["status"] == "RESOLVED" and og["gap_id"] != gap_id:
                        reopen.append(og["gap_id"])
            elif cid.startswith("GAP-"):
                og = _get(gaps, cid.split("/")[0])
                if og["status"] == "RESOLVED":
                    reopen.append(og["gap_id"])
            else:
                raise BAError("BAD_INPUT", f"Unrecognised conflicting artifact id {cid}")
        for rid in requirement_updates:
            if rid not in conflicting_ids:
                raise BAError("BAD_INPUT", f"{rid} has an update but is not listed in --conflicting.")

    # materiality: scope/policy/security/compliance/requirement change needs the decision owner
    material = bool(g.get("material") or conflict_status == "CHANGE_CONFIRMED" or requirement_updates)
    confirmed_by = {"name": closer_name, "role": closer_role}
    owner_stmt = None
    if material and not same_person(closer_name, owner_name):
        if not normalize_ws(owner_confirmation or ""):
            a["awaiting_owner_confirmation"] = True
            g["updated_at"] = now
            p.save_gaps(gaps, touched=[gap_id])
            p.audit("GAP_AWAITING_OWNER_CONFIRMATION", gap_id=gap_id, answer_id=answer_id,
                    decision_owner={"name": owner_name, "role": owner_role})
            raise BAError("AWAITING_OWNER_CONFIRMATION",
                          f"{gap_id} materially affects scope/policy/security/requirements. It stays open until the decision owner "
                          f"({owner_name}, {owner_role}) confirms. Recommendations are not decisions.",
                          decision_owner={"name": owner_name, "role": owner_role})
        confirmed_by, owner_stmt = {"name": owner_name, "role": owner_role}, owner_confirmation.strip()
    elif material:
        owner_stmt = confirmation.strip()

    # -- plan (everything below is derived data; nothing is written until the journal is saved)
    recs, _ = read_jsonl(p.path("decisions/decision-log.jsonl"))
    did = p.next_id("DEC", [r["decision_id"] for r in recs if r.get("record_type") == "decision"])
    jid = uuid.uuid4().hex[:12]
    decision = {
        "record_type": "decision", "decision_id": did, "gap_id": gap_id,
        "related_artifact_ids": list(dict.fromkeys(g["impacted_requirement_ids"] + conflicting_ids)),
        "question": g["question"], "human_response": a["response"], "answer_id": answer_id,
        "validated_interpretation": normalize_ws(interpretation),
        "validation_status": "CHANGE_CONFIRMED" if conflict_status == "CHANGE_CONFIRMED" else "VALIDATED",
        "decision_owner_name": owner_name, "decision_owner_role": owner_role,
        "respondent_name": a.get("respondent_name"), "respondent_role": a.get("respondent_role"),
        "confirmed_by_name": confirmed_by["name"], "confirmed_by_role": confirmed_by["role"],
        "confirmation_statement": owner_stmt or confirmation.strip(), "timestamp_ist": now,
        "response_received_at_ist": a["received_at_ist"],
        "conflict_check": {"status": conflict_status, "conflicting_artifact_ids": conflicting_ids},
        "change_authorization": change_auth,
        "supersedes_decision_id": supersede[0] if supersede else None, "supersedes_decision_ids": supersede,
    }
    validation = {"status": conflict_status, "validated_at_ist": now, "reviewed_candidate_ids": reviewed_ids,
                  "checks": ["completeness", "conflict-vs-brd-requirements", "conflict-vs-decisions", "conflict-vs-answers"],
                  "conflicting_artifact_ids": conflicting_ids}
    event = {
        "event_id": "EV-" + jid, "at_ist": now, "timezone": "Asia/Kolkata", "clock_source": "host-system-clock",
        "event_type": "GAP_RESOLVED", "gap_id": gap_id, "decision_id": did,
        "decision_owner": {"name": owner_name, "role": owner_role},
        "closed_by": {"name": closer_name, "role": closer_role},
        "respondent": ({"name": a["respondent_name"], "role": a["respondent_role"]} if a.get("respondent_name") else None),
        "response_received_at_ist": a["received_at_ist"], "closed_at_ist": now,
        "closure_summary": normalize_ws(closure_summary), "closure_validation": validation,
        "conflict_check": {"status": conflict_status, "conflicting_artifact_ids": conflicting_ids},
        "closer_confirmation": confirmation.strip(), "owner_confirmation": owner_stmt if material else None,
        "reopened_gap_ids": sorted(set(reopen)), "superseded_decision_ids": supersede,
    }
    after = copy.deepcopy(g)
    after.update({
        "status": "RESOLVED", "resolution": normalize_ws(interpretation), "decision_id": did,
        "decision_owner_name": owner_name, "decision_owner_role": owner_role, "closed_by_name": closer_name,
        "closed_by_role": closer_role, "respondent_name": a.get("respondent_name"), "respondent_role": a.get("respondent_role"),
        "closed_at_ist": now, "closure_summary": normalize_ws(closure_summary), "closure_validation": validation,
        "needs_impact_review": False, "updated_at": now})
    for aa in after["answers"]:
        if aa["answer_id"] == answer_id:
            aa["assessment"] = "ACCEPTED"
    for c in after["conflicts"]:
        if c["answer_id"] == answer_id and c["status"] == "OPEN":
            c["status"], c["resolved_at_ist"], c["resolution"] = "RESOLVED_BY_CHANGE", now, did
    after["history"].append({"from": "WAITING_FOR_ANSWER", "to": "RESOLVED", "at_ist": now, "note": f"{did} closed by {closer_name}"})
    req_after = {}
    for rid, ch in requirement_updates.items():
        req_after[rid] = reqs.apply_change(p, p.requirement(rid), ch, f"Change confirmed via {gap_id}/{did}", "decision", did)
    journal = {"journal_id": jid, "gap_id": gap_id, "decision": decision, "event": event, "gap_after": after,
               "requirement_after": req_after, "reopen_gap_ids": sorted(set(reopen)), "superseded": supersede, "created_at_ist": now}
    try:
        atomic_write_text(p.path(f"state/pending/{jid}.closure.json"), json.dumps(journal, indent=2, ensure_ascii=False))
    except OSError as e:
        raise BAError("WRITE_FAILED", f"Could not persist the closure journal; {gap_id} stays pending: {e}")
    fault("after_journal")
    try:
        apply_closure(p, journal)
    except OSError as e:
        raise BAError("WRITE_FAILED", f"Closure of {gap_id} was interrupted ({e}); it is journaled and will be completed by `recover`.")
    return {"gap_id": gap_id, "status": "RESOLVED", "decision_id": did, "closed_at_ist": now,
            "closed_by": {"name": closer_name, "role": closer_role}, "decision_owner": {"name": owner_name, "role": owner_role},
            "reopened_gaps": journal["reopen_gap_ids"], "superseded_decisions": supersede,
            "updated_requirements": {k: v["version"] for k, v in req_after.items()}}


def apply_closure(p, j: dict) -> None:
    """Idempotent roll-forward of a closure journal. Safe to re-run after a crash at any point."""
    dec, ev, did = j["decision"], j["event"], j["decision"]["decision_id"]
    logp = p.path("decisions/decision-log.jsonl")
    recs, _ = read_jsonl(logp)
    if not any(r.get("record_type") == "decision" and r["decision_id"] == did for r in recs):
        append_jsonl(logp, dec)
    fault("after_decision")
    for sid in j["superseded"]:
        if not any(r.get("record_type") == "decision_status" and r["decision_id"] == sid and r.get("by") == did for r in recs):
            append_jsonl(logp, {"record_type": "decision_status", "decision_id": sid, "status": "SUPERSEDED",
                                "reason": f"superseded by {did}", "by": did, "at_ist": ev["closed_at_ist"]})
    if not any(e.get("event_id") == ev["event_id"] for e in p.audit_events()):
        append_jsonl(p.path("state/audit.jsonl"), ev)
    fault("after_event")
    changed_reqs = set()
    for rid, newrec in j["requirement_after"].items():
        cur = p.requirement(rid)
        if cur["version"] < newrec["version"]:
            p.save_requirement(newrec)
            p.audit("REQUIREMENT_UPDATED", requirement_id=rid, new_version=newrec["version"], cause="decision", decision_id=did)
            changed_reqs.add(rid)
    for rid in j["gap_after"]["impacted_requirement_ids"]:
        r = p.requirement(rid)
        if did not in r["related_decision_ids"]:
            r["related_decision_ids"].append(did)
            p.save_requirement(r)
    gaps = p.gaps()
    touched = []
    for i, g in enumerate(gaps):
        if g["gap_id"] == j["gap_id"] and not (g["status"] == "RESOLVED" and g.get("decision_id") == did):
            gaps[i] = j["gap_after"]
            touched.append(g["gap_id"])
        elif g["gap_id"] in j["reopen_gap_ids"] and g["status"] == "RESOLVED":
            snap = {k: g.get(k) for k in ("decision_id", "resolution", "closed_by_name", "closed_by_role", "closed_at_ist",
                                          "closure_summary", "closure_validation", "respondent_name", "respondent_role")}
            g.setdefault("closure_history", []).append(snap)
            for k in snap:
                g[k] = None
            g["status"], g["updated_at"] = "REOPENED", ev["closed_at_ist"]
            g["history"].append({"from": "RESOLVED", "to": "REOPENED", "at_ist": ev["closed_at_ist"],
                                 "note": f"affected by confirmed change {did}"})
            touched.append(g["gap_id"])
    fault("before_register")
    if touched:
        p.save_gaps(gaps, touched=touched)
    fault("after_register")
    p.render_decision(did)
    for sid in j["superseded"]:
        p.render_decision(sid)
    if j["reopen_gap_ids"] or changed_reqs:
        rids = set(changed_reqs)
        for og in j["reopen_gap_ids"]:
            rids |= set(p.gap(og)["impacted_requirement_ids"])
        if rids:
            reqs._after_requirement_change(p, rids, f"Confirmed change via {did}", "confirmed-change")
        if j["reopen_gap_ids"]:
            p.reopen_to("WAITING_FOR_GAP_INPUT", "gap-reopened")
    p.save_state(f"gap-resolved-{j['gap_id']}")
    jp = p.path(f"state/pending/{j['journal_id']}.closure.json")
    if jp.exists():
        jp.unlink()


# ------------------------------------------------- defer / skip / reopen

def _change_status(p, gid: str, new: str, note: str, extra: dict | None = None, audit: str | None = None, **audit_fields):
    gaps = p.gaps()
    g = _get(gaps, gid)
    check_transition(GAP_TRANSITIONS, "Gap", g["status"], new)
    now = ist_now_iso()
    g["history"].append({"from": g["status"], "to": new, "at_ist": now, "note": note})
    g["status"], g["updated_at"] = new, now
    if extra:
        g.update(extra)
    p.save_gaps(gaps, touched=[gid])
    if audit:
        p.audit(audit, gap_id=gid, **audit_fields)
    p.save_state(f"gap-{new.lower()}")
    return g


def defer(p, gid: str, reason: str, owner: str | None = None, revisit_by: str | None = None,
          by_name: str | None = None, by_role: str | None = None) -> dict:
    if by_name or by_role:
        by_name, by_role = require_identity(by_name, by_role, "Person deferring")
    d = {"at_ist": ist_now_iso(), "reason": reason, "owner": owner, "revisit_by": revisit_by, "by_name": by_name, "by_role": by_role}
    g = _change_status(p, gid, "DEFERRED", reason or "deferred", {"deferral": d}, "GAP_DEFERRED", reason=reason, owner=owner,
                       revisit_by=revisit_by)
    return {"gap_id": gid, "status": g["status"], "deferral": d,
            "note": "Deferred is NOT resolved: affected requirements stay blocked under the strict policy."}


def skip(p, gid: str) -> dict:
    gaps = p.gaps()
    g = _get(gaps, gid)
    g["skipped_count"] += 1
    g = _change_status(p, gid, "OPEN", "skipped for now", {"skipped_count": g["skipped_count"]}, "GAP_SKIPPED")
    return {"gap_id": gid, "status": g["status"], "skipped_count": g["skipped_count"]}


def revisit(p, gid: str) -> dict:
    cur = _get(p.gaps(), gid)
    if cur["status"] != "DEFERRED":
        raise BAError("INVALID_TRANSITION", f"Only a DEFERRED gap can be revisited; {gid} is {cur['status']}.")
    require_owner(p, cur)
    g = _change_status(p, gid, "WAITING_FOR_ANSWER", "revisited", {"deferral": None}, "GAP_REVISITED")
    p.reopen_to("WAITING_FOR_GAP_INPUT", "gap-revisit")
    return {"gap_id": gid, "status": g["status"]}


def reopen(p, gid: str, reason: str, by_name: str, by_role: str, statement: str | None = None) -> dict:
    by_name, by_role = require_identity(by_name, by_role, "Person reopening")
    if not normalize_ws(reason):
        raise BAError("BAD_INPUT", "A reason is required.")
    gaps = p.gaps()
    g = _get(gaps, gid)
    if g["status"] != "RESOLVED":
        raise BAError("INVALID_TRANSITION", f"Only a RESOLVED gap can be reopened; {gid} is {g['status']}.")
    did = g["decision_id"]
    snap = {k: g.get(k) for k in ("decision_id", "resolution", "closed_by_name", "closed_by_role", "closed_at_ist",
                                  "closure_summary", "closure_validation", "respondent_name", "respondent_role")}
    g.setdefault("closure_history", []).append(snap)
    for k in snap:
        g[k] = None
    now = ist_now_iso()
    g["history"].append({"from": "RESOLVED", "to": "REOPENED", "at_ist": now, "note": f"{reason} (by {by_name}, {by_role})"})
    g["status"], g["updated_at"] = "REOPENED", now
    append_jsonl(p.path("decisions/decision-log.jsonl"), {"record_type": "decision_status", "decision_id": did, "status": "INVALIDATED",
                                                          "reason": reason, "by": gid, "at_ist": now, "by_name": by_name, "by_role": by_role})
    p.save_gaps(gaps, touched=[gid])
    p.render_decision(did)
    p.audit("GAP_REOPENED", gap_id=gid, decision_id=did, reason=reason, by_name=by_name, by_role=by_role, statement=statement)
    impact = reqs._after_requirement_change(p, set(g["impacted_requirement_ids"]), f"{gid} reopened: {reason}", "gap-reopened")
    p.reopen_to("WAITING_FOR_GAP_INPUT", "gap-reopened")
    return {"gap_id": gid, "status": "REOPENED", "invalidated_decision": did, **impact}


def set_policy(p, accept_carry_forward: bool, by_name: str, by_role: str, statement: str) -> dict:
    by_name, by_role = require_identity(by_name, by_role, "Policy approver")
    if not normalize_ws(statement):
        raise BAError("MISSING_METADATA", "A verbatim statement of acceptance is required.")
    pol = p.state["policy"]
    pol["accept_nonblocking_carry_forward"] = bool(accept_carry_forward)
    pol["accepted_by"] = {"name": by_name, "role": by_role, "statement": statement, "at_ist": ist_now_iso()}
    p.audit("POLICY_CHANGED", accept_nonblocking_carry_forward=bool(accept_carry_forward), by_name=by_name, by_role=by_role)
    p.save_state("policy-updated")
    return {"policy": pol}


def blocked_requirements(p) -> dict[str, list[str]]:
    """requirement_id → blocking gap ids (strict policy: unresolved CRITICAL/HIGH always; others unless carry-forward accepted)."""
    pol = p.state["policy"]
    blocking_prio = set(pol["blocking_priorities"])
    out: dict[str, list[str]] = {}
    for g in p.gaps():
        if g["status"] == "RESOLVED":
            continue
        if g["priority"] in blocking_prio or not pol["accept_nonblocking_carry_forward"]:
            for rid in g["impacted_requirement_ids"]:
                out.setdefault(rid, []).append(g["gap_id"])
    return out


def recover_journals(p) -> list[str]:
    done = []
    for f in sorted(p.path("state/pending").glob("*.closure.json")):
        j = json.loads(f.read_text(encoding="utf-8"))
        apply_closure(p, j)
        done.append(j["gap_id"])
        p.audit("RECOVERY_CLOSURE_ROLLED_FORWARD", gap_id=j["gap_id"], decision_id=j["decision"]["decision_id"])
    return done
