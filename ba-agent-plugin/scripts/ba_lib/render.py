"""Render JSON records to Markdown using templates/*.md (string.Template)."""
from __future__ import annotations

from pathlib import Path
from string import Template

from .util import human_ist

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "templates"
_tcache: dict[str, Template] = {}


def _tpl(name: str) -> Template:
    if name not in _tcache:
        _tcache[name] = Template((TEMPLATE_DIR / f"{name}.md").read_text(encoding="utf-8"))
    return _tcache[name]


def _csv(items) -> str:
    items = [str(i) for i in (items or [])]
    return ", ".join(items) if items else "None"


def _numbered(items) -> str:
    return "\n".join(f"{n}. {i}" for n, i in enumerate(items or [], 1)) or "None"


def _labels(p, keys) -> list[str]:
    """Requirement keys as the short names a person reads (the key itself when there is no project to ask)."""
    if p is None:
        return list(keys or [])
    lab = {}
    try:
        from . import reqindex
        lab = reqindex.labels(p)
    except Exception:
        pass
    return [lab.get(k, k) for k in (keys or [])]


def gap_md(g: dict, p=None) -> str:
    answers = []
    for a in g.get("answers", []):
        who = a.get("respondent_name") or "unidentified respondent"
        role = f" ({a['respondent_role']})" if a.get("respondent_role") else ""
        note = f" — _{a['assessment']}_" + (f": {a['assessment_note']}" if a.get("assessment_note") else "") if a.get("assessment") else ""
        answers.append(f"- **{a['answer_id']}** {human_ist(a['received_at_ist'])} by {who}{role}{note}\n  > {a['response']}")
    conflicts = [f"- {c['conflict_id']} [{c['status']}] with {', '.join(c['with'])}: {c['description']}"
                 for c in g.get("conflicts", [])]
    history = [f"- {human_ist(h['at_ist'])}: {h.get('from') or '—'} → {h['to']}" + (f" ({h['note']})" if h.get("note") else "")
               for h in g.get("history", [])]
    owner = g.get("decision_owner_override") or {"name": g.get("decision_owner_name"), "role": g.get("decision_owner_role")}
    d = g.get("deferral")
    cf = g.get("carry_forward")
    evidence = g["evidence"] + (f"\n\n> {g['evidence_quote']}" if g.get("evidence_quote") else "")
    return _tpl("gap").safe_substitute(
        gap_id=g["gap_id"], title=g["title"], category=g["category"], priority=g["priority"], status=g["status"],
        impacted_req_keys=_csv(_labels(p, g["impacted_req_keys"])),
        decision_owner=(f"{owner['name']} — {owner['role']}" if owner.get("name") else "Not yet identified"),
        material="Yes" if g.get("material") else "No", description=g["description"], evidence=evidence,
        why_it_matters=g["why_it_matters"], question=g["question"], answers="\n".join(answers) or "None yet",
        conflicts="\n".join(conflicts) or "None",
        closed_at=human_ist(g.get("closed_at_ist")),
        closed_by=(f"{g['closed_by_name']} — {g['closed_by_role']}" if g.get("closed_by_name") else "—"),
        respondent=(f"{g['respondent_name']} — {g['respondent_role']}" if g.get("respondent_name") else "—"),
        decision_id=g.get("decision_id") or "—", closure_summary=g.get("closure_summary") or "—",
        closure_validation=(f"{g['closure_validation']['status']} at {human_ist(g['closure_validation']['validated_at_ist'])}"
                            if g.get("closure_validation") else "—"),
        deferral=(f"Deferred {human_ist(d['at_ist'])} by {d.get('by_name') or '—'} — {d.get('by_role') or '—'}. "
                  f"Decision owner: {d.get('decision_owner_name') or '—'} — {d.get('decision_owner_role') or '—'}. "
                  f"Reason: {d.get('reason') or 'not given'}. Waiting on: {d.get('owner') or '—'}. Revisit by: {d.get('revisit_by') or '—'}"
                  if d else "Not deferred")
                 + "".join(f"\n- Earlier deferral {human_ist(x['at_ist'])} by {x.get('by_name') or '—'}: {x.get('reason') or 'not given'}"
                           for x in g.get("deferral_history", [])),
        carry_forward=(f"CARRIED FORWARD — still OPEN, not resolved. Since {human_ist(cf['at_ist'])} by {cf.get('by_name') or '—'} — "
                       f"{cf.get('by_role') or '—'}. Decision owner: {cf.get('decision_owner_name') or '—'} — "
                       f"{cf.get('decision_owner_role') or '—'}. Reason: {cf.get('reason') or 'not given'}. "
                       f"Affected requirements: {_csv(_labels(p, cf.get('affected_req_keys')))}" if cf else "Not carried forward")
                    + "".join(f"\n- Earlier carry-forward {human_ist(x['at_ist'])} by {x.get('by_name') or '—'}, ended {human_ist(x.get('ended_at_ist'))} "
                              f"({x.get('ended_by') or '—'})" for x in g.get("carry_forward_history", [])),
        history="\n".join(history) or "None")


