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
        deferral=(f"Deferred {human_ist(d['at_ist'])}. Reason: {d.get('reason') or 'not given'}. "
                  f"Owner: {d.get('owner') or '—'}. Revisit by: {d.get('revisit_by') or '—'}" if d else "Not deferred"),
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
        open_gap_ids=_csv(s["open_gap_ids"]), approval=approval, role=us["role"], capability=us["capability"],
        value=us["value"], acceptance_criteria=_numbered(s["acceptance_criteria"]),
        business_rules=notes.get("business_rules") or "—", validation_exceptions=notes.get("validation_exceptions") or "—",
        non_functional=notes.get("non_functional") or "—", review_history="\n".join(hist) or "None")
