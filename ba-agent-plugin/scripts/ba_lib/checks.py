"""Deterministic validation, traceability, crash recovery, status and completion."""
from __future__ import annotations

import json
from pathlib import Path

from . import gaps as gapmod, schema
from .project import REQ_DIR
from .util import BAError, human_ist, is_ist_iso, ist_now_iso, read_jsonl, normalize_ws

REQ_ACTIVE = ("ACTIVE", "NEEDS_REVIEW")
CLOSURE_FIELDS = ["decision_id", "resolution", "decision_owner_name", "decision_owner_role", "closed_by_name",
                  "closed_by_role", "closed_at_ist", "closure_summary", "closure_validation"]
BUCKET_STATUS = {"candidates": {"CANDIDATE", "IN_REVIEW", "BLOCKED"}, "approved": {"APPROVED"},
                 "unapproved": {"CHANGES_REQUESTED"}, "deferred": {"DEFERRED"}}


def validate_project(p) -> dict:
    E, W = [], []
    st = p.state
    # ---- requirements
    secs = {s["section_id"] for s in (p.read_json("state/pending/source-change.json")["sections"]
                                      if p.exists("state/pending/source-change.json") else p.source_metadata()["sections"])}
    reqs = p.requirements()
    rids = {r["requirement_id"] for r in reqs}
    for r in reqs:
        for e in schema.validate(r, "requirement"):
            E.append(f"{r.get('requirement_id')}: {e}")
        for s in r.get("source_section_ids", []):
            if s not in secs:
                E.append(f"{r['requirement_id']}: source section {s} does not exist in the BRD outline")
        if not r.get("source_excerpt"):
            W.append(f"{r['requirement_id']}: no source excerpt recorded")
        if r["source_fingerprint"] != st["source"]["fingerprint"] and r["status"] == "ACTIVE" and not st["impact_review"].get("pending"):
            E.append(f"{r['requirement_id']}: source fingerprint differs from the project source fingerprint")
        if not p.exists(f"{REQ_DIR}/{r['requirement_id']}.md"):
            W.append(f"{r['requirement_id']}: rendered .md missing")
    # ---- gaps / decisions / audit
    gaps = p.gaps()
    gids = [g["gap_id"] for g in gaps]
    if len(gids) != len(set(gids)):
        E.append("duplicate gap IDs in gap-register.json")
    recs, jerr = read_jsonl(p.path("decisions/decision-log.jsonl"))
    E += jerr
    decs = p.decisions()
    dids = [r["decision_id"] for r in recs if r.get("record_type") == "decision"]
    if len(dids) != len(set(dids)):
        E.append("duplicate decision IDs in decision-log.jsonl (a decision was recorded twice)")
    for d in decs.values():
        for e in schema.validate(d if False else {k: v for k, v in d.items() if k not in ("current_status", "status_reason")}, "decision"):
            E.append(f"{d.get('decision_id')}: {e}")
        if d.get("gap_id") and d["gap_id"] not in gids:
            E.append(f"{d['decision_id']}: refers to unknown gap {d['gap_id']}")
        for s in d.get("supersedes_decision_ids", []):
            if s not in decs:
                E.append(f"{d['decision_id']}: supersedes unknown decision {s}")
    events, aerr = read_jsonl(p.path("state/audit.jsonl"))
    E += aerr
    for ev in events:
        if not (ev.get("event_id") and ev.get("event_type") and is_ist_iso(ev.get("at_ist"))):
            E.append(f"audit event {ev.get('event_id')}: missing id/type or non-IST timestamp")
    closures = {}
    for ev in events:
        if ev.get("event_type") == "GAP_RESOLVED":
            closures.setdefault(ev["gap_id"], []).append(ev)
    for g in gaps:
        for e in schema.validate(g, "gap"):
            E.append(f"{g.get('gap_id')}: {e}")
        for r in g["impacted_requirement_ids"]:
            if r not in rids:
                E.append(f"{g['gap_id']}: impacted requirement {r} does not exist")
        resolved_hist = sum(1 for h in g["history"] if h["to"] == "RESOLVED")
        n_events = len(closures.get(g["gap_id"], []))
        if resolved_hist != n_events:
            E.append(f"{g['gap_id']}: {resolved_hist} RESOLVED transition(s) in state but {n_events} GAP_RESOLVED audit event(s) — "
                     "run `recover` (interrupted closure) or investigate tampering")
        if g["status"] == "RESOLVED":
            missing = [f for f in CLOSURE_FIELDS if not g.get(f)]
            if missing:
                E.append(f"{g['gap_id']}: RESOLVED but missing closure metadata {missing}")
            if g.get("closed_at_ist") and not is_ist_iso(g["closed_at_ist"]):
                E.append(f"{g['gap_id']}: closed_at_ist is not an IST (+05:30) timestamp")
            d = decs.get(g.get("decision_id") or "")
            if not d:
                E.append(f"{g['gap_id']}: closing decision {g.get('decision_id')} not found")
            elif d["current_status"] != "ACTIVE":
                E.append(f"{g['gap_id']}: closing decision {d['decision_id']} is {d['current_status']}; gap should be reopened")
            ev = [e for e in closures.get(g["gap_id"], []) if e.get("decision_id") == g.get("decision_id")]
            if not ev:
                E.append(f"{g['gap_id']}: no GAP_RESOLVED audit event for {g.get('decision_id')}")
            else:
                e = ev[-1]
                if e["closed_at_ist"] != g.get("closed_at_ist") or e["closed_by"]["name"] != g.get("closed_by_name"):
                    E.append(f"{g['gap_id']}: closure audit event disagrees with the gap record")
        elif g.get("closed_at_ist") or g.get("closed_by_name"):
            E.append(f"{g['gap_id']}: status {g['status']} but closure fields are populated")
        if g.get("needs_impact_review"):
            W.append(f"{g['gap_id']}: flagged for impact review")
    for f in p.path("state/pending").glob("*.closure.json"):
        E.append(f"interrupted gap closure journal {f.name} — run `recover`")
    # ---- stories
    seen: dict[str, list[tuple[str, int]]] = {}
    for bucket, s in p.all_stories():
        sid = s.get("story_id")
        for e in schema.validate(s, "story"):
            E.append(f"{sid}: {e}")
        if s.get("status") not in BUCKET_STATUS[bucket]:
            E.append(f"{sid}: status {s.get('status')} is not allowed in stories/{bucket}/")
        if bucket == "approved":
            ap = s.get("approval") or {}
            if ap.get("version") != s.get("version") or ap.get("decision") != "APPROVED" or not is_ist_iso(ap.get("at_ist")):
                E.append(f"{sid}: approved record lacks a valid approval for version {s.get('version')}")
            if not any(e.get("event_type") == "STORY_APPROVED" and e.get("story_id") == sid and e.get("version") == s["version"] for e in events):
                E.append(f"{sid}: v{s['version']} is in approved/ but has no STORY_APPROVED audit event")
        for r in s["requirement_ids"]:
            if r not in rids:
                E.append(f"{sid}: linked requirement {r} does not exist")
        for d in s["decision_ids"]:
            if d not in decs:
                E.append(f"{sid}: linked decision {d} does not exist")
        seen.setdefault(sid, []).append((bucket, s["version"]))
        if (s.get("impact_review") or {}).get("pending"):
            W.append(f"{sid} v{s['version']}: impact review pending ({s['impact_review']['reason']})")
    for sid, locs in seen.items():
        if len({v for b, v in locs if b != "approved"}) < len([1 for b, v in locs if b != "approved"]):
            E.append(f"{sid}: present in multiple non-approved folders")
        for b, v in locs:
            if b != "approved" and ("approved", v) in locs:
                E.append(f"{sid}: v{v} is in both approved/ and {b}/ — interrupted approval; run `recover`")
    # ---- baseline & source
    m = p.baseline_manifest()
    for v in m["versions"]:
        if not p.exists(f"baselines/baseline-v{v['version']}.json"):
            E.append(f"baseline v{v['version']} file missing")
    if st.get("baseline_version") is not None and not any(v["version"] == st["baseline_version"] and v["status"] == "APPROVED" for v in m["versions"]):
        E.append("workflow baseline_version does not point at an APPROVED baseline")
    if m.get("stale"):
        W.append(f"baseline is stale: {m.get('stale_reason')}")
    src = Path(st["source"]["path"])
    if not src.exists():
        W.append(f"BRD file not found at {src}")
    else:
        try:
            from . import ingest
            if ingest.extract(src)["fingerprint"] != st["source"]["fingerprint"] and not st["impact_review"].get("pending"):
                W.append("the BRD has changed since analysis — run `source check`")
        except BAError as e:
            W.append(f"BRD could not be re-read: {e.message}")
    sm = p.source_metadata()
    if sm.get("injection_suspects"):
        W.append(f"{len(sm['injection_suspects'])} instruction-like line(s) in the BRD are treated as data only")
    expect = sorted(g["gap_id"] for g in gaps if g["status"] != "RESOLVED")
    if sorted(st["pending_gap_ids"]) != expect:
        W.append("workflow.json pending_gap_ids is out of date (fixed on next save)")
    return {"ok": not E, "errors": E, "warnings": W,
            "counts": {"requirements": len(reqs), "gaps": len(gaps), "decisions": len(decs), "audit_events": len(events)}}