def decision_md(d: dict, current_status: str = "ACTIVE", p=None) -> str:
    cc = d["conflict_check"]
    cc_text = f"{cc['status']}; conflicting artifacts: {_csv(cc['conflicting_artifact_ids'])}"
    if d.get("change_authorization"):
        ca = d["change_authorization"]
        cc_text += f"\nChange authorised by {ca['name']} ({ca['role']}): {ca['statement']}"
    return _tpl("decision").safe_substitute(
        decision_id=d["decision_id"], gap_id=d.get("gap_id") or "—", timestamp_ist=human_ist(d["timestamp_ist"]),
        validation_status=d["validation_status"], supersedes=_csv(d.get("supersedes_decision_ids") or
                                                                 ([d["supersedes_decision_id"]] if d.get("supersedes_decision_id") else [])),
        current_status=current_status, question=d["question"], human_response=d["human_response"].replace("\n", "\n> "),
        validated_interpretation=d["validated_interpretation"],
        decision_owner=f"{d['decision_owner_name']} — {d['decision_owner_role']}",
        respondent=(f"{d['respondent_name']} — {d['respondent_role']}" if d.get("respondent_name") else "—"),
        confirmed_by=f"{d['confirmed_by_name']} — {d['confirmed_by_role']}", conflict_check=cc_text)


READINESS_TEXT = {"READY_FOR_DEVELOPMENT": "Ready for development", "NEEDS_CLARIFICATION": "NEEDS CLARIFICATION — open gaps below",
                  "NOT_READY": "NOT READY FOR DEVELOPMENT — blocked on open gaps below"}


def _unresolved_md(a: dict) -> str:
    refs = a.get("open_gap_refs") or []
    if not refs:
        return "None — every gap affecting this requirement is resolved."
    out = []
    for u in refs:
        line = (f"- **{u['gap_id']}** ({u.get('priority', '?')}{', carried forward' if u.get('carried_forward') else ''}) — OPEN, "
                f"NOT an approved decision. Question: {u.get('question') or '—'}\n  - Potential impact: {u['potential_impact']}\n"
                f"  - Effect on readiness: {u['readiness_effect'].replace('_', ' ').title()}")
        if u.get("interim_assumption"):
            line += f"\n  - Working assumption (NOT approved, to be confirmed): {u['interim_assumption']}"
        out.append(line)
    return "\n".join(out)


def ac_md(a: dict, p=None) -> str:
    notes = a.get("notes") or {}
    ap = a.get("approval")
    approval = ""
    if ap:
        who = ap.get("approver_name") or "approver not identified"
        role = f" ({ap['approver_role']})" if ap.get("approver_role") else ""
        approval = f"\n**Approved:** v{ap['version']} on {human_ist(ap['at_ist'])} by {who}{role}"
    hist = [f"- v{h['version']} {h['action']} — {human_ist(h['at_ist'])}" + (f" — {h['note']}" if h.get("note") else "")
            for h in a.get("review_history", [])]
    label = (_labels(p, [a["req_key"]]) or [a["req_key"]])[0]
    return _tpl("ac").safe_substitute(
        req_key=a["req_key"], label=label, title=a.get("requirement_title") or "", status=a["status"].title(), version=a["version"],
        decision_ids=_csv(a["decision_ids"]), assumptions=_csv(a["assumptions"] or ["None"]),
        open_gap_ids=_csv([u["gap_id"] for u in a.get("open_gap_refs") or []]), approval=approval,
        readiness=READINESS_TEXT.get(a.get("readiness") or "", "Not assessed"), unresolved_decisions=_unresolved_md(a),
        acceptance_criteria=_numbered(a["acceptance_criteria"]),
        business_rules=notes.get("business_rules") or "—", validation_exceptions=notes.get("validation_exceptions") or "—",
        non_functional=notes.get("non_functional") or "—", review_history="\n".join(hist) or "None")


