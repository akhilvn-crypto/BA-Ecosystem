"""Decision owner, gap lifecycle, answer validation, and audited gap closure."""
from __future__ import annotations

import copy
import json
import re
import uuid
from datetime import datetime

from . import conflicts, reqs
from .project import GAP_TRANSITIONS, check_transition, fault
from .util import (BAError, append_jsonl, atomic_write_text, ist_now_iso, normalize_ws, now_ist, read_jsonl,
                   require_identity, same_person)

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

def _check_suggestion(sug, tag: str, reqmap: dict, decisions: dict) -> dict | None:
    """A suggested answer is shown to the human only when it cites the evidence it comes from."""
    if not sug:
        return None
    if not isinstance(sug, dict) or not normalize_ws(sug.get("answer") or "") or not normalize_ws(sug.get("source") or ""):
        raise BAError("BAD_INPUT", f"{tag}: suggested_answer needs 'answer' and 'source' (the BRD section, requirement or decision it "
                      "is based on). Leave it out when there is no supporting evidence.")
    cited = re.findall(r"\b(?:REQ|DEC)-\d{3,}\b", sug["source"])
    bad = [c for c in cited if c not in reqmap and c not in decisions]
    if bad:
        raise BAError("UNKNOWN_REQUIREMENT", f"{tag}: suggested_answer cites unknown artifact(s) {bad}")
    return {"answer": normalize_ws(sug["answer"]), "source": normalize_ws(sug["source"])}


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
            suggestion = _check_suggestion(it.get("suggested_answer"), tag, reqmap, p.decisions())
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
                "closure_validation": None, "suggested_answer": suggestion, "answers": [], "conflicts": [],
                "deferral": None, "deferral_history": [], "carry_forward": None, "carry_forward_history": [], "skipped_count": 0,
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
        for g in built:  # stories that already exist for these requirements must name the new gap
            p.flag_stories(set(g["impacted_requirement_ids"]), f"new gap {g['gap_id']} opened: declare it in the story's "
                           "unresolved decisions and reassess readiness", "gap-opened")
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
    gaps_all = p.gaps()  # re-running the analysis is the reassessment that a requirement change asked for
    cleared = [g["gap_id"] for g in gaps_all if g.get("needs_impact_review")]
    if cleared:
        for g in gaps_all:
            if g["gap_id"] in cleared:
                g["needs_impact_review"], g["updated_at"] = False, ist_now_iso()
        p.save_gaps(gaps_all, touched=cleared)
        p.audit("GAP_IMPACT_CLEARED", gap_ids=cleared, cause="gap-analysis-recommitted")
    unresolved = [g for g in p.gaps() if g["status"] != "RESOLVED"]
    if unresolved and p.state["stage"] == "GAP_ANALYSIS":
        p.set_stage("WAITING_FOR_GAP_INPUT", "gap-analysis-committed")
    else:
        p.save_state("gap-analysis-committed")
    return {"gaps": len(p.gaps()), "unresolved": len(unresolved), "stage": p.state["stage"]}


# -------------------------------------------------------------- triage

def _story_ids_for(p, g: dict) -> list[str]:
    """Stories (any folder) that link to a requirement this gap affects."""
    return sorted({s["story_id"] for _, s in p.all_stories() if set(s["requirement_ids"]) & set(g["impacted_requirement_ids"])})


def _explanation(p, g: dict, reqmap: dict) -> dict:
    """Plain facts behind 'Explain This Question': what is missing, why it matters, what it does to the requirement."""
    blocker = carry_forward_blocker(p, g)
    return {
        "what_we_are_asking": g["question"], "missing_information": g["description"], "evidence": g["evidence"],
        "why_it_matters": g["why_it_matters"], "priority": g["priority"], "priority_rationale": g.get("priority_rationale"),
        "impact_on_requirements": [{"id": r, "title": reqmap[r]["title"], "statement": reqmap[r]["statement"],
                                    "source": reqmap[r]["source_reference"]} for r in g["impacted_requirement_ids"] if r in reqmap],
        "blocks_stories_if_unresolved": blocks_requirements(p, g),
        "if_carried_forward": (None if blocker else
                               "The gap stays OPEN. Stories for the affected requirement(s) can still be written, but each one is linked "
                               "to this gap, names the undecided point and its possible impact, and is flagged NEEDS_CLARIFICATION "
                               "(or NOT_READY) until the gap is answered. When it is answered the stories are updated and reviewed again."),
        "carry_forward_not_available_because": blocker,
    }