def traceability(p, write: bool = False) -> dict:
    reqs = [r for r in p.requirements() if r["status"] in REQ_ACTIVE]
    rids = {r["requirement_id"] for r in p.requirements()}
    active = {r["requirement_id"] for r in reqs}
    base = p.approved_baseline()
    ready = {e["requirement_id"] for e in base["requirements"] if e["baseline_status"] == "READY"} if base else set()
    cover = {r: {"approved": [], "candidate": [], "deferred": []} for r in active}
    orphans, dangling, dupes, titles = [], [], [], {}
    for bucket, s in p.all_stories():
        key = {"approved": "approved", "candidates": "candidate", "unapproved": "candidate", "deferred": "deferred"}[bucket]
        valid = [r for r in s["requirement_ids"] if r in active]
        for r in s["requirement_ids"]:
            if r not in rids:
                dangling.append({"from": s["story_id"], "to": r})
            elif r not in active:
                dangling.append({"from": s["story_id"], "to": r, "note": "requirement is not active"})
        if not valid:
            orphans.append(s["story_id"])
        for r in valid:
            cover[r][key].append(s["story_id"])
        t = normalize_ws(s["title"]).lower()
        if t in titles and titles[t] != s["story_id"]:
            dupes.append([titles[t], s["story_id"]])
        titles.setdefault(t, s["story_id"])
    for g in p.gaps():
        for r in g["impacted_requirement_ids"]:
            if r not in rids:
                dangling.append({"from": g["gap_id"], "to": r})
    uncovered = sorted(r for r in ready if not any(cover[r].values()))
    without_approved = sorted(r for r in ready if not cover[r]["approved"])
    blocked = sorted(active - ready) if base else sorted(active)
    out = {"requirements_active": len(active), "ready_in_baseline": len(ready), "blocked_or_not_baselined": blocked,
           "uncovered_by_any_story": uncovered, "not_covered_by_approved_story": without_approved,
           "orphan_stories": sorted(set(orphans)), "dangling_references": dangling, "duplicate_stories": dupes,
           "coverage": {r: v for r, v in sorted(cover.items())}}
    out["ok"] = not (orphans or dangling or dupes)
    if write:
        lines = ["# Traceability report", "", f"Generated {human_ist(ist_now_iso())}", "",
                 f"- Active requirements: {len(active)} · ready in baseline: {len(ready)}",
                 f"- Uncovered by any story: {', '.join(uncovered) or 'none'}",
                 f"- Not yet covered by an approved story: {', '.join(without_approved) or 'none'}",
                 f"- Blocked / not baselined: {', '.join(blocked) or 'none'}",
                 f"- Orphan stories: {', '.join(sorted(set(orphans))) or 'none'}",
                 f"- Dangling references: {json.dumps(dangling) if dangling else 'none'}",
                 f"- Duplicate stories: {dupes or 'none'}", "", "## Requirement → story coverage",
                 "| Requirement | Approved | Candidate | Deferred |", "|---|---|---|---|"]
        for r, v in sorted(cover.items()):
            lines.append(f"| {r} | {', '.join(v['approved']) or '—'} | {', '.join(v['candidate']) or '—'} | {', '.join(v['deferred']) or '—'} |")
        p.write_text("reports/traceability-report.md", "\n".join(lines) + "\n")
    return out


