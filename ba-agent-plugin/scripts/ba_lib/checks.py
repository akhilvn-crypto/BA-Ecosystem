"""Deterministic validation, traceability, crash recovery, status and completion."""
from __future__ import annotations

import json
from pathlib import Path

from . import acs as acmod, actrack, changes as changemod, docsync, gaps as gapmod, reqindex, schema, snapshots, sync
from .project import GAP_BUCKETS, ac_fname, gap_bucket
from .util import append_jsonl, atomic_write_bytes, human_ist, is_ist_iso, ist_now_iso, read_jsonl, normalize_ws, sha256_bytes

CLOSURE_FIELDS = ["decision_id", "resolution", "decision_owner_name", "decision_owner_role", "closed_by_name",
                  "closed_by_role", "closed_at_ist", "closure_summary", "closure_validation"]
BUCKET_STATUS = {"candidates": {"CANDIDATE", "IN_REVIEW", "BLOCKED"}, "approved": {"APPROVED"},
                 "unapproved": {"CHANGES_REQUESTED"}, "deferred": {"DEFERRED"}}
PRIORITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


def validate_project(p) -> dict:
    E, W = [], []
    st = p.state
    # With the file bridge on, the workspace is a lean copy: md/ views are rebuilt by writing commands and already sit on the
    # user's side, so a missing view is expected, not damage (and `recover` would only rebuild copies the user already has).
    views_not_staged = sync.bridge_on(p.root)
    # ---- source and index
    doc = reqindex.load(p)
    ents = doc["requirements"]
    keys = [e["req_key"] for e in ents]
    rkeys = set(keys)
    if len(keys) != len(rkeys):
        E.append("duplicate requirement keys in the index")
    ids: dict[str, int] = {}
    for e in ents:
        if e["status"] == "ACTIVE":
            ids[e["id"]] = ids.get(e["id"], 0) + 1
    for i, n in sorted(ids.items()):
        if n > 1:
            W.append(f"the ID {i} appears in {n} places; refer to those requirements by their full key")
    root = Path(st["source"]["root"])
    if not root.exists():
        W.append(f"the requirements folder was not found at {root}")
    for rel, f in doc["files"].items():
        if f["status"] != "ACTIVE":
            continue
        if snapshots.latest(p, rel) is None or snapshots.read_bytes(p, snapshots.latest(p, rel)) is None:
            W.append(f"the accepted copy of {rel} is missing or damaged (snapshots/source/); the enriched copy cannot be rendered from it")
        path = root / rel
        if root.exists() and path.is_file() and sha256_bytes(path.read_bytes()) != f["file_hash"]:
            W.append(f"{rel} has changed since it was read — run `changes check` (it explains the impact before anything is changed)")
        if f.get("mode") == "headings":
            W.append(f"{rel}: no requirement IDs found; headings are used as requirements (keys are heading paths)")
        if f.get("unclosed_fence"):
            W.append(f"{rel}: a code fence is never closed, so this file is copied without enrichment")
        if f.get("instruction_like_lines"):
            W.append(f"{rel}: {len(f['instruction_like_lines'])} instruction-like line(s) are treated as data only")
    for e in ents:
        for fl in e.get("flags", []):
            if fl["kind"] == "DUPLICATE_ID":
                W.append(f"{e['req_key']}: this ID appears more than once in {e['file']}")
    # ---- enriched copies
    if p.exists(docsync.MANIFEST) or doc["files"]:
        for row in docsync.check(p)["files"]:
            if row["state"] == "ERROR":
                E.append(f"enriched copy of {row['file']}: {row['problem']}")
            elif row["state"] == "DRIFT":
                W.append(f"the enriched copy of {row['file']} was changed by hand — run `changes check` (the edit is kept before it is restored)")
            elif row["state"] in ("STALE", "MISSING") and not views_not_staged:
                W.append(f"the enriched copy of {row['file']} is {row['state'].lower()} — run `doc sync`")
    for w in p.doc_warnings:
        W.append(f"enriched copy: {w}")
    # ---- gaps / decisions / audit
    gaps = p.gaps()
    gids = [g["gap_id"] for g in gaps]
    if len(gids) != len(set(gids)):
        E.append("duplicate gap IDs in gap-register.json")
    for g in gaps:
        b = gap_bucket(g)
        if views_not_staged:
            pass
        elif not p.exists(f"gaps/{b}/{g['gap_id']}.md"):
            W.append(f"{g['gap_id']}: readable copy missing from gaps/{b}/md (run `recover` to rebuild it)")
        elif any(p.exists(f"gaps/{o}/{g['gap_id']}.md") for o in GAP_BUCKETS if o != b):
            W.append(f"{g['gap_id']}: also found in another gaps/ folder; it belongs in gaps/{b} (run `recover`)")
    recs, jerr = read_jsonl(p.path("decisions/decision-log.jsonl"))
    E += jerr
    decs = p.decisions()
    dids = [r["decision_id"] for r in recs if r.get("record_type") == "decision"]
    if len(dids) != len(set(dids)):
        E.append("duplicate decision IDs in decision-log.jsonl (a decision was recorded twice)")
    for d in decs.values():
        for e in schema.validate({k: v for k, v in d.items() if k not in ("current_status", "status_reason")}, "decision"):
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
    event_ids = {ev.get("event_id") for ev in events}
    closures = {}
    for ev in events:
        if ev.get("event_type") == "GAP_RESOLVED":
            closures.setdefault(ev["gap_id"], []).append(ev)
    for g in gaps:
        for e in schema.validate(g, "gap"):
            E.append(f"{g.get('gap_id')}: {e}")
        for r in g["impacted_req_keys"]:
            if r not in rkeys:
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
        if g["status"] == "DEFERRED":
            d = g.get("deferral") or {}
            missing = [f for f in ("reason", "by_name", "by_role", "at_ist", "decision_owner_name", "decision_owner_role") if not d.get(f)]
            if missing:
                W.append(f"{g['gap_id']}: DEFERRED but the deferral record is missing {missing}")
            elif not is_ist_iso(d["at_ist"]):
                E.append(f"{g['gap_id']}: deferral timestamp is not an IST (+05:30) timestamp")
        elif g.get("deferral"):
            E.append(f"{g['gap_id']}: status {g['status']} but a deferral record is still attached")
        cf = g.get("carry_forward")
        if cf:
            if g["status"] != "OPEN":
                E.append(f"{g['gap_id']}: status {g['status']} but a carry-forward record is still attached "
                         "(a carried-forward gap stays OPEN)")
            missing = [f for f in ("by_name", "by_role", "at_ist", "decision_owner_name", "decision_owner_role") if not cf.get(f)]
            if missing:
                E.append(f"{g['gap_id']}: carry-forward record is missing {missing}")
            elif not is_ist_iso(cf["at_ist"]):
                E.append(f"{g['gap_id']}: carry-forward timestamp is not an IST (+05:30) timestamp")
            why = gapmod.carry_forward_blocker(p, g)
            if why:
                W.append(f"{g['gap_id']}: carried forward but no longer eligible ({why}); it blocks its acceptance criteria again")
        for h in g["history"]:
            if h.get("event_id") and h["event_id"] not in event_ids:
                W.append(f"{g['gap_id']}: audit event {h['event_id']} ({h.get('event_type')}) is missing — run `recover` to restore it")
        if g.get("needs_impact_review"):
            W.append(f"{g['gap_id']}: flagged for impact review")
    for f in p.path("state/pending").glob("*.closure.json"):
        E.append(f"interrupted gap closure journal {f.name} — run `recover`")
    # ---- acceptance criteria
    for rel in p.unreadable_ac_files():
        E.append(f"{rel}: not valid JSON - the record cannot be read (run `changes check`; an approved record's edit is kept before it is restored)")
    seen: dict[str, list[tuple[str, int]]] = {}
    for bucket, a in p.all_acs():
        key = a.get("req_key")
        for e in schema.validate(a, "ac"):
            E.append(f"{key}: {e}")
        if a.get("status") not in BUCKET_STATUS[bucket]:
            E.append(f"{key}: status {a.get('status')} is not allowed in acceptance/{bucket}/")
        if bucket == "approved":
            ap = a.get("approval") or {}
            if ap.get("version") != a.get("version") or ap.get("decision") != "APPROVED" or not is_ist_iso(ap.get("at_ist")):
                E.append(f"{key}: approved record lacks a valid approval for version {a.get('version')}")
            if not any(e.get("event_type") == "AC_APPROVED" and e.get("req_key") == key and e.get("version") == a["version"] for e in events):
                E.append(f"{key}: v{a['version']} is in approved/ but has no AC_APPROVED audit event")
        if key not in rkeys:
            E.append(f"{key}: the requirement does not exist in the index")
        for d in a["decision_ids"]:
            if d not in decs:
                E.append(f"{key}: linked decision {d} does not exist")
        declared = {u["gap_id"] for u in a["open_gap_refs"]}
        for gid in sorted(declared):
            if gid not in gids:
                E.append(f"{key}: open gap {gid} does not exist")
        live = {g["gap_id"] for g in gaps if g["status"] != "RESOLVED" and key in g["impacted_req_keys"]}
        if declared != live and not (a.get("impact_review") or {}).get("pending"):
            W.append(f"{key} v{a['version']}: open gaps {sorted(live)} differ from the criteria's declared open gaps {sorted(declared)}")
        if a.get("readiness") and a["readiness"] != acmod.readiness_of(a["open_gap_refs"]):
            E.append(f"{key}: readiness {a['readiness']} does not match its open gaps")
        by = {e["req_key"]: e for e in ents}
        if key in by and by[key]["status"] == "ACTIVE" and by[key]["material_hash"] != a.get("requirement_hash") and not (a.get("impact_review") or {}).get("pending"):
            W.append(f"{key} v{a['version']}: the requirement text changed since these criteria were written")
        seen.setdefault(key, []).append((bucket, a["version"]))
        if (a.get("impact_review") or {}).get("pending"):
            W.append(f"{key} v{a['version']}: impact review pending ({a['impact_review']['reason']})")
    for key, locs in seen.items():
        if len({v for b, v in locs if b != "approved"}) < len([1 for b, v in locs if b != "approved"]):
            E.append(f"{key}: present in multiple non-approved folders")
        for b, v in locs:
            if b != "approved" and ("approved", v) in locs:
                E.append(f"{key}: v{v} is in both approved/ and {b}/ — interrupted approval; run `recover`")
    # ---- change records, approved-AC drift
    for rec in changemod.records(p):
        for e in schema.validate(rec, "change"):
            E.append(f"{rec.get('change_id')}: {e}")
        if rec["status"] == "DETECTED":
            W.append(f"{rec['change_id']}: detected but not yet applied or reviewed ({rec['summary']})")
    for item, _ in changemod.detect_acs(p)[0]:
        W.append(f"{item['req_key']}: the approved criteria on disk differ from what was approved ({item['change_type'].replace('_', ' ').lower()}, "
                 f"{item['classification'].replace('_', ' ').lower()}) — run `changes check`")
    for key, e in actrack.index(p)["acs"].items():
        for v in e["versions"]:
            if actrack.snapshot_record(p, v) is None:
                W.append(f"{key} v{v['version']}: the approved snapshot {v['snapshot']} is missing or damaged; the approval cannot be restored from it")
    expect = sorted(g["gap_id"] for g in gaps if g["status"] != "RESOLVED")
    if sorted(st["pending_gap_ids"]) != expect:
        W.append("workflow.json pending_gap_ids is out of date (fixed on next save)")
    out = {"ok": not E, "errors": E, "warnings": W,
           "counts": {"requirements": sum(1 for e in ents if e["status"] == "ACTIVE"), "gaps": len(gaps), "decisions": len(decs),
                      "audit_events": len(events)}}
    if views_not_staged:
        out["md_views"] = "not checked: bridge on, md/ views are not staged (do not run `recover` for them)"
    return out


