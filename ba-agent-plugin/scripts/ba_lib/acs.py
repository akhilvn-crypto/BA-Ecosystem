"""Acceptance criteria: drafting, review, approval, edit/versioning, defer/revisit. One record per requirement.

The record is authoritative (`acceptance/<folder>/json/`); the enriched copy of the requirement document is a view of the
approved ones. Only an APPROVED record is ever rendered into the document.
"""
from __future__ import annotations

import copy
import re

from . import actrack, docsync, gaps as gapmod, present, reqindex
from .project import AC_TRANSITIONS, PENDING_AC_BUCKETS, ac_fname, check_transition, fault, physical
from .util import BAError, ist_now_iso, normalize_ws, require_identity

GWT = re.compile(r"\bgiven\b.+\bwhen\b.+\bthen\b", re.I | re.S)
EDITABLE = ["acceptance_criteria", "assumptions", "notes", "open_gap_refs"]
EFFECTS = ("NEEDS_CLARIFICATION", "NOT_READY")
READINESS = ("READY_FOR_DEVELOPMENT", "NEEDS_CLARIFICATION", "NOT_READY")
DRAFT_STAGES = ("GAP_ANALYSIS", "WAITING_FOR_GAP_INPUT", "AC_DRAFTING", "WAITING_FOR_AC_REVIEW", "COMPLETED")
ORDER = {"IN_REVIEW": 0, "CANDIDATE": 1, "CHANGES_REQUESTED": 2, "DEFERRED": 3}


def _entry(p, key: str) -> dict:
    return reqindex.by_key(p)[key]


def label(p, key: str) -> str:
    return reqindex.label(p, key)


def open_gap_context(p, keys: list[str]) -> list[dict]:
    """The unresolved gaps that touch these requirements, with what an AC author needs to describe them honestly."""
    out = []
    for g in p.gaps():
        if g["status"] != "RESOLVED" and set(g["impacted_req_keys"]) & set(keys):
            out.append({"gap_id": g["gap_id"], "title": g["title"], "priority": g["priority"], "status": g["status"],
                        "carried_forward": gapmod.is_carried(p, g), "blocks_ac": gapmod.blocks_requirements(p, g),
                        "question": g["question"], "description": g["description"], "why_it_matters": g["why_it_matters"],
                        "evidence": g["evidence"], "affects_requirements": [k for k in g["impacted_req_keys"] if k in keys]})
    return out


def readiness_of(unresolved: list[dict]) -> str:
    if any(u.get("readiness_effect") == "NOT_READY" for u in unresolved):
        return "NOT_READY"
    return "NEEDS_CLARIFICATION" if unresolved else "READY_FOR_DEVELOPMENT"


def _unresolved(p, c: dict, tag: str) -> list[dict]:
    """Validate the declared open gaps against the live unresolved gaps and return them normalised. Every open gap that touches the
    requirement must be named, with a potential impact written by the author; nothing is invented here, and gaps that are no
    longer open must be removed (and their decision applied)."""
    refs = c.get("open_gap_refs")
    if refs is None:
        refs = []
    if not isinstance(refs, list) or not all(isinstance(u, dict) for u in refs):
        raise BAError("BAD_INPUT", f"{tag}: open_gap_refs must be a list of objects {{gap_id, potential_impact, readiness_effect, interim_assumption?}}.")
    gmap = {g["gap_id"]: g for g in p.gaps()}
    live = {g["gap_id"]: g for g in open_gap_context(p, [c["req_key"]])}
    out, seen = [], set()
    for u in refs:
        gid = u.get("gap_id")
        if gid in seen:
            raise BAError("BAD_INPUT", f"{tag}: {gid} is listed twice in open_gap_refs.")
        seen.add(gid)
        if gid in gmap and gmap[gid]["status"] == "RESOLVED":
            raise BAError("OPEN_GAP_RESOLVED", f"{tag}: {gid} is resolved ({gmap[gid]['decision_id']}: {gmap[gid]['resolution']}). "
                          "Remove it from open_gap_refs and apply the decision to the criteria.", gap_id=gid, decision_id=gmap[gid]["decision_id"])
        if gid not in live:
            raise BAError("UNKNOWN_GAP_LINK", f"{tag}: {gid} is not an unresolved gap of requirement {c['req_key']}. "
                          "Only real open gaps can be linked; never invent one.")
        impact = normalize_ws(u.get("potential_impact") or "")
        if len(impact) < 5:
            raise BAError("BAD_INPUT", f"{tag}: {gid} needs a potential_impact: what in these criteria may change or be wrong "
                          "depending on the answer.")
        effect = u.get("readiness_effect") or "NEEDS_CLARIFICATION"
        if effect not in EFFECTS:
            raise BAError("BAD_INPUT", f"{tag}: readiness_effect must be one of {list(EFFECTS)}.")
        out.append({"gap_id": gid, "priority": live[gid]["priority"], "question": live[gid]["question"],
                    "carried_forward": live[gid]["carried_forward"], "potential_impact": impact, "readiness_effect": effect,
                    "interim_assumption": normalize_ws(u.get("interim_assumption") or "") or None})
    missing = [live[g] for g in live if g not in seen]
    if missing:
        raise BAError("OPEN_GAP_MISSING", f"{tag}: the requirement depends on open gap(s) {[m['gap_id'] for m in missing]} but the criteria "
                      "do not declare them. Add one open_gap_refs entry per gap (gap_id, potential_impact, readiness_effect "
                      "NEEDS_CLARIFICATION|NOT_READY, optional interim_assumption); see `ac gaps`.", missing=missing)
    return out