def recover(p) -> dict:
    actions = gapmod.recover_journals(p)
    out = {"closures_rolled_forward": actions, "approvals_repaired": [], "gaps_reopened": [], "audit_backfilled": []}
    events = p.audit_events()
    for sid_path in sorted(p.path("stories/approved").glob("US-*.json")):
        a = json.loads(sid_path.read_text(encoding="utf-8"))
        cand = p.path(f"stories/candidates/{a['story_id']}.json")
        if cand.exists() and json.loads(cand.read_text(encoding="utf-8"))["version"] == a["version"]:
            p.remove_story_files("candidates", a["story_id"])
            out["approvals_repaired"].append(a["story_id"])
        if not any(e.get("event_type") == "STORY_APPROVED" and e.get("story_id") == a["story_id"] and e.get("version") == a["version"] for e in events):
            ap = a.get("approval") or {}
            p.audit("STORY_APPROVED", story_id=a["story_id"], version=a["version"], approver_name=ap.get("approver_name"),
                    approver_role=ap.get("approver_role"), statement=ap.get("statement"), recovered=True,
                    original_approval_at_ist=ap.get("at_ist"))
            out["audit_backfilled"].append(a["story_id"])
    gaps = p.gaps()
    events = p.audit_events()
    decs = p.decisions()
    touched = []
    for g in gaps:
        if g["status"] == "RESOLVED":
            ok = (all(g.get(f) for f in CLOSURE_FIELDS) and g["decision_id"] in decs
                  and any(e.get("event_type") == "GAP_RESOLVED" and e.get("gap_id") == g["gap_id"] and e.get("decision_id") == g["decision_id"] for e in events))
            if not ok:
                g["status"] = "REOPENED"
                g["history"].append({"from": "RESOLVED", "to": "REOPENED", "at_ist": ist_now_iso(),
                                     "note": "recovery: closure metadata/audit trail incomplete"})
                touched.append(g["gap_id"])
    if touched:
        p.save_gaps(gaps, touched=touched)
        for t in touched:
            p.audit("RECOVERY_GAP_REOPENED", gap_id=t, reason="closure metadata or audit event missing")
        out["gaps_reopened"] = touched
    p.save_state("recovered")
    return out