def traceability(p, write: bool = False) -> dict:
    ents = {e["req_key"]: e for e in p.requirements(False)}
    active = {k for k, e in ents.items() if e["status"] == "ACTIVE"}
    blocked_map = gapmod.blocked_requirements(p)
    cover = {k: {"approved": [], "candidate": [], "deferred": []} for k in active}
    orphans, dangling = [], []
    for bucket, a in p.all_acs():
        key = {"approved": "approved", "candidates": "candidate", "unapproved": "candidate", "deferred": "deferred"}[bucket]
        if a["req_key"] not in ents:
            dangling.append({"from": a["req_key"], "to": a["req_key"], "note": "no such requirement"})
            orphans.append(a["req_key"])
        elif a["req_key"] not in active:
            dangling.append({"from": a["req_key"], "to": a["req_key"], "note": "requirement is not active (removed from the source)"})
        else:
            cover[a["req_key"]][key].append(f"v{a['version']}")
    for g in p.gaps():
        for r in g["impacted_req_keys"]:
            if r not in ents:
                dangling.append({"from": g["gap_id"], "to": r})
    gap_trace = {}
    acs = p.all_acs()
    for g in p.gaps():
        if g["status"] != "RESOLVED":
            linked = sorted({a["req_key"] for _, a in acs if g["gap_id"] in [u["gap_id"] for u in a.get("open_gap_refs", [])]})
            affected = sorted({a["req_key"] for _, a in acs if a["req_key"] in g["impacted_req_keys"]})
            gap_trace[g["gap_id"]] = {"priority": g["priority"], "status": g["status"], "carried_forward": gapmod.is_carried(p, g),
                                      "blocks_ac": gapmod.blocks_requirements(p, g), "requirements": g["impacted_req_keys"],
                                      "acs": linked, "acs_missing_the_link": sorted(set(affected) - set(linked))}
    unlinked = sorted({k for v in gap_trace.values() for k in v["acs_missing_the_link"]})
    blocked = sorted(k for k in active if k in blocked_map)
    workable = sorted(k for k in active if k not in blocked_map)
    uncovered = sorted(k for k in workable if not any(cover[k].values()))
    without_approved = sorted(k for k in workable if not cover[k]["approved"])
    ch = changemod.summary(p)
    out = {"requirements_active": len(active), "blocked": blocked,
           "change_impact": {"unreviewed": ch["unreviewed"], "awaiting_reconciliation": ch["awaiting_reconciliation"],
                             "undetected": ch["undetected"], "stale_artifacts": ch["stale"]},
           "uncovered_by_any_ac": uncovered, "not_covered_by_approved_ac": without_approved,
           "orphan_acs": sorted(set(orphans)), "dangling_references": dangling,
           "open_gap_traceability": gap_trace, "acs_missing_open_gap_links": unlinked,
           "document_location": {k: f"{ents[k]['file']}:{ents[k]['line_start']}" for k in sorted(active)},
           "coverage": {k: v for k, v in sorted(cover.items())}}
    out["ok"] = not (orphans or dangling)
    if write:
        lines = ["# Traceability report", "", f"Generated {human_ist(ist_now_iso())}", "",
                 f"- Active requirements: {len(active)} · blocked by gaps: {len(blocked)}",
                 f"- Without any acceptance criteria (not blocked): {', '.join(uncovered) or 'none'}",
                 f"- Not yet covered by approved criteria (not blocked): {', '.join(without_approved) or 'none'}",
                 f"- Blocked by unresolved gaps: {', '.join(blocked) or 'none'}",
                 f"- Orphan criteria: {', '.join(sorted(set(orphans))) or 'none'}",
                 f"- Dangling references: {json.dumps(dangling) if dangling else 'none'}",
                 f"- Criteria missing a link to an open gap: {', '.join(unlinked) or 'none'}", "",
                 "## Change impact (requirement files / approved criteria / enriched copies)",
                 f"- Awaiting your decision: {', '.join(c['change_id'] for c in ch['unreviewed']) or 'none'}",
                 f"- Applied, actions still open: {', '.join(c['change_id'] + ' (' + ', '.join(c['open_items']) + ')' for c in ch['awaiting_reconciliation']) or 'none'}",
                 f"- Not yet checked: {ch['undetected']['source'] or 'requirements unchanged'}; criteria: {', '.join(ch['undetected']['acs']) or 'none'}; "
                 f"enriched copies: {', '.join(ch['undetected']['enriched']) or 'none'}",
                 f"- Under review: criteria {', '.join(ch['stale']['acs_flagged']) or 'none'}; gaps {', '.join(ch['stale']['gaps_flagged']) or 'none'}", "",
                 "## Open gap → acceptance-criteria traceability", "| Gap | Priority | State | Requirements | Criteria that name it |", "|---|---|---|---|---|"]
        for gid, v in sorted(gap_trace.items()):
            state = "carried forward" if v["carried_forward"] else ("blocking" if v["blocks_ac"] else v["status"])
            lines.append(f"| {gid} | {v['priority']} | {state} | {', '.join(p.label(k) for k in v['requirements'])} | {', '.join(v['acs']) or '—'} |")
        lines += ["", "## Requirement → acceptance-criteria coverage",
                  "| Requirement | Where | Approved | Candidate | Deferred |", "|---|---|---|---|---|"]
        for k, v in sorted(cover.items()):
            lines.append(f"| {p.label(k)} | {out['document_location'][k]} | {', '.join(v['approved']) or '—'} | {', '.join(v['candidate']) or '—'} | {', '.join(v['deferred']) or '—'} |")
        p.write_text("reports/traceability-report.md", "\n".join(lines) + "\n")
    return out