def change_md(r: dict) -> str:
    """Readable change record: what changed, why it matters, what must happen next."""
    imp = r.get("impact") or {}
    out = [f"# {r['change_id']} — {r['summary']}", "",
           f"- **Status:** {r['status']}  ·  **Classification:** {r['classification'].replace('_', ' ').title()}  ·  **Origin:** {r['origin']}",
           f"- **Detected:** {human_ist(r['detected_at_ist'])}" + (f" (during `{r['detected_during']}`)" if r.get("detected_during") else "")]
    sub = r.get("subject") or {}
    if r["type"] == "SOURCE_CHANGE":
        f = sub.get("files") or {}
        out.append(f"- **Requirements fingerprint:** `{sub.get('previous_fingerprint')}` → `{sub.get('new_fingerprint')}`")
        out.append(f"- **Files:** added {_csv(f.get('added'))}; changed {_csv(f.get('changed'))}; removed {_csv(f.get('removed'))}")
        out.append("- The supplied requirement files are never modified by the BA Agent.")
    else:
        out.append(f"- **Requirement:** {sub.get('req_key')} · approved v{sub.get('approved_version')} · {sub.get('change_type')}"
                   + (f" via {sub['edited_via']}" if sub.get("edited_via") else ""))
    out += ["", "## What changed"]
    for c in r.get("changes") or []:
        kind = c.get("change", "CHANGE")
        if c.get("req_key") and kind in ("ADDED", "DELETED", "MODIFIED", "FORMATTING_ONLY"):
            out.append(f"- **{kind}** requirement {c['req_key']} “{c.get('title', '')}”")
            for h in c.get("hunks") or []:
                out += [f"  - removed: {x}" for x in h["removed"]] + [f"  - added: {x}" for x in h["added"]]
            if c.get("excerpt"):
                out.append(f"  - {c['excerpt']}")
            if c.get("note"):
                out.append(f"  - _{c['note']}_")
        elif kind in ("FILE_ADDED", "FILE_REMOVED"):
            out.append(f"- **{kind.replace('_', ' ')}** {c.get('file')}" + (f" ({c['note']})" if c.get("note") else ""))
        elif c.get("fields"):
            out.append(f"- **{kind}** {c.get('req_key', '')}")
            for field, d in c["fields"].items():
                if "added" in d or "removed" in d:
                    out += [f"  - {field}: removed “{x}”" for x in d.get("removed", [])] + [f"  - {field}: added “{x}”" for x in d.get("added", [])]
                    if d.get("reordered"):
                        out.append(f"  - {field}: reordered")
                else:
                    out.append(f"  - {field}: “{d.get('before')}” → “{d.get('after')}”")
        else:
            out.append(f"- {kind} {c.get('req_key', '')} {c.get('note', '')}".rstrip())
    if not r.get("changes"):
        out.append("- nothing recorded")
    out += ["", "## Impact"]
    if r["type"] == "SOURCE_CHANGE":
        ar = imp.get("affected_requirements") or []
        out.append("- Changed or removed requirements: " + ("; ".join(f"{a['req_key']} ({a['impact'].replace('_', ' ').lower()})" for a in ar) or "none"))
        out.append("- New requirements: " + (", ".join(x["req_key"] for x in imp.get("new_requirements") or []) or "none"))
        out.append("- Open gaps on them: " + (", ".join(f"{g['gap_id']} [{g['priority']}]" for g in imp.get("open_gaps") or []) or "none"))
        out.append("- Decisions to re-validate (not invalidated automatically): " + (", ".join(imp.get("decisions_to_revalidate") or []) or "none"))
        out.append("- Acceptance criteria: " + (", ".join(f"{s['req_key']} v{s['version']} ({s['bucket']})" for s in imp.get("linked_acs") or []) or "none"))
        out.append("- Approved criteria whose approval stays on record but need review: " + (", ".join(imp.get("approved_acs_at_risk") or []) or "none"))
        w = imp.get("workflow") or {}
        if w.get("will_reopen_to"):
            out.append(f"- Workflow: {w.get('stage')} → will reopen to {w['will_reopen_to']}")
    elif imp.get("req_key"):
        out.append(f"- Fields changed: {imp.get('summary_of_fields') or 'none'}")
        cr = imp.get("criteria") or {}
        out.append(f"- Criteria: +{len(cr.get('added', []))} / -{len(cr.get('removed', []))}" + (" (reordered)" if cr.get("reordered") else ""))
        out.append("- Open gaps on this requirement: " + (", ".join(g["gap_id"] for g in imp.get("open_gaps_on_requirement") or []) or "none"))
        ap = imp.get("approval_record") or {}
        if ap.get("version"):
            out.append(f"- Approval on record: v{ap['version']} by {ap.get('approver_name') or '—'} ({ap.get('approver_role') or '—'}), "
                       f"{human_ist(ap.get('approved_at_ist'))} — preserved")
    else:
        out.append("- none (formatting only)")
    out += ["", "## Required actions"] + ([f"{n}. {a}" for n, a in enumerate(r.get("required_actions") or [], 1)] or ["- none"])
    if r.get("items"):
        out += ["", "## Progress", "| Item | State | Closed |", "|---|---|---|"]
        for i in r["items"]:
            out.append(f"| {i['text']} | {i['state']} | {human_ist(i['closed_at_ist']) if i.get('closed_at_ist') else '—'}"
                       f"{' by ' + i['closed_by'] if i.get('closed_by') and i['closed_by'] != 'workflow' else ''} |")
    ap = r.get("applied")
    if ap:
        out += ["", "## Applied", f"- {human_ist(ap['at_ist'])}" + (f" by {ap['by_name']} ({ap['by_role']}): “{ap['statement']}”" if ap.get("by_name") else " (direct library call)")]
    out += ["", "## History"] + [f"- {human_ist(h['at_ist'])}: {h['event']}" + (f" — {h['note']}" if h.get("note") else "") for h in r.get("history") or []]
    return "\n".join(out) + "\n"
