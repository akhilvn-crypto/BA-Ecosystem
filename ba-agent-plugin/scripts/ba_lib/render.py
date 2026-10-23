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


def _bullets(items, empty="None") -> str:
    items = [str(i) for i in (items or [])]
    return "\n".join(f"- {i}" for i in items) if items else empty


def _numbered(items) -> str:
    return "\n".join(f"{n}. {i}" for n, i in enumerate(items or [], 1)) or "None"


def requirement_md(r: dict) -> str:
    flags = [f"- **{f['kind']}**: {f['note']}" + (f" ({', '.join(f.get('related_ids', []))})" if f.get("related_ids") else "")
             for f in r.get("flags", [])]
    versions = [f"- v{v['version']} ({human_ist(v['changed_at'])}): {v['statement']}" for v in r.get("versions", [])]
    return _tpl("requirement").safe_substitute(
        requirement_id=r["requirement_id"], title=r["title"], status=r["status"], version=r["version"],
        type=r["type"], source_reference=r["source_reference"], source_fingerprint=r["source_fingerprint"],
        created_at=human_ist(r["created_at"]), updated_at=human_ist(r["updated_at"]),
        statement=r["statement"], source_excerpt=(f"> {r['source_excerpt']}" if r.get("source_excerpt") else "Not recorded"),
        actors=_csv(r.get("actors")), preconditions=_csv(r.get("preconditions")), outcomes=_csv(r.get("outcomes")),
        constraints=_csv(r.get("constraints")), dependencies=_csv(r.get("dependencies")),
        acceptance_info=_csv(r.get("acceptance_info")), assumptions=_csv(r.get("assumptions")),
        related_requirement_ids=_csv(r.get("related_requirement_ids")), related_gap_ids=_csv(r.get("related_gap_ids")),
        related_decision_ids=_csv(r.get("related_decision_ids")),
        flags="\n".join(flags) or "None", versions="\n".join(versions) or "Current version only")


def gap_md(g: dict) -> str:
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
    return _tpl("gap").safe_substitute(
        gap_id=g["gap_id"], title=g["title"], category=g["category"], priority=g["priority"], status=g["status"],
        impacted_requirement_ids=_csv(g["impacted_requirement_ids"]),
        decision_owner=(f"{owner['name']} — {owner['role']}" if owner.get("name") else "Not yet identified"),
        material="Yes" if g.get("material") else "No", description=g["description"], evidence=g["evidence"],
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
                       f"Affected requirements: {_csv(cf.get('affected_requirement_ids'))}" if cf else "Not carried forward")
                    + "".join(f"\n- Earlier carry-forward {human_ist(x['at_ist'])} by {x.get('by_name') or '—'}, ended {human_ist(x.get('ended_at_ist'))} "
                              f"({x.get('ended_by') or '—'})" for x in g.get("carry_forward_history", [])),
        history="\n".join(history) or "None")


def decision_md(d: dict, current_status: str = "ACTIVE") -> str:
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


def _readiness(s: dict) -> str:
    return READINESS_TEXT.get(s.get("readiness") or "", "Not assessed (story saved before readiness tracking)")


def _unresolved_md(s: dict) -> str:
    ud = s.get("unresolved_decisions") or []
    if not ud:
        return "None — every gap affecting this story is resolved." if "unresolved_decisions" in s else "Not assessed"
    out = []
    for u in ud:
        line = (f"- **{u['gap_id']}** ({u.get('priority', '?')}{', carried forward' if u.get('carried_forward') else ''}) — OPEN, "
                f"NOT an approved decision. Question: {u.get('question') or '—'}\n  - Potential impact: {u['potential_impact']}\n"
                f"  - Effect on readiness: {u['readiness_effect'].replace('_', ' ').title()}")
        if u.get("interim_assumption"):
            line += f"\n  - Working assumption (NOT approved, to be confirmed): {u['interim_assumption']}"
        out.append(line)
    return "\n".join(out)


