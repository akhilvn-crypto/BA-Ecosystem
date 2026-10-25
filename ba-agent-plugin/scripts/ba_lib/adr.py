"""ADR-LOG.md: who decided / approved what, and when (IST), at the top of every artifact folder.

The log is a view of `state/audit.jsonl` (the single source of truth); it is rebuilt from scratch after every writing
command, so it can never drift from the audit trail and is safe to delete."""
from __future__ import annotations

from .util import atomic_write_text, human_ist

LOG_NAME = "ADR-LOG.md"

# event type -> (label, folders whose log lists it). "" is the project root, which lists every decision.
_GAPS, _DEC, _REQ, _BASE, _CHG = "gaps", "decisions", "atomic-requirements", "baselines", "changes"
EVENTS = {
    "GAP_RESOLVED": ("Gap closed, decision recorded", (_GAPS, _DEC)),
    "GAP_ANSWER_RECEIVED": ("Answer received", (_GAPS,)),
    "GAP_REOPENED": ("Gap reopened", (_GAPS, _DEC)),
    "GAP_CARRIED_FORWARD_BULK": ("Gaps carried forward", (_GAPS,)),
    "GAP_DECISION_OWNER_OVERRIDE": ("Decision-owner override", (_GAPS, _DEC)),
    "DECISION_OWNER_SET": ("Decision owner set", (_DEC,)),
    "DECISION_OWNER_CHANGED": ("Decision owner changed", (_DEC,)),
    "POLICY_CHANGED": ("Policy changed", (_GAPS, _DEC)),
    "REQUIREMENTS_COMMITTED": ("Requirements committed", (_REQ,)),
    "REQUIREMENT_REMOVED": ("Requirement removed", (_REQ,)),
    "BASELINE_APPROVED": ("Baseline approved", (_BASE,)),
    "STORY_APPROVED": ("Story approved", ("stories/approved",)),
    "STORY_CHANGES_REQUESTED": ("Story not approved (changes requested)", ("stories/unapproved",)),
    "STORY_DEFERRED": ("Story deferred", ("stories/deferred",)),
    "STORY_IMPACT_CLEARED": ("Story impact cleared", ("stories/approved", "stories/candidates")),
    "STORY_RESTORED": ("Approved story restored", ("stories/approved", _CHG)),
    "SOURCE_CHANGE_ACCEPTED": ("BRD change accepted", (_CHG,)),
    "SOURCE_CHANGE_WITHDRAWN": ("BRD change withdrawn", (_CHG,)),
    "CHANGE_APPLIED": ("Change applied", (_CHG,)),
    "CHANGE_ITEM_DISMISSED": ("Change item dismissed", (_CHG,)),
}


def _person(name, role) -> str:
    return f"{name} — {role}" if name and role else (name or "—")


def _who(ev: dict) -> str:
    for key in ("closed_by", "respondent", "owner", "decision_owner"):
        v = ev.get(key)
        if isinstance(v, dict) and v.get("name"):
            return _person(v["name"], v.get("role"))
    for n, r in (("approver_name", "approver_role"), ("by_name", "by_role"), ("respondent_name", "respondent_role")):
        if ev.get(n):
            return _person(ev[n], ev.get(r))
    return "not recorded"


def _what(ev: dict) -> str:
    t = ev["event_type"]
    if t.startswith("STORY_"):
        v = ev.get("version")
        return f"{ev.get('story_id', '')}" + (f" v{v}" if v else "")
    if t == "GAP_RESOLVED":
        return f"{ev.get('gap_id')} → {ev.get('decision_id')}"
    if t == "GAP_CARRIED_FORWARD_BULK":
        return ", ".join(ev.get("gap_ids") or [])
    if t == "BASELINE_APPROVED":
        return f"baseline v{ev.get('version')}"
    if t == "REQUIREMENT_REMOVED":
        return ev.get("requirement_id", "")
    if t == "REQUIREMENTS_COMMITTED":
        return f"{ev.get('count')} requirements"
    return ev.get("gap_id") or ev.get("change_id") or ev.get("decision_id") or ev.get("item_id") or ""


def _why(ev: dict) -> str:
    for key in ("statement", "closer_confirmation", "closure_summary", "reason", "note", "choice"):
        if ev.get(key):
            return str(ev[key])
    return ""


def _cell(s) -> str:
    return " ".join(str(s).split()).replace("|", "\\|")


def _render(folder: str, events: list[dict]) -> str:
    title = folder or "project"
    out = [f"# ADR log — {title}", "",
           "Who approved or decided what, and when. All times are IST (Asia/Kolkata, UTC+05:30). "
           "Generated from `state/audit.jsonl` after every change; do not edit — your edits are overwritten.", ""]
    if not events:
        out += ["_No approvals or decisions recorded here yet._", ""]
        return "\n".join(out)
    out += ["| # | When (IST) | What happened | Item | By | Statement / reason |", "|---|---|---|---|---|---|"]
    for i, ev in enumerate(events, 1):
        out.append(f"| {i} | {human_ist(ev.get('at_ist')).replace(' (Asia/Kolkata, UTC+05:30)', '')} | {EVENTS[ev['event_type']][0]} | "
                   f"{_cell(_what(ev))} | {_cell(_who(ev))} | {_cell(_why(ev))} |")
    return "\n".join(out) + "\n"


def log_folders() -> tuple[str, ...]:
    """Folders that carry a log: every artifact folder, with the three gap folders (open/deferred/resolved) sharing the one in `gaps/`."""
    from .project import SPLIT_DIRS  # late: project imports this module
    return ("gaps",) + tuple(d for d in SPLIT_DIRS if not d.startswith("gaps/"))


def refresh(p) -> None:
    """Rewrite ADR-LOG.md in the project root and in every artifact folder, only when its content changed."""
    events = [e for e in p.audit_events() if e.get("event_type") in EVENTS]
    events.sort(key=lambda e: e.get("at_ist", ""))
    targets = {"": events}
    for d in log_folders():
        targets[d] = [e for e in events if d in EVENTS[e["event_type"]][1]]
    for folder, evs in targets.items():
        base = p.root / folder if folder else p.root
        if not base.is_dir():
            continue
        target, text = base / LOG_NAME, _render(folder, evs)
        try:
            if target.exists() and target.read_text(encoding="utf-8") == text:
                continue
            atomic_write_text(target, text)
        except OSError:
            continue  # a convenience view: never fail a workflow command because it could not be written