def recover(p) -> dict:
    actions = gapmod.recover_journals(p)
    out = {"closures_rolled_forward": actions, "approvals_repaired": [], "gaps_reopened": [], "audit_backfilled": []}
    if p._has_register():
        out["gap_files_rebuilt"] = p.sync_gap_files()
    events = p.audit_events()
    for path in p.files("acceptance/approved", "*.json"):
        a = json.loads(path.read_text(encoding="utf-8"))
        cand = p.path(f"acceptance/candidates/{ac_fname(a['req_key'])}.json")
        if cand.exists() and json.loads(cand.read_text(encoding="utf-8"))["version"] == a["version"]:
            p.remove_ac_files("candidates", a["req_key"])
            out["approvals_repaired"].append(a["req_key"])
        if not any(e.get("event_type") == "AC_APPROVED" and e.get("req_key") == a["req_key"] and e.get("version") == a["version"] for e in events):
            ap = a.get("approval") or {}
            p.audit("AC_APPROVED", req_key=a["req_key"], version=a["version"], approver_name=ap.get("approver_name"),
                    approver_role=ap.get("approver_role"), statement=ap.get("statement"), recovered=True,
                    original_approval_at_ist=ap.get("at_ist"))
            out["audit_backfilled"].append(a["req_key"])
    out["ac_tracking_adopted"] = []
    for rec in changemod.detect_acs(p)[1]:
        actrack.record_approved(p, rec, event="ADOPTED")
        out["ac_tracking_adopted"].append(rec["req_key"])
    gaps = p.gaps()
    events = p.audit_events()
    have = {e.get("event_id") for e in events}
    for g in gaps:
        for h in g["history"]:
            if h.get("event_id") and h["event_id"] not in have:
                ev = p._event(h.get("event_type") or "GAP_STATUS_CHANGED", gap_id=g["gap_id"], from_status=h["from"], to_status=h["to"],
                              note=h.get("note"), recovered=True, priority=g["priority"])
                cf = g.get("carry_forward") or (g.get("carry_forward_history") or [None])[-1]
                if h.get("event_type") == "GAP_CARRIED_FORWARD" and cf:
                    ev.update(reason=cf.get("reason"), actor={"name": cf.get("by_name"), "role": cf.get("by_role")},
                              decision_owner={"name": cf.get("decision_owner_name"), "role": cf.get("decision_owner_role")},
                              req_keys=cf.get("affected_req_keys"))
                if h["to"] == "DEFERRED" and g.get("deferral"):
                    d = g["deferral"]
                    ev.update(reason=d.get("reason"), waiting_on=d.get("owner"), revisit_by=d.get("revisit_by"),
                              actor={"name": d.get("by_name"), "role": d.get("by_role")},
                              decision_owner={"name": d.get("decision_owner_name"), "role": d.get("decision_owner_role")})
                ev["event_id"], ev["at_ist"] = h["event_id"], h["at_ist"]
                append_jsonl(p.path("state/audit.jsonl"), ev)
                have.add(h["event_id"])
                out["audit_backfilled"].append(f"{g['gap_id']}:{h['event_id']}")
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
    r = docsync.refresh(p)
    out["enriched_rendered"] = r["written"]
    if r["drift"]:
        out["enriched_drift"] = r["drift"]
    p.save_state("recovered")
    return out


