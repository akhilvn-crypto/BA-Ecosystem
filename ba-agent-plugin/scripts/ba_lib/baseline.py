"""Versioned requirements baseline."""
from __future__ import annotations

from pathlib import Path

from . import gaps as gapmod, ingest
from .project import physical
from .util import BAError, canonical_json, human_ist, ist_now_iso, normalize_ws, require_identity, sha256_text

ACTIVE = ("ACTIVE",)


def inputs_hash(p) -> str:
    reqs = [(r["requirement_id"], r["version"], r["statement"], r["status"]) for r in p.requirements()]
    decs = [(d["decision_id"], d["validated_interpretation"], d["current_status"]) for d in p.decisions().values()]
    gps = [(g["gap_id"], g["status"], g["priority"], tuple(g["impacted_requirement_ids"]))
           + (("carried-forward",) if g.get("carry_forward") else ()) for g in p.gaps()]
    return sha256_text(canonical_json({"r": reqs, "d": decs, "g": gps, "policy": p.state["policy"]}))


def _fingerprint_ok(p) -> None:
    src = Path(p.state["source"]["path"])
    if not src.exists():
        return  # warn-only: derived artifacts remain valid, but the status report flags it
    meta = ingest.extract(src)
    if meta["fingerprint"] != p.state["source"]["fingerprint"]:
        raise BAError("SOURCE_CHANGED", "The BRD changed since analysis. Run `source check` first.")