def _check_content(p, c: dict, tag: str = "ac") -> None:
    for f in ("req_key", "acceptance_criteria"):
        if not c.get(f):
            raise BAError("BAD_INPUT", f"{tag}: '{f}' is required.")
    if not isinstance(c["acceptance_criteria"], list) or not all(isinstance(a, str) for a in c["acceptance_criteria"]):
        raise BAError("BAD_INPUT", f"{tag}: acceptance_criteria must be a list of strings.")
    bad = [a for a in c["acceptance_criteria"] if not GWT.search(a)]
    if bad:
        raise BAError("ACCEPTANCE_FORMAT", f"{tag}: every criterion must read 'Given … when … then …'. Offending: {bad[:2]}")
    short = [a for a in c["acceptance_criteria"] if len(normalize_ws(a)) < 10]
    if short:
        raise BAError("ACCEPTANCE_FORMAT", f"{tag}: criterion too short: {short[:1]}")
    norm = [normalize_ws(a).lower() for a in c["acceptance_criteria"]]
    if len(set(norm)) != len(norm):
        raise BAError("DUPLICATE_CRITERION", f"{tag}: the same criterion is listed twice.")
    e = reqindex.by_key(p).get(c["req_key"])
    if not e or e["status"] != "ACTIVE":
        raise BAError("UNKNOWN_REQUIREMENT", f"{tag}: {c['req_key']} is not an active requirement of the supplied documents.")
    blocked = gapmod.blocked_requirements(p).get(c["req_key"])
    if blocked:
        raise BAError("REQUIREMENT_BLOCKED", f"{tag}: cannot write criteria for blocked requirement {label(p, c['req_key'])}; "
                      f"unresolved gaps: {blocked}", blocked={c["req_key"]: blocked})
    _unresolved(p, c, tag)


def _derived(p, key: str) -> dict:
    e = _entry(p, key)
    decs = [d["decision_id"] for d in p.decisions().values()
            if d["current_status"] == "ACTIVE" and key in d.get("related_artifact_ids", [])]
    open_gaps = sorted(g["gap_id"] for g in p.gaps() if g["status"] != "RESOLVED" and key in g["impacted_req_keys"])
    return {"decision_ids": sorted(decs), "open_gap_ids": open_gaps, "requirement_hash": e["material_hash"], "title": e["title"]}


def _clean_list(v, field) -> list[str]:
    if v is None:
        return []
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise BAError("BAD_INPUT", f"'{field}' must be a list of strings.")
    return [normalize_ws(x) for x in v if normalize_ws(x)]


def next_version(p, key: str) -> int:
    """One more than every version this requirement's criteria ever had (live, archived or approved and snapshotted)."""
    v = [0]
    e = actrack.entry(p, key)
    if e:
        v += [x["version"] for x in e["versions"]]
    v += [x["version"] for x in p.archived_acs() if x["req_key"] == key]
    for b, a in p.all_acs():
        if a["req_key"] == key:
            v.append(a["version"])
    return max(v) + 1


def live_record(p, key: str):
    """(bucket, record) of the AC for `key` in a live folder, preferring approved, or None."""
    rows = [(b, a) for b, a in p.all_acs() if a["req_key"] == key]
    rows.sort(key=lambda r: r[0] != "approved")
    return rows[0] if rows else None