# ------------------------------------------------------------------ status

def _counts(items, key):
    out = {}
    for i in items:
        out[i[key]] = out.get(i[key], 0) + 1
    return out


def next_action(p, v: dict | None = None) -> str:
    st = p.state
    stage = st["stage"]
    if list(p.path("state/pending").glob("*.closure.json")):
        return "Run `recover` — an interrupted gap closure was found."
    if stage in ("PAUSED", "BLOCKED"):
        return f"Workflow is {stage}. Resume to {st.get('resume_stage')} once the blocker is cleared (e.g. identify the decision owner)."
    if st["impact_review"].get("pending"):
        return "BRD changed: reassess affected requirements (`req update --cause source-change` / `req confirm` / `req remove`), then `source accept`."
    if stage == "BRD_PARSED":
        return "Decompose the BRD into atomic requirements (`req add`), then `req commit`."
    if stage == "REQUIREMENTS_READY":
        return "Run gap analysis: `gaps add`, cross-requirement pass, then `gaps commit --cross-pass-done`."
    gaps = p.gaps()
    unresolved = [g for g in gaps if g["status"] != "RESOLVED"]
    presentable = [g for g in unresolved if g["status"] in ("OPEN", "WAITING_FOR_ANSWER", "REOPENED")]
    if stage in ("GAP_ANALYSIS", "WAITING_FOR_GAP_INPUT"):
        if presentable:
            if not st.get("decision_owner"):
                return "Ask who the decision owner is (name + role) → `owner set`; then `gaps next`."
            return f"{len(presentable)} gap(s) await answers: `gaps next`."
        if not st.get("gap_analysis_complete"):
            return "Finish gap analysis (cross-requirement pass), then `gaps commit --cross-pass-done`."
        return "Create the baseline: `baseline create` (deferred gaps will block their requirements)."
    if not st.get("gap_analysis_complete"):
        if any(r["status"] == "NEEDS_REVIEW" for r in p.requirements()):
            return "Requirements need review after a change; finish impact review."
        return "Re-run gap analysis for changed requirements, then `gaps commit --cross-pass-done`."
    m = p.baseline_manifest()
    if m["stale"]:
        return f"Baseline is stale ({m['stale_reason']}). Re-run any needed analysis, then `baseline create`."
    if m["versions"] and m["versions"][-1]["status"] == "PROPOSED":
        return f"Ask the user to confirm baseline v{m['versions'][-1]['version']} (`baseline confirm`)."
    stories = p.all_stories()
    flagged = [s["story_id"] for _, s in stories if (s.get("impact_review") or {}).get("pending")]
    if flagged:
        return f"Stories need impact review: {', '.join(sorted(set(flagged)))} (`story edit` or `story confirm-unaffected`)."
    if not stories and stage == "REQUIREMENTS_BASELINED":
        return "Generate candidate stories from the approved baseline (`story add`)."
    cand = [s for b, s in stories if b == "candidates"]
    if cand:
        return f"{len(cand)} story(ies) await review: `story next`."
    unapproved = [s for b, s in stories if b == "unapproved"]
    deferred = [s for b, s in stories if b == "deferred"]
    comp = completion(p)
    if comp["complete"]:
        if stage == "COMPLETED":
            return "Workflow complete. If the BRD changes, run `source check`; to revise a story use `story edit`."
        return "All work is approved. Run `complete` to close the workflow."
    if comp["uncovered"]:
        return f"Requirements without stories: {', '.join(comp['uncovered'])} — generate stories (`story add`)."
    if unapproved:
        return (f"{len(unapproved)} unapproved story(ies) in stories/unapproved/: ask what should change "
                "(`story next --unapproved`, then `story edit`).")
    if deferred:
        return f"{len(deferred)} deferred story(ies): revisit with `story next --deferred`."
    return "Resolve/defer remaining blocked requirements: " + "; ".join(comp["reasons"][:3])