def _options(p, g: dict) -> list[str]:
    if g["status"] == "DEFERRED":
        return ["Other (type an answer or an instruction)", "Explain This Question", "Keep Deferred"]
    if g["status"] == "OPEN" and g.get("carry_forward"):
        return ["Other (type an answer or an instruction)", "Explain This Question", "Keep Carried Forward"]
    if carry_forward_blocker(p, g):
        return ["Other (type an answer or an instruction)", "Explain This Question", "Answer Later", "Defer This Gap"]
    opts = ["Suggested Answer"] if g.get("suggested_answer") else []
    return opts + ["Other (type an answer or an instruction)", "Explain This Question", "Carry Forward"]


def _packet(p, g: dict, reqmap: dict) -> dict:
    owner = effective_owner(p, g)
    blocker = carry_forward_blocker(p, g)
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
        "suggested_answer": g.get("suggested_answer"),
        "blocks_stories": blocks_requirements(p, g),
        "carry_forward_allowed": blocker is None, "carry_forward_not_allowed_because": blocker,
        "stories_linked_to_requirements": _story_ids_for(p, g),
        "explain": _explanation(p, g, reqmap),
        "options": _options(p, g),
        "defer_requires_revisit_date": g["priority"] in p.state["policy"].get("deferral_revisit_required_priorities", []),
        "deferral": g.get("deferral"), "carry_forward": g.get("carry_forward"), "skipped_count": g["skipped_count"],
        "revisit_needed_to_answer": g["status"] == "DEFERRED" or bool(g["status"] == "OPEN" and g.get("carry_forward")),
    }


def explain(p, gid: str) -> dict:
    """Explain This Question: a request for help, never an answer. Nothing but an audit event is written."""
    g = _get(p.gaps(), gid)
    reqmap = {r["requirement_id"]: r for r in p.requirements()}
    p.audit("GAP_EXPLAINED", gap_id=gid, priority=g["priority"])
    return {"gap_id": gid, "status": g["status"], **_explanation(p, g, reqmap),
            "next": "Explain in plain words with a real-world example, then show the same options again. This is not an answer."}


def triage(p, status: str | None = None) -> dict:
    """`gaps triage`: every unresolved gap a human can still act on - OPEN (incl. presented, reopened, answer-later and
    carried-forward) and DEFERRED - in the order they should be reviewed. Read-only; nothing is presented or changed."""
    group = (status or "").upper() or None
    if group not in (None, "OPEN", "DEFERRED"):
        raise BAError("BAD_INPUT", "status must be OPEN or DEFERRED.")
    due = {d["gap_id"] for d in due_for_revisit(p)}
    rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    rows = []
    for g in p.gaps():
        if g["status"] == "RESOLVED":
            continue
        kind = "DEFERRED" if g["status"] == "DEFERRED" else "OPEN"
        if group and kind != group:
            continue
        owner = effective_owner(p, g)
        d = g.get("deferral") or {}
        rows.append({
            "gap_id": g["gap_id"], "priority": g["priority"], "status": g["status"], "triage_group": kind,
            "carried_forward": bool(g.get("carry_forward")), "material": bool(g.get("material")), "title": g["title"],
            "question": g["question"], "decision_owner": owner and {"name": owner["name"], "role": owner["role"]},
            "deferral": d and {"reason": d.get("reason"), "by_name": d.get("by_name"), "by_role": d.get("by_role"),
                               "at_ist": d.get("at_ist"), "revisit_by": d.get("revisit_by"), "waiting_on": d.get("owner")},
            "due_for_revisit": g["gap_id"] in due, "skipped_count": g["skipped_count"],
            "requirement_ids": g["impacted_requirement_ids"], "story_ids": _story_ids_for(p, g),
            "blocks_stories": blocks_requirements(p, g)})
    rows.sort(key=lambda r: (rank[r["priority"]], r["triage_group"] != "OPEN", r["gap_id"]))
    return {"gaps": rows, "total": len(rows),
            "counts": {"OPEN": sum(1 for r in rows if r["triage_group"] == "OPEN"),
                       "DEFERRED": sum(1 for r in rows if r["triage_group"] == "DEFERRED"),
                       "carried_forward": sum(1 for r in rows if r["carried_forward"]),
                       "blocking": sum(1 for r in rows if r["blocks_stories"])},
            "decision_owner": p.state.get("decision_owner") and {"name": p.state["decision_owner"]["name"],
                                                                 "role": p.state["decision_owner"]["role"]},
            "next": ("Ask for the decision owner first (`owner set`)." if not p.state.get("decision_owner") else
                     "Present the gaps one at a time in this order: `gaps next --id GAP-00x`.") if rows else "No OPEN or DEFERRED gaps."}