def add_acs(p, items: list[dict]) -> dict:
    st = p.state
    if st["stage"] not in DRAFT_STAGES:
        raise BAError("INVALID_STAGE", f"Acceptance criteria cannot be added from stage {st['stage']}.")
    if not st.get("gap_analysis_complete"):
        raise BAError("ANALYSIS_INCOMPLETE", "Finish gap analysis first (`gaps commit --cross-pass-done`): criteria are written only once "
                      "the gaps for a requirement are known.")
    if not isinstance(items, list) or not items:
        raise BAError("BAD_INPUT", "Provide a non-empty JSON list of acceptance-criteria records.")
    built, errors, batch = [], [], set()
    now = ist_now_iso()
    for n, it in enumerate(items):
        tag = f"item[{n}]"
        try:
            if not it.get("req_key"):
                raise BAError("BAD_INPUT", f"{tag}: 'req_key' is required (see `req list`).")
            it = dict(it, req_key=p.req_key(it["req_key"], active_only=True))
            if it.get("unresolved_decisions") and "open_gap_refs" not in it:
                it["open_gap_refs"] = it["unresolved_decisions"]
            key = it["req_key"]
            if key in batch or live_record(p, key):
                raise BAError("DUPLICATE_AC", f"{tag}: {label(p, key)} already has acceptance criteria. Change them with `ac edit`.")
            it.setdefault("open_gap_refs", [])
            _check_content(p, it, tag)
            batch.add(key)
            d = _derived(p, key)
            refs = _unresolved(p, it, tag)
            built.append({
                "req_key": key, "requirement_title": d["title"], "version": next_version(p, key), "status": "CANDIDATE",
                "acceptance_criteria": [normalize_ws(a) for a in it["acceptance_criteria"]],
                "decision_ids": d["decision_ids"], "open_gap_refs": refs, "readiness": readiness_of(refs),
                "requirement_hash": d["requirement_hash"], "assumptions": _clean_list(it.get("assumptions"), "assumptions"),
                "notes": it.get("notes") or {}, "impact_review": None, "approval": None, "deferral": None, "supersedes_version": None,
                "review_history": [{"action": "GENERATED", "version": next_version(p, key), "at_ist": now}], "created_at": now, "updated_at": now})
        except BAError as e:
            errors.append(e.message)
    if errors:
        raise BAError("ACS_REJECTED", "No acceptance criteria were saved: " + " | ".join(errors), errors=errors)
    for a in built:
        p.save_ac("candidates", a)
    p.audit("AC_GENERATED", req_keys=[a["req_key"] for a in built])
    if st["stage"] in ("GAP_ANALYSIS", "WAITING_FOR_GAP_INPUT", "COMPLETED"):
        p.set_stage("AC_DRAFTING", "ac-generated")
    else:
        p.save_state("ac-generated")
    hints = {a["req_key"]: _entry(p, a["req_key"])["existing_ac_hint"] for a in built if _entry(p, a["req_key"])["existing_ac_hint"]}
    return {"created": [a["req_key"] for a in built],
            "with_open_gaps": [a["req_key"] for a in built if a["open_gap_refs"]],
            "needs_clarification": [a["req_key"] for a in built if a["readiness"] == "NEEDS_CLARIFICATION"],
            "not_ready": [a["req_key"] for a in built if a["readiness"] == "NOT_READY"],
            "document_already_states": hints or None}


def gap_context(p, refs: list[str]) -> dict:
    """`ac gaps`: what to declare in open_gap_refs for criteria on these requirements (and each one's live status)."""
    keys = [p.req_key(r, active_only=True) for r in refs] or [e["req_key"] for e in p.requirements()]
    ctx = open_gap_context(p, keys)
    blocked = gapmod.blocked_requirements(p)
    return {"requirements": [{"req_key": k, "label": label(p, k), "status": docsync.requirement_status(p, k, blocked),
                              "document_already_states": _entry(p, k)["existing_ac_hint"] or None} for k in keys],
            "open_gaps": ctx,
            "rule": "Declare every gap below in the criteria's open_gap_refs with a potential_impact you can support from the gap and the "
                    "requirement text. Do not invent requirements; a working assumption must be labelled interim_assumption and is never "
                    "an approved decision. readiness_effect NOT_READY = the core behaviour cannot be built until the gap is answered; "
                    "NEEDS_CLARIFICATION = buildable, details may change. A BLOCKED requirement gets no criteria until its blocking gaps "
                    "are resolved.",
            "blocking_gaps_present": [g["gap_id"] for g in ctx if g["blocks_ac"]]}


def _load(p, ref: str, buckets=("candidates", "unapproved", "deferred", "approved")) -> tuple[str, dict]:
    key = reqindex.resolve(p, ref)
    name = ac_fname(key)
    for b in buckets:
        if p.exists(f"acceptance/{b}/{name}.json"):
            return b, p.read_json(f"acceptance/{b}/{name}.json")
    raise BAError("UNKNOWN_AC", f"There are no acceptance criteria for {label(p, key)} in {list(buckets)}.")