def completion(p) -> dict:
    reasons = []
    st = p.state
    v = validate_project(p)
    if v["errors"]:
        reasons.append(f"{len(v['errors'])} validation error(s)")
    base = p.approved_baseline()
    if not base:
        reasons.append("no approved baseline")
    elif p.baseline_manifest()["stale"]:
        reasons.append("baseline is stale")
    if st["impact_review"].get("pending"):
        reasons.append("BRD change awaiting impact review")
    t = traceability(p)
    if t["blocked_or_not_baselined"]:
        reasons.append(f"requirements blocked/not baselined: {', '.join(t['blocked_or_not_baselined'])}")
    if t["not_covered_by_approved_story"]:
        reasons.append(f"requirements without an approved story: {', '.join(t['not_covered_by_approved_story'])}")
    stories = p.all_stories()
    pend = [s["story_id"] for b, s in stories if b in ("candidates", "unapproved", "deferred")]
    if pend:
        reasons.append(f"stories not approved: {', '.join(sorted(set(pend)))}")
    flagged = [s["story_id"] for _, s in stories if (s.get("impact_review") or {}).get("pending")]
    if flagged:
        reasons.append(f"stories flagged for impact review: {', '.join(sorted(set(flagged)))}")
    if not t["ok"]:
        reasons.append("traceability problems (orphans/dangling/duplicates)")
    return {"complete": not reasons, "reasons": reasons, "uncovered": t["uncovered_by_any_story"]}


def status_report(p) -> dict:
    st = p.state
    reqs = p.requirements()
    gaps = p.gaps()
    stories = p.all_stories()
    m = p.baseline_manifest()
    pol = st["policy"]
    blocking = [g["gap_id"] for g in gaps if g["status"] != "RESOLVED" and (g["priority"] in pol["blocking_priorities"] or not pol["accept_nonblocking_carry_forward"])]
    v = validate_project(p)
    return {
        "project_id": st["project_id"], "workflow_id": st["workflow_id"], "stage": st["stage"], "output_folder": str(p.root),
        "source": {"file": Path(st["source"]["path"]).name, "path": st["source"]["path"], "fingerprint": st["source"]["fingerprint"],
                   "format": st["source"]["format"]},
        "decision_owner": st.get("decision_owner") and {"name": st["decision_owner"]["name"], "role": st["decision_owner"]["role"]},
        "requirements": _counts(reqs, "status"),
        "gaps": {"total": len(gaps), "by_status": _counts(gaps, "status"), "blocking_unresolved": blocking,
                 "deferred": [g["gap_id"] for g in gaps if g["status"] == "DEFERRED"]},
        "baseline": {"approved_version": st["baseline_version"], "latest": m["versions"][-1] if m["versions"] else None,
                     "stale": m["stale"], "stale_reason": m["stale_reason"]},
        "stories": {"candidate": sum(1 for b, _ in stories if b == "candidates"),
                    "approved": sum(1 for b, _ in stories if b == "approved"),
                    "unapproved": sum(1 for b, _ in stories if b == "unapproved"),
                    "deferred": sum(1 for b, _ in stories if b == "deferred"),
                    "flagged_for_impact_review": sorted({s["story_id"] for _, s in stories if (s.get("impact_review") or {}).get("pending")})},
        "impact_review_pending": st["impact_review"].get("pending", False),
        "validation": {"ok": v["ok"], "errors": v["errors"][:10], "warnings": v["warnings"][:10],
                       "error_count": len(v["errors"]), "warning_count": len(v["warnings"])},
        "updated_at": st["updated_at"], "next_action": next_action(p),
    }