# ------------------------------------------------------------------ status

def _counts(items, key):
    out = {}
    for i in items:
        out[i[key]] = out.get(i[key], 0) + 1
    return out


def requirement_rows(p) -> list[dict]:
    """Every active requirement with its live status (READY | READY_WITH_OPEN_GAPS | BLOCKED) and where its criteria stand."""
    blocked = gapmod.blocked_requirements(p)
    open_nb = gapmod.open_nonblocking_requirements(p)
    st = acmod.ac_states(p)
    labels = reqindex.labels(p)
    rows = []
    for e in p.requirements():
        k = e["req_key"]
        s = st.get(k) or {}
        rows.append({"req_key": k, "label": labels[k], "title": e["title"], "status": docsync.requirement_status(p, k, blocked, open_nb),
                     "blocked_by": blocked.get(k), "open_gaps": open_nb.get(k), "ac_approved": s.get("approved"), "ac_pending": s.get("pending"),
                     "readiness": s.get("readiness"), "impact_review": s.get("impact_review") or None})
    return rows


def next_action(p, v: dict | None = None, ch: dict | None = None) -> str:
    st = p.state
    stage = st["stage"]
    ch = ch or changemod.summary(p)
    if list(p.path("state/pending").glob("*.closure.json")):
        return "Run `recover` — an interrupted gap closure was found."
    if stage in ("PAUSED", "BLOCKED"):
        return f"Workflow is {stage}. Resume to {st.get('resume_stage')} once the blocker is cleared (e.g. identify the decision owner)."
    if ch["unreviewed"]:
        return (f"{len(ch['unreviewed'])} detected change(s) await the user's decision ({', '.join(c['change_id'] for c in ch['unreviewed'])}): "
                "show the summary and impact (`changes show`), then `changes apply`. Nothing downstream has been changed yet.")
    und = ch["undetected"]
    if und["source"] or und["acs"] or und["enriched"]:
        what = ("the requirement files" if und["source"] else
                "approved criteria for " + ", ".join(und["acs"]) if und["acs"] else "the enriched copy " + ", ".join(und["enriched"]))
        return f"{what} changed since the last check: run `changes check` and show the user the impact before anything else."
    if stage == "SOURCE_INDEXED":
        return "Run gap analysis: read each requirement (`req list`, `req show`), `gaps add`, the cross-requirement pass (`req pairs`), then `gaps commit --cross-pass-done`."
    gaps = p.gaps()
    unresolved = [g for g in gaps if g["status"] != "RESOLVED"]
    presentable = [g for g in unresolved if g["status"] in ("OPEN", "WAITING_FOR_ANSWER", "REOPENED") and not g.get("carry_forward")]
    carried = [g["gap_id"] for g in unresolved if g.get("carry_forward")]
    rows = requirement_rows(p)
    todo = [r["req_key"] for r in rows if r["status"] != "BLOCKED" and not r["ac_approved"] and not r["ac_pending"]]
    if stage in ("GAP_ANALYSIS", "WAITING_FOR_GAP_INPUT") or (presentable and not st.get("gap_analysis_complete")):
        if presentable:
            if not st.get("decision_owner"):
                return "Ask who the decision owner is (name + role) → `owner set`; then `gaps next`."
            return f"{len(presentable)} gap(s) await answers: `gaps next`."
        if not st.get("gap_analysis_complete"):
            return "Finish gap analysis (cross-requirement pass), then `gaps commit --cross-pass-done`."
        if todo:
            return (f"Draft acceptance criteria for the {len(todo)} requirement(s) that are not blocked (`ac gaps`, then `ac add`)"
                    + (f"; {len(carried)} carried-forward gap(s) stay open and are named in the criteria" if carried else "") + ".")
        return "Resolve, defer or carry forward the remaining gaps (`gaps triage`): they block the other requirements' criteria."
    if not st.get("gap_analysis_complete"):
        return "Re-run gap analysis for changed requirements, then `gaps commit --cross-pass-done`."
    flagged = sorted({r["req_key"] for r in rows if r["impact_review"]})
    if flagged:
        return f"Acceptance criteria need impact review: {', '.join(p.label(k) for k in flagged)} (`ac edit` or `ac confirm-unaffected`)."
    if presentable:
        return f"{len(presentable)} gap(s) await answers: `gaps next`."
    cand = [a for b, a in p.all_acs() if b == "candidates"]
    if cand:
        return f"{len(cand)} acceptance-criteria record(s) await review: `ac next`."
    if todo and stage != "COMPLETED":
        return f"Draft acceptance criteria for {len(todo)} requirement(s) without any (`ac gaps`, then `ac add`)."
    unapproved = [a for b, a in p.all_acs() if b == "unapproved"]
    deferred = [a for b, a in p.all_acs() if b == "deferred"]
    comp = completion(p, ch)
    note = (f" {len(carried)} carried-forward gap(s) are still OPEN ({', '.join(carried)}): revisit them with `gaps next --carried` "
            "when the answer is known; the linked criteria stay flagged NEEDS_CLARIFICATION until then." if carried else "")
    if comp["complete"]:
        if stage == "COMPLETED":
            return ("Workflow complete. If a requirement file or approved criteria change, run `changes check`; to revise criteria use `ac edit`." + note)
        return "All work is approved. Run `complete` to close the workflow." + note
    if todo:
        return f"Requirements without acceptance criteria: {', '.join(p.label(k) for k in todo)} — write them (`ac add`)."
    if unapproved:
        return (f"{len(unapproved)} unapproved record(s) in acceptance/unapproved/: ask what should change "
                "(`ac next --unapproved`, then `ac edit`).")
    if deferred:
        return f"{len(deferred)} deferred record(s): revisit with `ac next --deferred`."
    return "Resolve/defer remaining blocked requirements: " + "; ".join(comp["reasons"][:3])