def _hist(a, action, version, note=None, **extra):
    a["review_history"].append({"action": action, "version": version, "at_ist": ist_now_iso(), "note": note, **extra})
    a["updated_at"] = ist_now_iso()


def preflight(p) -> list[dict]:
    """Problems that stop EVERY record from being approved (a change in the requirements nobody has reviewed yet). Reported once,
    with the step that clears them, instead of being repeated on each record."""
    from . import changes
    ch = changes.summary(p)
    out = []
    if ch["unreviewed"] or ch["undetected"]["source"]:
        out.append({"id": "source_change", "problem": "a change in the requirement documents is awaiting review",
                    "action": "run `/ba-agent:changes`."})
    return out


def _ac_blockers(p, a: dict) -> list[str]:
    """Why THIS record cannot be approved as it stands (the record-specific rules `approve` enforces)."""
    out = []
    if (a.get("impact_review") or {}).get("pending"):
        out.append(f"flagged for impact review: {a['impact_review'].get('reason')}")
    e = reqindex.by_key(p).get(a["req_key"])
    if not e or e["status"] != "ACTIVE":
        out.append("the requirement was removed from the source document")
    elif e["material_hash"] != a["requirement_hash"]:
        out.append("requirement text changed since this version was written (edit the criteria or confirm they are unaffected)")
    live = _derived(p, a["req_key"]) if e and e["status"] == "ACTIVE" else {"open_gap_ids": []}
    gmap = {g["gap_id"]: g for g in p.gaps()}
    hard = [g for g in live["open_gap_ids"] if gapmod.blocks_requirements(p, gmap[g])]
    if hard:
        out.append(f"blocked by unresolved gap(s): {', '.join(hard)}")
    return out


def _pending(p) -> list[tuple[str, dict]]:
    rows = [(b, a) for b, a in p.all_acs() if b in PENDING_AC_BUCKETS]
    rows.sort(key=lambda r: (ORDER.get(r[1]["status"], 9), r[1]["req_key"]))
    return rows


def triage(p) -> dict:
    """`ac triage`: every record that is not yet approved - pending review, in review, not approved, deferred - with what stops it
    being approved right now. Read-only: nothing is presented, moved or re-versioned. Problems shared by all records come back once
    as `preflight`; a row lists only its own blockers."""
    pre = preflight(p)
    rows = []
    for bucket, a in _pending(p):
        blockers = _ac_blockers(p, a)
        last = next((h for h in reversed(a["review_history"]) if h["action"] in ("CHANGES_REQUESTED", "DEFERRED")), None)
        rows.append({
            "req_key": a["req_key"], "label": label(p, a["req_key"]), "version": a["version"], "title": a.get("requirement_title"),
            "status": a["status"], "bucket": bucket, "readiness": a.get("readiness"),
            "open_gap_ids": _derived(p, a["req_key"])["open_gap_ids"] if a["req_key"] in reqindex.by_key(p) and _entry(p, a["req_key"])["status"] == "ACTIVE" else [],
            "deferral": a.get("deferral") if a["status"] == "DEFERRED" else None,
            "last_review_note": last and {"action": last["action"], "note": last.get("note"), "at_ist": last["at_ist"]},
            "blockers": blockers, "eligible": not blockers and not pre})
    counts = {k: sum(1 for r in rows if r["bucket"] == b) for k, b in (("pending_review", "candidates"),
              ("not_approved", "unapproved"), ("deferred", "deferred"))}
    summary = None
    if rows:
        summary = (f"{len(rows)} record(s): " + " · ".join(f"{n} {w}" for w, n in (("pending review", counts["pending_review"]),
                   ("not approved", counts["not_approved"]), ("deferred", counts["deferred"])) if n)
                   + (". Clear first: " + "; ".join(x["problem"] for x in pre) if pre else "")
                   + ". Order: " + ", ".join(r["label"] for r in rows[:15]) + (f", +{len(rows) - 15} more" if len(rows) > 15 else ""))
    return {"acs": rows, "total": len(rows), "counts": counts, "summary": summary, "preflight": pre,
            "eligible": [r["req_key"] for r in rows if r["eligible"]],
            "not_eligible": [r["req_key"] for r in rows if not r["eligible"]],
            "eligible_after_preflight": [r["req_key"] for r in rows if not r["blockers"]] if pre else [],
            "next": ("No unapproved acceptance criteria." if not rows else
                     "Clear `preflight` first (one dialog), then present the records one at a time: `ac next --key <key>`." if pre else
                     "Present the records one at a time in this order: `ac next --key <key>`.")}