def status_text(r: dict) -> str:
    g, s, b = r["gaps"], r["stories"], r["baseline"]
    lines = [f"BA project: {r['project_id']}   stage: {r['stage']}",
             f"Source: {r['source']['file']} ({r['source']['format']})  fingerprint {r['source']['fingerprint'][:19]}…",
             f"Output folder: {r['output_folder']}",
             "Decision owner: " + (f"{r['decision_owner']['name']} — {r['decision_owner']['role']}" if r["decision_owner"] else "NOT YET IDENTIFIED"),
             f"Requirements: {r['requirements'] or 'none yet'}",
             f"Gaps: {g['total']} total · {g['by_status'] or '—'} · blocking unresolved: {', '.join(g['blocking_unresolved']) or 'none'}",
             f"Baseline: approved v{b['approved_version']}" + (" (STALE: " + str(b['stale_reason']) + ")" if b["stale"] else "")
             if b["approved_version"] else "Baseline: none approved" + (f" (latest v{b['latest']['version']} {b['latest']['status']})" if b["latest"] else ""),
             f"Stories: {s['approved']} approved · {s['candidate']} candidate · {s['deferred']} deferred"
             + (f" · impact review: {', '.join(s['flagged_for_impact_review'])}" if s["flagged_for_impact_review"] else ""),
             f"Validation: {'OK' if r['validation']['ok'] else str(r['validation']['error_count']) + ' ERROR(S)'}, {r['validation']['warning_count']} warning(s)",
             f"Last updated: {human_ist(r['updated_at'])}", "", f"NEXT: {r['next_action']}"]
    for e in r["validation"]["errors"][:5]:
        lines.insert(-2, f"  ! {e}")
    return "\n".join(lines)


def complete_workflow(p) -> dict:
    comp = completion(p)
    st = p.state
    if comp["complete"]:
        if st["stage"] == "STORIES_READY":
            p.set_stage("WAITING_FOR_STORY_REVIEW", "story-review-done")
        if p.state["stage"] == "WAITING_FOR_STORY_REVIEW":
            p.audit("WORKFLOW_COMPLETED")
            p.set_stage("COMPLETED", "workflow-completed")
    report = write_summary(p, comp)
    return {"complete": comp["complete"], "stage": p.state["stage"], "outstanding": comp["reasons"],
            "summary_report": "reports/workflow-summary.md",
            "message": "Workflow COMPLETE." if comp["complete"] else "Workflow is PARTIAL / in progress — not complete."} | {"summary": report}


def write_summary(p, comp: dict | None = None) -> str:
    comp = comp or completion(p)
    r = status_report(p)
    g, s = r["gaps"], r["stories"]
    lines = ["# Workflow summary", "", f"**Status: {'COMPLETE' if comp['complete'] else 'PARTIAL — in progress'}** (stage {r['stage']})", "",
             f"- Source BRD: `{r['source']['file']}` · fingerprint `{r['source']['fingerprint']}`",
             f"- Output folder: `{r['output_folder']}`",
             f"- Requirements: {r['requirements']}",
             f"- Gaps: {g['by_status']} · blocking unresolved: {', '.join(g['blocking_unresolved']) or 'none'} · deferred: {', '.join(g['deferred']) or 'none'}",
             f"- Baseline: approved v{r['baseline']['approved_version']}",
             f"- Stories: {s['approved']} approved · {s['candidate']} candidate · {s['deferred']} deferred",
             f"- Validation warnings: {r['validation']['warnings'] or 'none'}", f"- Generated: {human_ist(ist_now_iso())}", ""]
    if not comp["complete"]:
        lines += ["## Outstanding"] + [f"- {x}" for x in comp["reasons"]] + [""]
    lines += ["## How to resume", "Run `/ba-agent:start` (or `/ba-agent:review`) and point it at this folder.", f"Next: {r['next_action']}"]
    text = "\n".join(lines) + "\n"
    p.write_text("reports/workflow-summary.md", text)
    return text