def story_md(s: dict) -> str:
    us = s["user_story"]
    notes = s.get("notes") or {}
    ap = s.get("approval")
    approval = ""
    if ap:
        who = ap.get("approver_name") or "approver not identified"
        role = f" ({ap['approver_role']})" if ap.get("approver_role") else ""
        approval = f"\n**Approved:** v{ap['version']} on {human_ist(ap['at_ist'])} by {who}{role}"
    hist = [f"- v{h['version']} {h['action']} — {human_ist(h['at_ist'])}" + (f" — {h['note']}" if h.get("note") else "")
            for h in s.get("review_history", [])]
    return _tpl("user_story").safe_substitute(
        story_id=s["story_id"], title=s["title"], status=s["status"].title(), version=s["version"],
        requirement_ids=_csv(s["requirement_ids"]), source_references=_csv(s["source_references"]),
        decision_ids=_csv(s["decision_ids"]), dependencies=_csv(s["dependencies"]), assumptions=_csv(s["assumptions"] or ["None"]),
        open_gap_ids=_csv(s["open_gap_ids"]), approval=approval, readiness=_readiness(s),
        unresolved_decisions=_unresolved_md(s), role=us["role"], capability=us["capability"],
        value=us["value"], acceptance_criteria=_numbered(s["acceptance_criteria"]),
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
        out.append(f"- **BRD fingerprint:** `{sub.get('previous_fingerprint')}` → `{sub.get('new_fingerprint')}`")
        out.append("- The original BRD file is never modified by the BA Agent.")
    else:
        out.append(f"- **Story:** {sub.get('story_id')} · approved v{sub.get('approved_version')} · {sub.get('change_type')}"
                   + (f" via {sub['edited_via']}" if sub.get("edited_via") else ""))
    out += ["", "## What changed"]
    for c in r.get("changes") or []:
        kind = c.get("change", "CHANGE")
        if kind in ("ADDED", "DELETED", "MODIFIED", "FORMATTING_ONLY") and c.get("section_key"):
            out.append(f"- **{kind}** section {c.get('section_id', '')} “{c.get('title', c['section_key'])}”")
            for h in c.get("hunks") or []:
                out += [f"  - removed: {x}" for x in h["removed"]] + [f"  - added: {x}" for x in h["added"]]
            if c.get("excerpt"):
                out.append(f"  - {c['excerpt']}")
            if c.get("note"):
                out.append(f"  - _{c['note']}_")
        elif c.get("fields"):
            out.append(f"- **{kind}** {c.get('story_id', '')}")
            for field, d in c["fields"].items():
                if "added" in d or "removed" in d:
                    out += [f"  - {field}: removed “{x}”" for x in d.get("removed", [])] + [f"  - {field}: added “{x}”" for x in d.get("added", [])]
                    if d.get("reordered"):
                        out.append(f"  - {field}: reordered")
                else:
                    out.append(f"  - {field}: “{d.get('before')}” → “{d.get('after')}”")
        else:
            out.append(f"- {kind} {c.get('story_id', '')}".rstrip())
    if not r.get("changes"):
        out.append("- nothing recorded")
    out += ["", "## Impact"]
    if r["type"] == "SOURCE_CHANGE":
        ar = imp.get("affected_requirements") or []
        out.append("- Requirements: " + ("; ".join(f"{a['requirement_id']} ({a['impact'].replace('_', ' ').lower()})" for a in ar) or "none"))
        out.append("- Open gaps: " + (", ".join(f"{g['gap_id']} [{g['priority']}]" for g in imp.get("open_gaps") or []) or "none"))
        out.append("- Decisions to re-validate (not invalidated automatically): " + (", ".join(imp.get("decisions_to_revalidate") or []) or "none"))
        out.append("- Stories: " + (", ".join(f"{s['story_id']} v{s['version']} ({s['bucket']})" for s in imp.get("linked_stories") or []) or "none"))
        out.append("- Approved stories whose approval stays on record but need review: " + (", ".join(imp.get("approved_stories_at_risk") or []) or "none"))
        b = imp.get("baseline") or {}
        out.append(f"- Baseline: approved v{b.get('approved_version')}" + (" → will be marked stale" if b.get("will_be_marked_stale") else ""))
        if imp.get("new_sections_without_requirements"):
            out.append("- New sections with no requirement: " + ", ".join(x["title"] for x in imp["new_sections_without_requirements"]))
    elif imp.get("story_id"):
        rq = imp.get("requirements") or {}
        out.append(f"- Fields changed: {imp.get('summary_of_fields') or 'none'}")
        out.append(f"- Requirements: {', '.join(rq.get('after') or []) or 'none'}"
                   + (f" (newly linked {', '.join(rq['added'])})" if rq.get("added") else "")
                   + (f" (no longer linked {', '.join(rq['removed'])})" if rq.get("removed") else ""))
        out.append("- Open gaps on those requirements: " + (", ".join(g["gap_id"] for g in imp.get("open_gaps_on_requirements") or []) or "none"))
        out.append("- Stories sharing requirements: " + (", ".join(s["story_id"] for s in imp.get("stories_sharing_requirements") or []) or "none"))
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