def _present_one(p, gid: str) -> dict:
    """Present one chosen gap. OPEN/REOPENED gaps become WAITING_FOR_ANSWER (as in `gaps next`). DEFERRED and
    carried-forward gaps are shown as they are - looking at them changes nothing; `gap revisit` is run only if the human
    decides to answer."""
    require_owner(p)
    gaps = p.gaps()
    g = _get(gaps, gid)
    if g["status"] == "RESOLVED":
        raise BAError("INVALID_TRANSITION", f"{gid} is already resolved; reopen it (`gap reopen`) if the decision must change.")
    if g["status"] in ("OPEN", "REOPENED"):
        if not g.get("carry_forward"):
            check_transition(GAP_TRANSITIONS, "Gap", g["status"], "WAITING_FOR_ANSWER")
            now = ist_now_iso()
            g["history"].append({"from": g["status"], "to": "WAITING_FOR_ANSWER", "at_ist": now, "note": "presented to user"})
            g["status"], g["updated_at"] = "WAITING_FOR_ANSWER", now
            p.save_gaps(gaps, touched=[gid])
            p.audit("GAP_PRESENTED", gap_ids=[gid])
            if p.state["stage"] == "GAP_ANALYSIS":
                p.set_stage("WAITING_FOR_GAP_INPUT", "gaps-presented")
            elif p.state["stage"] in ("REQUIREMENTS_BASELINED", "STORIES_READY", "WAITING_FOR_STORY_REVIEW", "COMPLETED"):
                p.reopen_to("WAITING_FOR_GAP_INPUT", "gap-revisit")
    reqmap = {r["requirement_id"]: r for r in p.requirements()}
    return _packet(p, _get(p.gaps(), gid), reqmap)


def keep(p, gid: str) -> dict:
    """Keep this gap as it is (triage). OPEN/presented → Answer Later (stays OPEN, asked again); DEFERRED or carried
    forward → unchanged, only the decision to keep it is audited."""
    g = _get(p.gaps(), gid)
    if g["status"] == "DEFERRED" or (g["status"] == "OPEN" and g.get("carry_forward")):
        kind = "DEFERRED" if g["status"] == "DEFERRED" else "CARRIED_FORWARD"
        p.audit("GAP_TRIAGE_KEPT", gap_id=gid, kept_as=kind, priority=g["priority"])
        return {"gap_id": gid, "status": g["status"], "kept_as": kind, "blocks_stories": blocks_requirements(p, g),
                "note": f"Left {kind.replace('_', ' ').lower()}; nothing else changed. It is still unresolved."}
    return answer_later(p, gid)


def next_gaps(p, limit: int = 1, carried: bool = False, gap_id: str | None = None) -> dict:
    """The next gap(s) to ask. Carried-forward gaps are not asked again unless `carried=True` (a deliberate revisit).
    `gap_id` presents one chosen gap instead (used by triage; also reaches DEFERRED gaps without changing them)."""
    if gap_id:
        packet = _present_one(p, gap_id)
        gaps = p.gaps()
        return {"gaps": [packet], "remaining_unpresented": 0,
                "unresolved_total": sum(1 for g in gaps if g["status"] != "RESOLVED"),
                "high_priority_outstanding": [g["gap_id"] for g in gaps if g["status"] != "RESOLVED" and g["priority"] in ("CRITICAL", "HIGH")],
                "carried_forward_open": carried_forward_gaps(p), "deferred_due_for_revisit": due_for_revisit(p)}
    require_owner(p)
    gaps = p.gaps()
    reqmap = {r["requirement_id"]: r for r in p.requirements()}
    rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    if carried:
        cand = [g for g in gaps if g["status"] == "OPEN" and g.get("carry_forward")]
        cand.sort(key=lambda g: (rank[g["priority"]], g["gap_id"]))
        for g in cand[:max(1, limit)]:
            revisit(p, g["gap_id"])
        gaps = p.gaps()
        chosen = [x for x in gaps if x["gap_id"] in [c["gap_id"] for c in cand[:max(1, limit)]]]
        remaining = max(0, len(cand) - len(chosen))
    else:
        cand = [g for g in gaps if g["status"] in ("WAITING_FOR_ANSWER", "OPEN", "REOPENED") and not g.get("carry_forward")]
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
            "unresolved_total": sum(1 for g in gaps if g["status"] != "RESOLVED"),
            "high_priority_outstanding": [g["gap_id"] for g in gaps if g["status"] != "RESOLVED" and g["priority"] in ("CRITICAL", "HIGH")],
            "carried_forward_open": carried_forward_gaps(p),
            "deferred_due_for_revisit": due_for_revisit(p),
            "bulk_carry_forward": _bulk_hint(p, chosen)}