def _update_context(p, a: dict) -> list[dict]:
    """For a record flagged because a gap it names was resolved: the decision to apply (right information, only when needed)."""
    gmap = {g["gap_id"]: g for g in p.gaps()}
    decs = p.decisions()
    out = []
    for u in a.get("open_gap_refs") or []:
        g = gmap.get(u["gap_id"])
        if g and g["status"] == "RESOLVED" and g.get("decision_id") in decs:
            out.append({"gap_id": g["gap_id"], "decision_id": g["decision_id"],
                        "interpretation": decs[g["decision_id"]]["validated_interpretation"]})
    return out


def next_ac(p, ref: str | None = None, deferred: bool = False, batch: int = 1, unapproved: bool = False, full: bool = False,
            fallback: bool = False) -> dict:
    buckets = ("deferred",) if deferred else ("unapproved",) if unapproved else ("candidates",)
    pool = [(b, a) for b, a in p.all_acs() if b in buckets]
    position = None
    if ref:
        pool = [_load(p, ref, PENDING_AC_BUCKETS + ("approved",))]
        order = [x["req_key"] for _, x in _pending(p)]
        if pool[0][1]["req_key"] in order:
            position = f"{order.index(pool[0][1]['req_key']) + 1} of {len(order)}"
    elif unapproved and not pool:
        return {"acs": [], "remaining": 0, "note": "No unapproved acceptance criteria."}
    pool.sort(key=lambda r: (ORDER.get(r[1]["status"], 9), r[1]["req_key"]))
    chosen = pool[:max(1, batch)]
    if not chosen:
        return {"acs": [], "remaining": 0, "note": "No acceptance criteria awaiting review."}
    for _, a in chosen:
        if a["status"] == "CANDIDATE":
            check_transition(AC_TRANSITIONS, "Acceptance criteria", "CANDIDATE", "IN_REVIEW")
            a["status"] = "IN_REVIEW"
            _hist(a, "PRESENTED", a["version"])
            p.save_ac("candidates", a)
    if p.state["stage"] == "AC_DRAFTING":
        p.set_stage("WAITING_FOR_AC_REVIEW", "ac-review-started")
    pre = preflight(p)
    out = []
    live_keys = reqindex.by_key(p)
    for i, (bucket, a) in enumerate(chosen):
        key = a["req_key"]
        e = live_keys.get(key)
        active = bool(e and e["status"] == "ACTIVE")
        derived = _derived(p, key) if active else {"open_gap_ids": []}
        blockers = ([x["problem"] for x in pre] + _ac_blockers(p, a)) if bucket != "approved" else []
        upd = _update_context(p, a) if (a.get("impact_review") or {}).get("pending") else []
        pos = position or (f"{i + 1} of {len(chosen)}" if len(chosen) > 1 else None)
        view = present.ac_view(a, {"label": label(p, key), "req_text": reqindex.text(p, e) if active else None,
                                   "source_ref": f"{e['file']} lines {e['line_start']}–{e['line_end']}" if active else None,
                                   "blockers": blockers, "bucket": bucket, "live_gaps": derived["open_gap_ids"], "update_ctx": upd,
                                   "position": pos, "fallback": fallback})
        item = {"req_key": key, "version": a["version"], "status": a["status"], "bucket": bucket, "readiness": a.get("readiness"),
                "eligible": not blockers, "blockers": blockers, "present": view}
        if full:
            item["ac"] = dict(a, live_open_gap_ids=derived["open_gap_ids"], requirement_text=reqindex.text(p, e) if active else None)
        out.append(item)
    return {"acs": out, "remaining": len(pool) - len(chosen)}


def request_changes(p, ref: str, version: int, note: str) -> dict:
    b, a = _load(p, ref, ("candidates",))
    if a["version"] != version:
        raise BAError("VERSION_MISMATCH", f"{label(p, a['req_key'])} is at v{a['version']}.")
    check_transition(AC_TRANSITIONS, "Acceptance criteria", a["status"], "CHANGES_REQUESTED")
    a["status"] = "CHANGES_REQUESTED"
    _hist(a, "CHANGES_REQUESTED", version, note)
    p.save_ac("unapproved", a)
    p.remove_ac_files("candidates", a["req_key"])
    p.audit("AC_CHANGES_REQUESTED", req_key=a["req_key"], version=version, note=note)
    p.save_state(f"ac-{ac_fname(a['req_key'])}-unapproved")
    name = ac_fname(a["req_key"])
    return {"req_key": a["req_key"], "status": a["status"], "location": physical(f"acceptance/unapproved/{name}.md"),
            "saved_to": str(p.path(f"acceptance/unapproved/{name}.md"))}