def create(p, summary: str | None = None) -> dict:
    st = p.state
    if st["stage"] in ("INITIALIZED", "BRD_PARSED", "REQUIREMENTS_READY", "PAUSED", "BLOCKED"):
        raise BAError("INVALID_STAGE", f"Cannot baseline from stage {st['stage']}.")
    if st["impact_review"].get("pending"):
        raise BAError("IMPACT_REVIEW_PENDING", "A BRD change is awaiting impact review (`source accept`).")
    if not st.get("gap_analysis_complete"):
        raise BAError("GAP_ANALYSIS_INCOMPLETE", "Complete gap analysis first (`gaps commit --cross-pass-done`).")
    if list(p.path("state/pending").glob("*.closure.json")):
        raise BAError("PENDING_JOURNAL", "An interrupted gap closure exists. Run `recover` first.")
    if any(r["status"] == "NEEDS_REVIEW" for r in p.requirements()):
        raise BAError("IMPACT_REVIEW_INCOMPLETE", "Some requirements still need review.")
    _fingerprint_ok(p)
    m = p.baseline_manifest()
    h = inputs_hash(p)
    latest = m["versions"][-1] if m["versions"] else None
    if latest and latest["inputs_hash"] == h and latest["status"] in ("PROPOSED", "APPROVED") and not m["stale"]:
        return {"reused": True, "version": latest["version"], "status": latest["status"],
                "note": "Inputs unchanged since this baseline; nothing regenerated."}
    blocked = gapmod.blocked_requirements(p)
    open_ok = gapmod.open_nonblocking_requirements(p)
    reqs = [r for r in p.requirements() if r["status"] == "ACTIVE"]
    decisions = {k: v for k, v in p.decisions().items() if v["current_status"] == "ACTIVE"}
    version = (latest["version"] + 1) if latest else 1
    now = ist_now_iso()
    entries = []
    for r in reqs:
        entry = {k: r[k] for k in ("requirement_id", "title", "statement", "type", "version", "source_reference",
                                   "source_section_ids", "related_decision_ids", "related_gap_ids", "actors", "preconditions",
                                   "outcomes", "constraints", "dependencies", "acceptance_info", "assumptions")}
        entry["baseline_status"] = "BLOCKED" if r["requirement_id"] in blocked else "READY"
        entry["blocked_by_gap_ids"] = blocked.get(r["requirement_id"], [])
        entry["open_gap_ids"] = open_ok.get(r["requirement_id"], [])  # READY, but these gaps are still unresolved
        entries.append(entry)
    outstanding = [dict({k: g[k] for k in ("gap_id", "title", "priority", "status", "impacted_requirement_ids")},
                        carried_forward=gapmod.is_carried(p, g)) for g in p.gaps() if g["status"] != "RESOLVED"]
    prev = p.read_json(f"baselines/baseline-v{latest['version']}.json") if latest else None
    pm = {e["requirement_id"]: e for e in prev["requirements"]} if prev else {}
    cm = {e["requirement_id"]: e for e in entries}
    change = {"added": sorted(set(cm) - set(pm)), "removed": sorted(set(pm) - set(cm)),
              "changed": sorted(k for k in cm if k in pm and (cm[k]["version"] != pm[k]["version"])),
              "newly_ready": sorted(k for k in cm if k in pm and pm[k]["baseline_status"] == "BLOCKED" and cm[k]["baseline_status"] == "READY"),
              "new_decisions": sorted(set(decisions) - set(prev["decision_ids"])) if prev else sorted(decisions)}
    doc = {"schema_version": "1.0", "baseline_version": version, "status": "PROPOSED", "created_at": now,
           "source": {"path": p.state["source"]["path"], "fingerprint": p.state["source"]["fingerprint"]},
           "change_summary": normalize_ws(summary or "") or ("Initial baseline" if not prev else "Updated baseline"),
           "changes_since_previous": change, "requirements": entries,
           "decision_ids": sorted(decisions),
           "decisions": [{"decision_id": d["decision_id"], "gap_id": d["gap_id"], "interpretation": d["validated_interpretation"],
                          "timestamp_ist": d["timestamp_ist"], "decision_owner": f"{d['decision_owner_name']} — {d['decision_owner_role']}",
                          "confirmed_by": f"{d['confirmed_by_name']} — {d['confirmed_by_role']}"} for d in decisions.values()],
           "outstanding_gaps": outstanding, "policy": p.state["policy"], "inputs_hash": h}
    p.write_json(f"baselines/baseline-v{version}.json", doc)
    p.write_text(f"baselines/baseline-v{version}.md", _markdown(doc), backup=False)
    m["versions"].append({"version": version, "status": "PROPOSED", "created_at": now, "inputs_hash": h,
                          "file": physical(f"baselines/baseline-v{version}.md"), "change_summary": doc["change_summary"],
                          "ready": sum(1 for e in entries if e["baseline_status"] == "READY"),
                          "blocked": sum(1 for e in entries if e["baseline_status"] == "BLOCKED")})
    m["stale"], m["stale_reason"] = False, None
    p.write_json("baselines/baseline-manifest.json", m)
    p.audit("BASELINE_CREATED", version=version, ready=m["versions"][-1]["ready"], blocked=m["versions"][-1]["blocked"], changes=change)
    if st["stage"] != "REQUIREMENTS_BASELINED":
        p.set_stage("REQUIREMENTS_BASELINED", f"baseline-v{version}-created")
    else:
        p.save_state(f"baseline-v{version}-created")
    return {"reused": False, "version": version, "status": "PROPOSED", "ready_requirements": m["versions"][-1]["ready"],
            "blocked_requirements": m["versions"][-1]["blocked"], "changes": change,
            "ready_with_open_gaps": {e["requirement_id"]: e["open_gap_ids"] for e in entries
                                     if e["baseline_status"] == "READY" and e["open_gap_ids"]},
            "next": "Show the baseline to the user and ask for explicit confirmation (`baseline confirm`)."}