def completion(p, ch: dict | None = None) -> dict:
    reasons = []
    st = p.state
    ch = ch or changemod.summary(p)
    if ch["unreviewed"]:
        reasons.append("detected changes not yet reviewed: " + ", ".join(c["change_id"] for c in ch["unreviewed"]))
    und = ch["undetected"]
    if und["source"] or und["acs"] or und["enriched"]:
        reasons.append("the requirement files, approved criteria or an enriched copy changed since the last `changes check`")
    if ch["awaiting_reconciliation"]:
        reasons.append("applied changes with open actions: " + "; ".join(f"{c['change_id']} ({', '.join(c['open_items'])})" for c in ch["awaiting_reconciliation"]))
    v = validate_project(p)
    if v["errors"]:
        reasons.append(f"{len(v['errors'])} validation error(s)")
    if not st.get("gap_analysis_complete"):
        reasons.append("gap analysis is not complete")
    t = traceability(p)
    if t["blocked"]:
        reasons.append(f"requirements blocked by unresolved gaps: {', '.join(p.label(k) for k in t['blocked'])}")
    if t["not_covered_by_approved_ac"]:
        reasons.append(f"requirements without approved acceptance criteria: {', '.join(p.label(k) for k in t['not_covered_by_approved_ac'])}")
    acs = p.all_acs()
    pend = sorted({a["req_key"] for b, a in acs if b in ("candidates", "unapproved", "deferred")})
    if pend:
        reasons.append(f"acceptance criteria not approved: {', '.join(p.label(k) for k in pend)}")
    flagged = sorted({a["req_key"] for _, a in acs if (a.get("impact_review") or {}).get("pending")})
    if flagged:
        reasons.append(f"acceptance criteria flagged for impact review: {', '.join(p.label(k) for k in flagged)}")
    not_ready = sorted({a["req_key"] for _, a in acs if a.get("readiness") == "NOT_READY"})
    if not_ready:
        reasons.append(f"criteria not ready for development (blocked on open gaps): {', '.join(p.label(k) for k in not_ready)}")
    if t["acs_missing_open_gap_links"]:
        reasons.append(f"criteria missing a link to an open gap: {', '.join(p.label(k) for k in t['acs_missing_open_gap_links'])}")
    if not t["ok"]:
        reasons.append("traceability problems (orphans/dangling references)")
    carried = [g["gap_id"] for g in p.gaps() if g["status"] == "OPEN" and g.get("carry_forward")]
    return {"complete": not reasons, "reasons": reasons, "uncovered": t["uncovered_by_any_ac"], "carried_forward": carried}