def approve(p, ref: str, version: int, statement: str, approver_name: str | None = None, approver_role: str | None = None) -> dict:
    now = ist_now_iso()
    bucket, a = _load(p, ref, ("candidates", "unapproved", "deferred"))
    key, lab = a["req_key"], label(p, a["req_key"])
    if bucket == "deferred":
        raise BAError("AC_DEFERRED", f"{lab} is deferred. Revisit it first (`ac revisit`) so it is reviewed again.")
    if bucket == "unapproved":
        raise BAError("AC_UNAPPROVED", f"{lab} was marked not approved. Apply the requested changes with `ac edit` (new version) and "
                      "approve that version.")
    if a["version"] != version:
        raise BAError("VERSION_MISMATCH", f"Approval must name the exact version under review; {lab} is at v{a['version']}.")
    check_transition(AC_TRANSITIONS, "Acceptance criteria", a["status"], "APPROVED")
    if not normalize_ws(statement or ""):
        raise BAError("APPROVAL_EVIDENCE_REQUIRED", "Record the human's explicit approval wording (--statement). "
                      "Silence, timeouts and quality scores never approve acceptance criteria.")
    if approver_name or approver_role:
        approver_name, approver_role = require_identity(approver_name, approver_role, "Approver")
    if (a.get("impact_review") or {}).get("pending"):
        raise BAError("IMPACT_REVIEW_PENDING", f"{lab} is flagged for impact review: {a['impact_review']['reason']}. "
                      "Edit it (new version) or confirm it is unaffected.")
    e = reqindex.by_key(p).get(key)
    if not e or e["status"] != "ACTIVE":
        raise BAError("REQUIREMENT_REMOVED", f"{lab} was removed from the source document.")
    if e["material_hash"] != a["requirement_hash"]:
        raise BAError("REQUIREMENT_CHANGED", f"{lab}: the requirement text changed since these criteria were written. Edit them "
                      "(`ac edit`) or confirm they are unaffected (`ac confirm-unaffected`).")
    _check_content(p, a, lab)
    live = _derived(p, key)
    a["decision_ids"] = live["decision_ids"]
    a["readiness"] = readiness_of(a["open_gap_refs"])
    approval = {"version": version, "decision": "APPROVED", "at_ist": now, "approver_name": approver_name,
                "approver_role": approver_role, "statement": statement.strip()}
    a["status"], a["approval"] = "APPROVED", approval
    _hist(a, "APPROVED", version, statement.strip(), approver_name=approver_name)
    prev = p.read_json(f"acceptance/approved/{ac_fname(key)}.json") if p.exists(f"acceptance/approved/{ac_fname(key)}.json") else None
    if prev and prev["version"] != version:
        p.archive_ac(prev)
    fault("ac_after_archive")
    p.save_ac("approved", a)
    fault("ac_after_approved_write")
    p.audit("AC_APPROVED", req_key=key, version=version, approver_name=approver_name, approver_role=approver_role,
            superseded_version=prev["version"] if prev else None, statement=statement.strip(),
            material_hash=actrack.material_hash(a), content_hash=actrack.content_hash(a))
    actrack.record_approved(p, a)  # hashes + an immutable copy of exactly what was approved, so later edits are detected
    p.remove_ac_files("candidates", key)
    p.save_state(f"ac-{ac_fname(key)}-approved")
    name = ac_fname(key)
    return {"req_key": key, "version": version, "status": "APPROVED", "approved_at_ist": now, "readiness": a["readiness"],
            "open_gap_ids": live["open_gap_ids"], "superseded_version": prev["version"] if prev else None,
            "location": physical(f"acceptance/approved/{name}.md"), "saved_to": str(p.path(f"acceptance/approved/{name}.md")),
            "enriched_copy": str(p.enriched_root / e["file"])}