def confirm(p, version: int, by_name: str | None = None, by_role: str | None = None, statement: str | None = None) -> dict:
    basis = "NAMED_APPROVER"
    if not (normalize_ws(by_name or "") or normalize_ws(by_role or "")):
        owner = p.state.get("decision_owner")  # the person already named for this project approves unless someone else is named
        if owner:
            by_name, by_role, basis = owner["name"], owner["role"], "DECISION_OWNER"
    by_name, by_role = require_identity(by_name, by_role, "Baseline approver")
    if not normalize_ws(statement):
        raise BAError("MISSING_METADATA", "The approver's verbatim confirmation is required.")
    m = p.baseline_manifest()
    ent = next((v for v in m["versions"] if v["version"] == version), None)
    if not ent:
        raise BAError("UNKNOWN_BASELINE", f"Baseline v{version} does not exist.")
    if ent["status"] != "PROPOSED" or version != m["versions"][-1]["version"] or m["stale"]:
        raise BAError("BASELINE_NOT_CONFIRMABLE", f"Baseline v{version} is {ent['status']}{' (stale)' if m['stale'] else ''}; "
                      "only the latest, current PROPOSED baseline can be confirmed.")
    for v in m["versions"]:
        if v["status"] == "APPROVED":
            v["status"] = "SUPERSEDED"
    ent["status"], ent["approved_by"] = "APPROVED", {"name": by_name, "role": by_role, "statement": statement, "at_ist": ist_now_iso()}
    m["current_version"] = version
    p.write_json("baselines/baseline-manifest.json", m)
    doc = p.read_json(f"baselines/baseline-v{version}.json")
    doc["status"], doc["approved_by"] = "APPROVED", ent["approved_by"]
    p.write_json(f"baselines/baseline-v{version}.json", doc)
    p.write_text(f"baselines/baseline-v{version}.md", _markdown(doc), backup=False)
    p.state["baseline_version"] = version
    p.audit("BASELINE_APPROVED", version=version, by_name=by_name, by_role=by_role, statement=statement, approver_basis=basis)
    p.save_state(f"baseline-v{version}-approved")
    # re-validate existing stories against the new baseline
    cur = {e["requirement_id"]: e for e in doc["requirements"]}
    flagged = []
    for _, s in p.all_stories():
        affected = {rid for rid in s["requirement_ids"]
                    if rid not in cur or cur[rid]["baseline_status"] != "READY"
                    or cur[rid]["version"] != s.get("requirement_versions", {}).get(rid)}
        if affected:
            flagged += p.flag_stories(affected, f"baseline v{version} changed {sorted(affected)}", "baseline-change")
    return {"version": version, "status": "APPROVED", "stories_flagged_for_impact_review": sorted(set(flagged))}


def _markdown(d: dict) -> str:
    out = [f"# Requirements baseline v{d['baseline_version']} — {d['status']}", "",
           f"- Created: {human_ist(d['created_at'])}",
           f"- Source: `{Path(d['source']['path']).name}` · fingerprint `{d['source']['fingerprint']}`",
           f"- Change summary: {d['change_summary']}"]
    if d.get("approved_by"):
        a = d["approved_by"]
        out.append(f"- Approved by: {a['name']} ({a['role']}) at {human_ist(a['at_ist'])}")
    ch = d["changes_since_previous"]
    out += ["", "## Changes since previous baseline",
            f"- Added: {', '.join(ch['added']) or 'none'}", f"- Changed: {', '.join(ch['changed']) or 'none'}",
            f"- Removed: {', '.join(ch['removed']) or 'none'}", f"- Newly ready: {', '.join(ch['newly_ready']) or 'none'}",
            f"- New decisions: {', '.join(ch['new_decisions']) or 'none'}", "", "## Requirements"]
    for r in d["requirements"]:
        tag = "READY" if r["baseline_status"] == "READY" else f"BLOCKED by {', '.join(r['blocked_by_gap_ids'])}"
        if r["baseline_status"] == "READY" and r.get("open_gap_ids"):
            tag = f"READY with open gaps {', '.join(r['open_gap_ids'])} (stories must name them)"
        out += [f"### {r['requirement_id']}: {r['title']} (v{r['version']}) — {tag}", r["statement"],
                f"- Source: {r['source_reference']}", f"- Decisions: {', '.join(r['related_decision_ids']) or 'none'}", ""]
    out += ["## Confirmed decisions"]
    out += [f"- **{x['decision_id']}** ({x['gap_id']}, {human_ist(x['timestamp_ist'])}; owner {x['decision_owner']}): {x['interpretation']}"
            for x in d["decisions"]] or ["- none"]
    out += ["", "## Outstanding gaps (NOT resolved)"]
    out += [f"- **{g['gap_id']}** [{g['priority']}, {g['status']}{', CARRIED FORWARD' if g.get('carried_forward') else ''}] {g['title']} "
            f"→ affects {', '.join(g['impacted_requirement_ids'])}" for g in d["outstanding_gaps"]] or ["- none"]
    return "\n".join(out) + "\n"