def status_report(p) -> dict:
    st = p.state
    reqs = p.requirements(False)
    gaps = p.gaps()
    acs = p.all_acs()
    blocking = [g["gap_id"] for g in gaps if gapmod.blocks_requirements(p, g)]
    v = validate_project(p)
    ch = changemod.summary(p)
    rows = requirement_rows(p)
    doc = reqindex.load(p)
    enr = docsync.check(p) if doc["files"] else {"files": [], "in_sync": True}
    src = st["source"]
    return {
        "project_id": st["project_id"], "workflow_id": st["workflow_id"], "stage": st["stage"], "output_folder": str(p.root),
        "source": {"path": src["path"], "root": src["root"], "kind": src["kind"], "recursive": src.get("recursive", False),
                   "files": sum(1 for f in doc["files"].values() if f["status"] == "ACTIVE"), "fingerprint": src["fingerprint"],
                   "id_pattern": src["locator"]["id_pattern"]},
        "enriched_folder": str(p.enriched_root),
        "decision_owner": st.get("decision_owner") and {"name": st["decision_owner"]["name"], "role": st["decision_owner"]["role"]},
        "requirements": _counts(reqs, "status"),
        "requirement_status": _counts(rows, "status"),
        "gaps": {"total": len(gaps), "by_status": _counts(gaps, "status"), "blocking_unresolved": blocking,
                 "deferred": [g["gap_id"] for g in gaps if g["status"] == "DEFERRED"],
                 "carried_forward": [g["gap_id"] for g in gaps if g["status"] == "OPEN" and g.get("carry_forward")],
                 "deferred_due_for_revisit": [d["gap_id"] for d in gapmod.due_for_revisit(p)],
                 "answer_later": [g["gap_id"] for g in gaps if g["status"] == "OPEN" and g["skipped_count"] > 0],
                 "outstanding_by_priority": [g["gap_id"] for g in sorted((x for x in gaps if x["status"] != "RESOLVED"),
                                                                         key=lambda x: (PRIORITY_RANK[x["priority"]], x["gap_id"]))]},
        "acceptance": {"candidate": sum(1 for b, _ in acs if b == "candidates"), "approved": sum(1 for b, _ in acs if b == "approved"),
                       "unapproved": sum(1 for b, _ in acs if b == "unapproved"), "deferred": sum(1 for b, _ in acs if b == "deferred"),
                       "needs_clarification": sorted({a["req_key"] for _, a in acs if a.get("readiness") == "NEEDS_CLARIFICATION"}),
                       "not_ready": sorted({a["req_key"] for _, a in acs if a.get("readiness") == "NOT_READY"}),
                       "flagged_for_impact_review": sorted({a["req_key"] for _, a in acs if (a.get("impact_review") or {}).get("pending")}),
                       "requirements_without_ac": [r["req_key"] for r in rows if not r["ac_approved"] and not r["ac_pending"] and r["status"] != "BLOCKED"]},
        "enriched": {"in_sync": enr["in_sync"], "files": [{"file": r["file"], "state": r["state"]} for r in enr["files"]]},
        "changes": ch,
        "validation": {"ok": v["ok"], "errors": v["errors"][:10], "warnings": v["warnings"][:10],
                       "error_count": len(v["errors"]), "warning_count": len(v["warnings"])},
        "updated_at": st["updated_at"], "next_action": next_action(p, None, ch),
    }


