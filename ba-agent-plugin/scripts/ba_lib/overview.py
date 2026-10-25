"""START-HERE.md: one page at the top of the project folder that says where things are and where the project stands.

It is a view of the folders and `state/workflow.json`, rebuilt after every writing command, so a Product Manager who opens the
folder (rather than the chat) can always see what was saved and where. Safe to delete; never edit it - it is overwritten."""
from __future__ import annotations

from .util import atomic_write_text, human_ist, ist_now_iso

NAME = "START-HERE.md"

STAGE_TEXT = {
    "INITIALIZED": "The BRD is being read.",
    "BRD_PARSED": "The BRD is read. Atomic requirements are being captured.",
    "REQUIREMENTS_READY": "Requirements are saved. Gaps are being looked for.",
    "GAP_ANALYSIS": "Gaps are being looked for.",
    "WAITING_FOR_GAP_INPUT": "Gaps are waiting for answers from the decision owner.",
    "REQUIREMENTS_BASELINED": "Requirements are baselined. User stories are being drafted.",
    "STORIES_READY": "User stories are drafted and waiting for your review.",
    "WAITING_FOR_STORY_REVIEW": "User stories are waiting for your review.",
    "COMPLETED": "Everything is decided and approved.",
    "PAUSED": "Paused. Run /ba-agent:start on this folder to carry on.",
    "BLOCKED": "Blocked. Run /ba-agent:status on this folder to see why.",
}


def _count(p, folder: str, pattern: str) -> int:
    d = p.root / folder / "md"
    return sum(1 for f in d.glob(pattern) if f.is_file()) if d.is_dir() else 0


def _row(label: str, n, folder: str | None, note: str = "") -> str:
    where = f"[{folder}/md]({folder}/md)" if folder else note
    return f"| {label} | {n} | {where} |"


def render(p) -> str:
    st = p.state
    b = p.baseline_manifest()
    approved_v = st.get("baseline_version")
    baseline = (f"v{approved_v} approved" + (" (needs a refresh)" if b.get("stale") else "")) if approved_v else \
        ("not approved yet" + (f" (latest: v{b['versions'][-1]['version']} {b['versions'][-1]['status'].lower()})" if b["versions"] else ""))
    owner = st.get("decision_owner")
    lines = [f"# {st['project_id']} — start here", "",
             f"Updated {human_ist(ist_now_iso()).replace(' (Asia/Kolkata, UTC+05:30)', '')}. "
             "Everything is saved here automatically as you go — there is nothing to copy or export.", "",
             f"**Where you are:** {STAGE_TEXT.get(st['stage'], st['stage'])}  ",
             f"**Decision owner:** {owner['name'] + ' — ' + owner['role'] if owner else 'not named yet'}  ",
             "**To carry on:** run `/ba-agent:start` and choose this folder (or `/ba-agent:review`).", "",
             "| What | How many | Open |", "|---|---|---|",
             _row("Atomic requirements", _count(p, "atomic-requirements", "REQ-*.md"), "atomic-requirements"),
             _row("Open gaps (waiting for an answer, including carried forward)", _count(p, "gaps/open", "GAP-*.md"), "gaps/open"),
             _row("Deferred gaps", _count(p, "gaps/deferred", "GAP-*.md"), "gaps/deferred"),
             _row("Resolved gaps", _count(p, "gaps/resolved", "GAP-*.md"), "gaps/resolved"),
             _row("Decisions", _count(p, "decisions", "DEC-*.md"), "decisions"),
             _row("Baseline", baseline, "baselines"),
             _row("Stories waiting for review", _count(p, "stories/candidates", "US-*.md"), "stories/candidates"),
             _row("Approved stories", _count(p, "stories/approved", "US-*.md"), "stories/approved"),
             _row("Stories not approved", _count(p, "stories/unapproved", "US-*.md"), "stories/unapproved"),
             _row("Deferred stories", _count(p, "stories/deferred", "US-*.md"), "stories/deferred"),
             _row("Changes found in the BRD or an approved story", _count(p, "changes", "CHG-*.md"), "changes"),
             "", "Who decided or approved what, and when: [ADR-LOG.md](ADR-LOG.md). "
             "Each folder above holds the readable files in `md/` and the tool's records in `json/`.", ""]
    return "\n".join(lines)


def refresh(p) -> None:
    """Rewrite START-HERE.md when its content changed (the timestamp line is ignored). Never fails a workflow command."""
    try:
        text = render(p)
        target = p.root / NAME
        if target.exists():
            old = target.read_text(encoding="utf-8").split("\n", 3)
            if len(old) > 3 and old[3] == text.split("\n", 3)[3]:
                return
        atomic_write_text(target, text)
    except Exception:
        return