def _bulk_hint(p, chosen: list[dict]) -> dict:
    """Tells the agent whether to offer the one-time 'walk through MEDIUM/LOW gaps, or carry them all forward' question:
    only when the gap being shown is itself eligible and more than one eligible gap is waiting."""
    ids = bulk_carry_forward_candidates(p)
    first_eligible = bool(chosen) and chosen[0]["gap_id"] in ids
    return {"offer_now": first_eligible and len(ids) > 1, "eligible_gap_ids": ids, "count": len(ids)}


def _get(gaps, gid):
    g = next((x for x in gaps if x["gap_id"] == gid), None)
    if not g:
        raise BAError("UNKNOWN_GAP", f"Gap {gid} does not exist.")
    return g


def precheck(p, gap: dict, texts: list[str]) -> list[dict]:
    """Advisory conflict candidates against requirements (business rules included), BRD excerpts, decisions, accepted answers."""
    against = [(r["requirement_id"], r["statement"]) for r in p.requirements() if r["status"] in ACTIVE_REQ]
    against += [(f"{r['requirement_id']}/BRD {r['source_reference']}", r["source_excerpt"]) for r in p.requirements()
                if r["status"] in ACTIVE_REQ and r["requirement_id"] in gap["impacted_requirement_ids"] and r.get("source_excerpt")]
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