def _changes_line(c: dict) -> str:
    bits = []
    if c["unreviewed"]:
        bits.append("AWAITING DECISION " + ", ".join(x["change_id"] for x in c["unreviewed"]))
    u = c["undetected"]
    if u["source"] or u["acs"] or u["enriched"]:
        bits.append("NOT YET CHECKED: " + ", ".join((["requirement files"] if u["source"] else []) + u["acs"] + u["enriched"]))
    if c["awaiting_reconciliation"]:
        bits.append("open actions " + ", ".join(x["change_id"] for x in c["awaiting_reconciliation"]))
    st = c["stale"]
    stale = [f"{len(v)} {k}" for k, v in (("acceptance record(s)", st["acs_flagged"]), ("gap(s)", st["gaps_flagged"])) if v]
    return "Changes: " + ("; ".join(bits) if bits else "none pending") + (f" · under review: {', '.join(stale)}" if stale else "")


def status_text(r: dict) -> str:
    g, a = r["gaps"], r["acceptance"]
    src = r["source"]
    lines = [f"BA project: {r['project_id']}   stage: {r['stage']}",
             f"Requirements: {src['path']} ({src['kind']}, {src['files']} file(s))  fingerprint {src['fingerprint'][:19]}…",
             f"Output folder: {r['output_folder']}  ·  enriched copies: {r['enriched_folder']}",
             "Decision owner: " + (f"{r['decision_owner']['name']} — {r['decision_owner']['role']}" if r["decision_owner"] else "NOT YET IDENTIFIED"),
             f"Requirements: {r['requirements'] or 'none yet'} · status {r['requirement_status'] or '—'}",
             f"Gaps: {g['total']} total · {g['by_status'] or '—'} · blocking unresolved: {', '.join(g['blocking_unresolved']) or 'none'}"
             + (f" · answer later: {', '.join(g['answer_later'])}" if g["answer_later"] else "")
             + (f" · deferred: {', '.join(g['deferred'])}" if g["deferred"] else "")
             + (f" · CARRIED FORWARD (open): {', '.join(g['carried_forward'])}" if g["carried_forward"] else "")
             + (f" · DEFERRED GAPS DUE FOR REVISIT: {', '.join(g['deferred_due_for_revisit'])}" if g["deferred_due_for_revisit"] else ""),
             f"Acceptance criteria: {a['approved']} approved · {a['candidate']} candidate · {a['deferred']} deferred"
             + (f" · needs clarification: {', '.join(a['needs_clarification'])}" if a["needs_clarification"] else "")
             + (f" · NOT READY: {', '.join(a['not_ready'])}" if a["not_ready"] else "")
             + (f" · impact review: {', '.join(a['flagged_for_impact_review'])}" if a["flagged_for_impact_review"] else "")
             + (f" · none yet for: {', '.join(a['requirements_without_ac'][:8])}" if a["requirements_without_ac"] else ""),
             f"Enriched copies: {'in step' if r['enriched']['in_sync'] else 'NOT in step: ' + ', '.join(f['file'] + ' ' + f['state'] for f in r['enriched']['files'] if f['state'] != 'IN_SYNC')}",
             _changes_line(r["changes"]),
             f"Validation: {'OK' if r['validation']['ok'] else str(r['validation']['error_count']) + ' ERROR(S)'}, {r['validation']['warning_count']} warning(s)",
             f"Last updated: {human_ist(r['updated_at'])}", "", f"NEXT: {r['next_action']}"]
    for e in r["validation"]["errors"][:5]:
        lines.insert(-2, f"  ! {e}")
    return "\n".join(lines)