def edit(p, ref: str, changes: dict, reason: str) -> dict:
    if not normalize_ws(reason or ""):
        raise BAError("BAD_INPUT", "State why the acceptance criteria are being changed.")
    bad = [k for k in changes if k not in EDITABLE]
    if bad or not changes:
        raise BAError("BAD_INPUT", f"Provide changes using only {EDITABLE}.")
    bucket, base = _load(p, ref)
    key = base["req_key"]
    new = copy.deepcopy(base)
    for k, v in changes.items():
        new[k] = _clean_list(v, k) if k == "assumptions" else v
    _check_content(p, new, label(p, key))
    new["open_gap_refs"] = _unresolved(p, new, label(p, key))
    new["readiness"] = readiness_of(new["open_gap_refs"])
    d = _derived(p, key)
    new["acceptance_criteria"] = [normalize_ws(x) for x in new["acceptance_criteria"]]
    if (bucket == "approved" and base["status"] == "APPROVED" and not (base.get("impact_review") or {}).get("pending")
            and actrack.material_hash(new) == actrack.material_hash(base) and d["requirement_hash"] == base["requirement_hash"]):
        return _edit_formatting_only(p, base, new, changes, reason)  # same meaning: the approval is not disturbed
    nv = next_version(p, key)
    new.update({"version": nv, "status": "CANDIDATE", "approval": None, "deferral": None, "impact_review": None,
                "supersedes_version": base["version"], "decision_ids": d["decision_ids"], "requirement_hash": d["requirement_hash"],
                "requirement_title": d["title"]})
    _hist(new, "EDITED", nv, reason, from_version=base["version"])
    if bucket in ("candidates", "unapproved", "deferred"):
        p.archive_ac(base)
        if bucket != "candidates":
            p.remove_ac_files(bucket, key)
    p.save_ac("candidates", new)
    p.audit("AC_EDITED", req_key=key, from_version=base["version"], new_version=nv, reason=reason,
            previous_status=base["status"], requires_reapproval=True)
    if bucket == "approved":
        from . import changes as changemod
        changemod.record_agent_ac_edit(p, base, new, reason)
    if p.state["stage"] == "COMPLETED":
        p.reopen_to("WAITING_FOR_AC_REVIEW", "ac-edited")
    p.save_state(f"ac-{ac_fname(key)}-edited")
    return {"req_key": key, "new_version": nv, "status": "CANDIDATE", "requires_approval": True, "readiness": new["readiness"],
            "open_gap_ids": d["open_gap_ids"],
            "approved_version_still_in_force": bucket == "approved" or p.exists(f"acceptance/approved/{ac_fname(key)}.json")}


def _edit_formatting_only(p, base: dict, new: dict, changes: dict, reason: str) -> dict:
    """An edit to an APPROVED record that changes wording layout but not meaning: keep the version and the approval."""
    from . import changes as changemod
    key = base["req_key"]
    if actrack.entry(p, key) is None:
        actrack.record_approved(p, base, event="ADOPTED")
    fmt = copy.deepcopy(base)
    for k in changes:
        fmt[k] = new[k]
    _hist(fmt, "FORMATTING_UPDATED", fmt["version"], reason)
    p.save_ac("approved", fmt)
    actrack.update_version(p, key, fmt["version"], accepted_content_hash=actrack.content_hash(fmt))
    p.audit("AC_FORMATTING_UPDATED", req_key=key, version=fmt["version"], reason=reason, approval_preserved=True)
    changemod.record_agent_ac_edit(p, base, fmt, reason, formatting_only=True)
    p.save_state(f"ac-{ac_fname(key)}-formatting-updated")
    return {"req_key": key, "new_version": fmt["version"], "status": "APPROVED", "formatting_only": True, "requires_approval": False,
            "readiness": fmt.get("readiness"), "approved_version_still_in_force": True,
            "note": "The edit changes layout / wording style only, not the meaning, so the approval is unchanged (recorded in the audit log)."}


def defer(p, ref: str, version: int, reason: str | None = None) -> dict:
    bucket, a = _load(p, ref, ("candidates", "unapproved"))
    if a["version"] != version:
        raise BAError("VERSION_MISMATCH", f"{label(p, a['req_key'])} is at v{a['version']}.")
    check_transition(AC_TRANSITIONS, "Acceptance criteria", a["status"], "DEFERRED")
    prev_status = a["status"]
    a["status"], a["deferral"] = "DEFERRED", {"at_ist": ist_now_iso(), "reason": reason}
    _hist(a, "DEFERRED", version, reason)
    p.save_ac("deferred", a)
    p.remove_ac_files(bucket, a["req_key"])
    p.audit("AC_DEFERRED", req_key=a["req_key"], version=version, reason=reason, from_status=prev_status)
    p.save_state(f"ac-{ac_fname(a['req_key'])}-deferred")
    return {"req_key": a["req_key"], "status": "DEFERRED"}


def revisit(p, ref: str) -> dict:
    """Bring a deferred - or a not-approved - record back for review at the same version (it is not approved by this)."""
    bucket, a = _load(p, ref, ("deferred", "unapproved"))
    prev_status = a["status"]
    check_transition(AC_TRANSITIONS, "Acceptance criteria", prev_status, "CANDIDATE")
    a["status"] = "CANDIDATE"
    _hist(a, "REVISITED", a["version"], from_status=prev_status)
    p.save_ac("candidates", a)
    p.remove_ac_files(bucket, a["req_key"])
    p.audit("AC_REVISITED", req_key=a["req_key"], version=a["version"], from_status=prev_status)
    if p.state["stage"] == "COMPLETED":
        p.reopen_to("WAITING_FOR_AC_REVIEW", "ac-revisited")
    p.save_state(f"ac-{ac_fname(a['req_key'])}-revisited")
    return {"req_key": a["req_key"], "status": "CANDIDATE"}