def record_answer(p, gap_id: str, response: str | None, respondent_name: str | None = None, respondent_role: str | None = None,
                  use_suggestion: bool = False) -> dict:
    """Store the human's raw answer. `use_suggestion` records the gap's evidence-backed suggested answer as the answer the
    human chose; it is stored and validated exactly like typed text and is never an answer on its own."""
    gaps = p.gaps()
    g = _get(gaps, gap_id)
    require_owner(p, g)
    if g["status"] != "WAITING_FOR_ANSWER":
        raise BAError("NOT_PRESENTED", f"{gap_id} is {g['status']}; present it first (`gaps next`) or revisit it.")
    basis, source = "TYPED", None
    if use_suggestion:
        sug = g.get("suggested_answer")
        if not sug:
            raise BAError("NO_SUGGESTED_ANSWER", f"{gap_id} has no evidence-backed suggested answer; ask the human to type an answer.")
        if normalize_ws(response or ""):
            raise BAError("BAD_INPUT", "Pass either the typed response or --use-suggestion, not both.")
        response, basis, source = sug["answer"], "SUGGESTED_ANSWER", sug["source"]
    if not normalize_ws(response or ""):
        raise BAError("BAD_INPUT", "The response is empty.")
    if respondent_name or respondent_role:
        respondent_name, respondent_role = require_identity(respondent_name, respondent_role, "Respondent")
    now = ist_now_iso()
    aid = f"A{len(g['answers']) + 1}"
    g["answers"].append({"answer_id": aid, "response": response.strip(), "respondent_name": respondent_name,
                         "respondent_role": respondent_role, "received_at_ist": now, "assessment": None, "basis": basis,
                         "suggestion_source": source})
    g["updated_at"] = now
    p.save_gaps(gaps, touched=[gap_id])
    p.audit("GAP_ANSWER_RECEIVED", gap_id=gap_id, answer_id=aid, respondent_name=respondent_name, respondent_role=respondent_role,
            basis=basis, suggestion_source=source)
    cands = precheck(p, g, [response])
    return {"gap_id": gap_id, "answer_id": aid, "received_at_ist": now, "basis": basis, "candidate_conflicts": cands,
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
        if c["status"] != "OPEN":
            continue
        if c["answer_id"] == answer_id:
            c["status"], c["resolved_at_ist"], c["resolution"] = "RESOLVED_BY_CHANGE", now, did
        else:  # an earlier answer was contradictory; this later, conflict-free answer is the clarification
            c["status"], c["resolved_at_ist"], c["resolution"] = "CLARIFIED", now, f"{answer_id} / {did}"
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
    to_update = sorted({s["story_id"] for _, s in p.all_stories() if gap_id in s.get("open_gap_ids", [])})
    return {"gap_id": gap_id, "status": "RESOLVED", "decision_id": did, "closed_at_ist": now,
            "closed_by": {"name": closer_name, "role": closer_role}, "decision_owner": {"name": owner_name, "role": owner_role},
            "reopened_gaps": journal["reopen_gap_ids"], "superseded_decisions": supersede,
            "updated_requirements": {k: v["version"] for k, v in req_after.items()},
            "was_carried_forward": bool(after.get("carry_forward_history")),
            "stories_to_update": to_update,
            "next": ("Refresh the baseline first (`baseline create`, then the human confirms it), then update these stories "
                     "(`story edit`): drop the gap from unresolved_decisions, apply the decision to the acceptance criteria and "
                     "reassess readiness. Each new story version is reviewed again."
                     if to_update else None)}


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
    # stories that listed this gap as an open point must now be updated and reviewed again
    gid = j["gap_id"]
    p.flag_stories(set(), f"{gid} resolved ({did}): remove it from the story's unresolved decisions, apply the decision and "
                   "reassess readiness", "gap-resolved", gap_id=gid)
    if j["gap_after"].get("carry_forward_history"):
        p.mark_baseline_stale(f"carried-forward gap {gid} resolved")
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

def _change_status(p, gid: str, new: str, note: str, event_type: str, extra: dict | None = None, now: str | None = None,
                   **audit_fields):
    """Status change that survives an interruption: the gap record (with its history entry and the id of its audit
    event) is saved first, then the audit event is appended. `recover` re-creates an event that is referenced by a
    history entry but missing from the audit log, so neither the state nor the audit trail is lost."""
    gaps = p.gaps()
    g = _get(gaps, gid)
    check_transition(GAP_TRANSITIONS, "Gap", g["status"], new)
    now = now or ist_now_iso()
    ev = p._event(event_type, gap_id=gid, **audit_fields)
    ev["at_ist"] = now
    g["history"].append({"from": g["status"], "to": new, "at_ist": now, "note": note, "event_id": ev["event_id"],
                         "event_type": event_type})
    if g.get("carry_forward") and new != "OPEN":  # leaving the carried-forward state keeps its record in the history
        g.setdefault("carry_forward_history", []).append(dict(g["carry_forward"], ended_at_ist=now, ended_by=f"{g['status']} → {new}"))
        g["carry_forward"] = None
    g["status"], g["updated_at"] = new, now
    if extra:
        g.update(extra)
    p.save_gaps(gaps, touched=[gid])
    fault("after_gap_status_saved")
    append_jsonl(p.path("state/audit.jsonl"), ev)
    p.save_state(f"gap-{new.lower()}")
    return g


def _revisit_date(p, g: dict, value: str | None) -> str | None:
    value = normalize_ws(value or "") or None
    required = g["priority"] in p.state["policy"].get("deferral_revisit_required_priorities", [])
    if not value:
        if required:
            raise BAError("REVISIT_DATE_REQUIRED", f"Policy requires a revisit date to defer a {g['priority']} gap. Ask the user "
                          "when this should be looked at again (YYYY-MM-DD).", priority=g["priority"])
        return None
    try:
        day = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise BAError("BAD_INPUT", f"revisit date must be YYYY-MM-DD (got {value!r}).")
    if day < now_ist().date():
        raise BAError("BAD_INPUT", f"revisit date {value} is in the past (today is {now_ist().date().isoformat()} IST).")
    return value


def defer(p, gid: str, reason: str, owner: str | None = None, revisit_by: str | None = None,
          by_name: str | None = None, by_role: str | None = None) -> dict:
    """Defer This Gap: a stated reason and a named actor are mandatory; the gap is DEFERRED, never resolved.
    `owner` is who the gap is waiting on (optional); the decision owner is taken from the project."""
    g = _get(p.gaps(), gid)
    if g["status"] == "DEFERRED":
        raise BAError("ALREADY_DEFERRED", f"{gid} is already deferred (reason: {(g.get('deferral') or {}).get('reason')}). "
                      "Revisit it first if the deferral should change.")
    if g["status"] == "RESOLVED":
        raise BAError("INVALID_TRANSITION", f"{gid} is already resolved; there is nothing to defer.")
    reason = normalize_ws(reason or "")
    if not reason:
        raise BAError("REASON_REQUIRED", "A reason is required to defer a gap. Ask the user why it is being deferred.")
    actor_name, actor_role = require_identity(by_name, by_role, "Person deferring the gap")
    dec_owner = require_owner(p, g)
    revisit_by = _revisit_date(p, g, revisit_by)
    now = ist_now_iso()
    d = {"at_ist": now, "timezone": "Asia/Kolkata", "reason": reason, "owner": normalize_ws(owner or "") or None,
         "revisit_by": revisit_by, "by_name": actor_name, "by_role": actor_role,
         "decision_owner_name": dec_owner["name"], "decision_owner_role": dec_owner["role"]}
    g = _change_status(p, gid, "DEFERRED", f"deferred by {actor_name}, {actor_role}: {reason}", "GAP_DEFERRED", {"deferral": d}, now=now,
                       reason=reason, waiting_on=d["owner"], revisit_by=revisit_by, actor={"name": actor_name, "role": actor_role},
                       decision_owner={"name": dec_owner["name"], "role": dec_owner["role"]}, priority=g["priority"])
    blocking = blocks_requirements(p, g)
    return {"gap_id": gid, "status": g["status"], "deferral": d, "blocks_stories": blocking,
            "note": "Deferred is NOT resolved: the gap stays in the outstanding list"
                    + (" and keeps the stories of its requirements blocked." if blocking else ".")}


def answer_later(p, gid: str) -> dict:
    """Answer Later: no reason needed. The gap goes back to OPEN, stays in the outstanding queue and is asked again."""
    g = _get(p.gaps(), gid)
    if g["status"] not in ("WAITING_FOR_ANSWER", "OPEN"):
        raise BAError("INVALID_TRANSITION", f"Only an open or presented gap can be answered later; {gid} is {g['status']}.")
    if g.get("carry_forward"):
        raise BAError("ALREADY_CARRIED_FORWARD", f"{gid} is carried forward and stays OPEN; revisit it (`gap revisit`) to ask it again.")
    count = g["skipped_count"] + 1
    g = _change_status(p, gid, "OPEN", "answer later", "GAP_ANSWER_LATER", {"skipped_count": count}, priority=g["priority"],
                       skipped_count=count)
    return {"gap_id": gid, "status": g["status"], "skipped_count": count, "blocks_stories": blocks_requirements(p, g),
            "note": "Still OPEN and in the outstanding queue; it will be asked again after the other open gaps of this round."}


skip = answer_later  # earlier name of the same action


def carry_forward_blocker(p, g: dict) -> str | None:
    """Why this gap may NOT be carried forward (None = it may). Only non-material MEDIUM/LOW gaps can be carried forward;
    CRITICAL/HIGH and security/compliance/scope-sensitive gaps keep the existing blocking rules."""
    if g["status"] == "RESOLVED":
        return "the gap is already resolved"
    if g["priority"] not in ("MEDIUM", "LOW") or g["priority"] in p.state["policy"]["blocking_priorities"]:
        return f"{g['priority']} gaps always block their stories; answer or formally defer it"
    if g.get("material"):
        return ("the gap is material (security, compliance, scope or policy); existing blocking rules apply, so it must be "
                "resolved or deferred, not carried forward")
    return None


def is_carried(p, g: dict) -> bool:
    return g["status"] == "OPEN" and bool(g.get("carry_forward")) and carry_forward_blocker(p, g) is None


def carried_forward_gaps(p) -> list[dict]:
    return [{"gap_id": g["gap_id"], "priority": g["priority"], "title": g["title"], "requirement_ids": g["impacted_requirement_ids"],
             "since_ist": g["carry_forward"]["at_ist"]}
            for g in p.gaps() if g["status"] == "OPEN" and g.get("carry_forward")]


def carry_forward(p, gid: str, by_name: str | None = None, by_role: str | None = None, reason: str | None = None) -> dict:
    """Carry Forward: the gap stays OPEN (never resolved) and stops blocking story generation. The human who chose it and
    the decision owner are recorded; stories for the affected requirements must name the gap and are flagged for clarification."""
    g = _get(p.gaps(), gid)
    if g["status"] not in ("WAITING_FOR_ANSWER", "OPEN"):
        raise BAError("INVALID_TRANSITION", f"Only an open or presented gap can be carried forward; {gid} is {g['status']}.")
    if g.get("carry_forward"):
        raise BAError("ALREADY_CARRIED_FORWARD", f"{gid} is already carried forward (since {g['carry_forward']['at_ist']}).")
    why = carry_forward_blocker(p, g)
    if why:
        raise BAError("CARRY_FORWARD_NOT_ALLOWED", f"{gid} cannot be carried forward: {why}.", priority=g["priority"],
                      material=bool(g.get("material")))
    actor_name, actor_role = require_identity(by_name, by_role, "Person carrying the gap forward")
    dec_owner = require_owner(p, g)
    now = ist_now_iso()
    cf = {"at_ist": now, "timezone": "Asia/Kolkata", "reason": normalize_ws(reason or "") or None, "by_name": actor_name,
          "by_role": actor_role, "decision_owner_name": dec_owner["name"], "decision_owner_role": dec_owner["role"],
          "priority": g["priority"], "affected_requirement_ids": list(g["impacted_requirement_ids"])}
    stories = _story_ids_for(p, g)
    g = _change_status(p, gid, "OPEN", f"carried forward by {actor_name}, {actor_role}", "GAP_CARRIED_FORWARD", {"carry_forward": cf},
                       now=now, reason=cf["reason"], actor={"name": actor_name, "role": actor_role},
                       decision_owner={"name": dec_owner["name"], "role": dec_owner["role"]}, priority=g["priority"],
                       requirement_ids=cf["affected_requirement_ids"])
    p.mark_baseline_stale(f"{gid} carried forward")
    p.reopen_to("WAITING_FOR_GAP_INPUT", "gap-carried-forward")
    return {"gap_id": gid, "status": g["status"], "carry_forward": cf, "blocks_stories": blocks_requirements(p, g),
            "stories_to_declare_the_gap": stories,
            "note": "The gap is still OPEN, not resolved. Stories for " + ", ".join(cf["affected_requirement_ids"])
                    + " can now be generated; each must name this gap, its possible impact and be flagged NEEDS_CLARIFICATION "
                      "(or NOT_READY). Revisit it any time with `gap revisit`."}


def bulk_carry_forward_candidates(p) -> list[str]:
    """Unresolved, not-yet-carried MEDIUM/LOW non-material gaps that `carry_forward_all` would carry, in review order."""
    rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    cand = [g for g in p.gaps() if g["status"] in ("WAITING_FOR_ANSWER", "OPEN") and not g.get("carry_forward")
            and carry_forward_blocker(p, g) is None]
    cand.sort(key=lambda g: (rank[g["priority"]], g["gap_id"]))
    return [g["gap_id"] for g in cand]


def carry_forward_all(p, by_name: str | None = None, by_role: str | None = None, reason: str | None = None) -> dict:
    """Carry every eligible MEDIUM/LOW non-material gap forward in one step (the human chose not to walk through them).
    Each gap gets its own `carry_forward` record and `GAP_CARRIED_FORWARD` audit event, exactly as `gap carry-forward`.
    CRITICAL/HIGH and material gaps are never touched; they are listed as `not_carried`."""
    actor_name, actor_role = require_identity(by_name, by_role, "Person carrying the gaps forward")
    require_owner(p)
    ids = bulk_carry_forward_candidates(p)
    if not ids:
        raise BAError("NOTHING_TO_CARRY_FORWARD", "No open MEDIUM/LOW non-material gap is left to carry forward.")
    carried = [carry_forward(p, gid, actor_name, actor_role, reason) for gid in ids]
    left = [{"gap_id": g["gap_id"], "priority": g["priority"], "status": g["status"], "why": carry_forward_blocker(p, g)}
            for g in p.gaps() if g["status"] != "RESOLVED" and not g.get("carry_forward") and g["gap_id"] not in ids]
    p.audit("GAP_CARRIED_FORWARD_BULK", gap_ids=ids, by_name=actor_name, by_role=actor_role, reason=normalize_ws(reason or "") or None)
    return {"carried_forward": ids, "count": len(ids), "by": {"name": actor_name, "role": actor_role},
            "stories_to_declare_the_gaps": sorted({s for c in carried for s in c["stories_to_declare_the_gap"]}),
            "not_carried": left,
            "note": "Every listed gap is still OPEN, not resolved. Stories for their requirements can now be generated, each naming "
                    "the gap(s) and flagged NEEDS_CLARIFICATION (or NOT_READY). `not_carried` gaps (CRITICAL/HIGH/material, deferred "
                    "or not yet asked) keep their own rules. Revisit any of them with `gap revisit` / `gaps next --carried`."}


def revisit(p, gid: str) -> dict:
    cur = _get(p.gaps(), gid)
    if cur["status"] == "OPEN" and cur.get("carry_forward"):
        require_owner(p, cur)
        g = _change_status(p, gid, "WAITING_FOR_ANSWER", "carried-forward gap revisited", "GAP_CARRY_FORWARD_REVISITED",
                           priority=cur["priority"], requirement_ids=cur["impacted_requirement_ids"])
        p.mark_baseline_stale(f"{gid} revisited")
        p.reopen_to("WAITING_FOR_GAP_INPUT", "gap-revisit")
        return {"gap_id": gid, "status": g["status"], "blocks_stories": blocks_requirements(p, g),
                "note": "Asked again. Until it is resolved or carried forward again it blocks its stories like any open gap."}
    if cur["status"] != "DEFERRED":
        raise BAError("INVALID_TRANSITION", f"Only a DEFERRED or carried-forward gap can be revisited; {gid} is {cur['status']}.")
    require_owner(p, cur)
    now = ist_now_iso()
    hist = list(cur.get("deferral_history", []))
    if cur.get("deferral"):
        hist.append(dict(cur["deferral"], revisited_at_ist=now))
    g = _change_status(p, gid, "WAITING_FOR_ANSWER", "revisited", "GAP_REVISITED", {"deferral": None, "deferral_history": hist}, now=now)
    p.reopen_to("WAITING_FOR_GAP_INPUT", "gap-revisit")
    return {"gap_id": gid, "status": g["status"]}


def due_for_revisit(p) -> list[dict]:
    """Deferred gaps whose revisit date has arrived (IST date)."""
    today = now_ist().date().isoformat()
    return [{"gap_id": g["gap_id"], "priority": g["priority"], "revisit_by": g["deferral"]["revisit_by"]}
            for g in p.gaps() if g["status"] == "DEFERRED" and (g.get("deferral") or {}).get("revisit_by")
            and g["deferral"]["revisit_by"] <= today]


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


def set_policy(p, accept_carry_forward: bool | None, by_name: str, by_role: str, statement: str,
               deferral_revisit_required: list[str] | None = None) -> dict:
    by_name, by_role = require_identity(by_name, by_role, "Policy approver")
    if not normalize_ws(statement):
        raise BAError("MISSING_METADATA", "A verbatim statement of acceptance is required.")
    if accept_carry_forward is None and deferral_revisit_required is None:
        raise BAError("BAD_INPUT", "Nothing to change: pass --carry-forward and/or --deferral-revisit-required.")
    pol = p.state["policy"]
    changes = {}
    if accept_carry_forward is not None:
        pol["accept_nonblocking_carry_forward"] = changes["accept_nonblocking_carry_forward"] = bool(accept_carry_forward)
    if deferral_revisit_required is not None:
        bad = [x for x in deferral_revisit_required if x not in PRIORITIES]
        if bad:
            raise BAError("BAD_INPUT", f"deferral-revisit-required takes priorities from {PRIORITIES} (got {bad}).")
        pol["deferral_revisit_required_priorities"] = changes["deferral_revisit_required_priorities"] = list(deferral_revisit_required)
    pol["accepted_by"] = {"name": by_name, "role": by_role, "statement": statement, "at_ist": ist_now_iso()}
    p.audit("POLICY_CHANGED", by_name=by_name, by_role=by_role, **changes)
    p.save_state("policy-updated")
    return {"policy": pol}


def blocks_requirements(p, g: dict) -> bool:
    """Does this unresolved gap block the stories of the requirements it affects?
    CRITICAL/HIGH always do, and so does any material (scope/policy/security/compliance) gap; other gaps block unless the
    human carried that gap forward or accepted the project-wide non-blocking policy."""
    if g["status"] == "RESOLVED":
        return False
    if is_carried(p, g):
        return False
    pol = p.state["policy"]
    return g["priority"] in pol["blocking_priorities"] or bool(g.get("material")) or not pol["accept_nonblocking_carry_forward"]


def blocked_requirements(p) -> dict[str, list[str]]:
    """requirement_id → blocking gap ids (see blocks_requirements)."""
    out: dict[str, list[str]] = {}
    for g in p.gaps():
        if blocks_requirements(p, g):
            for rid in g["impacted_requirement_ids"]:
                out.setdefault(rid, []).append(g["gap_id"])
    return out


def open_nonblocking_requirements(p) -> dict[str, list[str]]:
    """requirement_id → unresolved gap ids that do NOT block it (carried forward, or accepted by policy)."""
    out: dict[str, list[str]] = {}
    for g in p.gaps():
        if g["status"] != "RESOLVED" and not blocks_requirements(p, g):
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