def _freeze_enriched(p) -> dict:
    """A point-in-time copy of every enriched file (a record of the approved set; no extra approval step)."""
    stamp = ist_now_iso()[:19].replace("-", "").replace(":", "")
    out = {}
    for rel in sorted(docsync.manifest(p)["files"]):
        f = docsync.disk_path(p, rel)
        if f.is_file():
            dst = f"snapshots/enriched/{rel.replace('/', '__')}@{stamp}.md"
            data = f.read_bytes()
            atomic_write_bytes(p.path(dst), data)
            out[dst] = sha256_bytes(data)
    return out


def complete_workflow(p) -> dict:
    comp = completion(p)
    st = p.state
    if comp["complete"]:
        if st["stage"] in ("GAP_ANALYSIS", "WAITING_FOR_GAP_INPUT"):
            p.set_stage("AC_DRAFTING", "ac-drafting")
        if p.state["stage"] == "AC_DRAFTING":
            p.set_stage("WAITING_FOR_AC_REVIEW", "ac-review-done")
        if p.state["stage"] == "WAITING_FOR_AC_REVIEW":
            p.audit("WORKFLOW_COMPLETED", enriched_snapshot=_freeze_enriched(p))
            p.set_stage("COMPLETED", "workflow-completed")
    report = write_summary(p, comp)
    return {"complete": comp["complete"], "stage": p.state["stage"], "outstanding": comp["reasons"], "carried_forward": comp["carried_forward"],
            "summary_report": "reports/workflow-summary.md",
            "message": "Workflow COMPLETE." if comp["complete"] else "Workflow is PARTIAL / in progress — not complete."} | {"summary": report}


def write_summary(p, comp: dict | None = None) -> str:
    comp = comp or completion(p)
    r = status_report(p)
    g, a = r["gaps"], r["acceptance"]
    lines = ["# Workflow summary", "", f"**Status: {'COMPLETE' if comp['complete'] else 'PARTIAL — in progress'}** (stage {r['stage']})", "",
             f"- Requirements: `{r['source']['path']}` ({r['source']['files']} file(s)) · fingerprint `{r['source']['fingerprint']}`",
             f"- Output folder: `{r['output_folder']}` · enriched copies: `{r['enriched_folder']}`",
             f"- Requirements: {r['requirements']} · status {r['requirement_status']}",
             f"- Gaps: {g['by_status']} · blocking unresolved: {', '.join(g['blocking_unresolved']) or 'none'} · deferred: {', '.join(g['deferred']) or 'none'}"
             f" · carried forward (still OPEN): {', '.join(g['carried_forward']) or 'none'}",
             f"- Acceptance criteria: {a['approved']} approved · {a['candidate']} candidate · {a['deferred']} deferred · "
             f"needs clarification: {', '.join(a['needs_clarification']) or 'none'} · not ready: {', '.join(a['not_ready']) or 'none'}",
             f"- {_changes_line(r['changes'])}",
             f"- Validation warnings: {r['validation']['warnings'] or 'none'}", f"- Generated: {human_ist(ist_now_iso())}", ""]
    if not comp["complete"]:
        lines += ["## Outstanding"] + [f"- {x}" for x in comp["reasons"]] + [""]
    lines += ["## How to resume", "Run `/ba-agent:start` (or `/ba-agent:review`) and point it at this folder.", f"Next: {r['next_action']}"]
    text = "\n".join(lines) + "\n"
    p.write_text("reports/workflow-summary.md", text)
    return text