def keep(p, ref: str) -> dict:
    """Triage: leave a not-yet-approved record exactly as it is (it stays available for later review); only the decision is audited."""
    bucket, a = _load(p, ref, PENDING_AC_BUCKETS)
    loc = physical(f"acceptance/{bucket}/{ac_fname(a['req_key'])}.md")
    p.audit("AC_TRIAGE_KEPT", req_key=a["req_key"], version=a["version"], status=a["status"], location=loc)
    return {"req_key": a["req_key"], "version": a["version"], "status": a["status"], "location": loc,
            "note": "Left as it is; it stays available for later review. Nothing was approved."}


def confirm_unaffected(p, ref: str, statement: str, by_name: str | None = None, by_role: str | None = None) -> dict:
    bucket, a = _load(p, ref)
    key = a["req_key"]
    if not (a.get("impact_review") or {}).get("pending"):
        raise BAError("NOT_FLAGGED", f"{label(p, key)} has no pending impact review.")
    if not normalize_ws(statement or ""):
        raise BAError("BAD_INPUT", "A confirmation statement is required.")
    if by_name or by_role:
        by_name, by_role = require_identity(by_name, by_role, "Reviewer")
    e = reqindex.by_key(p).get(key)
    if not e or e["status"] != "ACTIVE":
        raise BAError("REQUIREMENT_REMOVED", f"{label(p, key)} was removed from the source document; archive the criteria (`ac archive`).")
    _check_content(p, a, label(p, key))
    repinned = e["material_hash"] != a["requirement_hash"]
    a["requirement_hash"] = e["material_hash"]
    a["impact_review"] = None
    _hist(a, "IMPACT_CLEARED", a["version"], statement, by_name=by_name, requirement_repinned=repinned or None)
    p.save_ac(bucket, a)
    if bucket == "approved" and actrack.entry(p, key):
        actrack.update_version(p, key, a["version"], accepted_content_hash=actrack.content_hash(a))
    p.audit("AC_IMPACT_CLEARED", req_key=key, version=a["version"], bucket=bucket, by_name=by_name, by_role=by_role, statement=statement,
            requirement_repinned=repinned)
    return {"req_key": key, "version": a["version"], "impact_review": "cleared", "requirement_text_confirmed_unaffected": repinned}


def archive(p, ref: str, reason: str, by_name: str, by_role: str) -> dict:
    """The requirement is gone from the source document: move its criteria (every version, approved included) to the archive."""
    by_name, by_role = require_identity(by_name, by_role, "Person archiving the criteria")
    if not normalize_ws(reason or ""):
        raise BAError("REASON_REQUIRED", "A reason is required to archive acceptance criteria.")
    key = reqindex.resolve(p, ref)
    if _entry(p, key)["status"] != "REMOVED":
        raise BAError("REQUIREMENT_STILL_PRESENT", f"{label(p, key)} is still in the source document; archive is for removed requirements. "
                      "Use `ac defer` or `ac request-changes` to set criteria aside.")
    moved = []
    for bucket, a in p.all_acs():
        if a["req_key"] != key:
            continue
        _hist(a, "ARCHIVED", a["version"], normalize_ws(reason), by_name=by_name)
        p.archive_ac(a, status="SUPERSEDED")
        p.remove_ac_files(bucket, key)
        moved.append(f"{bucket}:v{a['version']}")
    if not moved:
        raise BAError("NOTHING_TO_ARCHIVE", f"{label(p, key)} has no live acceptance criteria.")
    if actrack.entry(p, key):
        actrack.update_entry(p, key, status=actrack.REMOVAL_CONFIRMED)
    p.audit("AC_ARCHIVED", req_key=key, reason=reason, by_name=by_name, by_role=by_role, moved=moved)
    p.sync_docs()
    p.save_state(f"ac-{ac_fname(key)}-archived")
    return {"req_key": key, "archived": moved}


def ac_states(p) -> dict[str, dict]:
    """req_key -> {'approved': version|None, 'pending': status|None, 'readiness': ...} over the live folders."""
    out: dict[str, dict] = {}
    for b, a in p.all_acs():
        s = out.setdefault(a["req_key"], {"approved": None, "pending": None, "readiness": None, "impact_review": False})
        if b == "approved":
            s["approved"], s["readiness"] = a["version"], a.get("readiness")
        else:
            s["pending"] = a["status"]
        s["impact_review"] = s["impact_review"] or bool((a.get("impact_review") or {}).get("pending"))
    return out
