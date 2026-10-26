"""START-HERE.md: one page at the top of the project folder that says where things are and where the project stands.

It is a view of the folders and `state/workflow.json`, rebuilt after every writing command, so a Product Manager who opens the
folder (rather than the chat) can always see what was saved and where. Safe to delete; never edit it - it is overwritten."""
from __future__ import annotations

from .util import atomic_write_text, human_ist, ist_now_iso

NAME = "START-HERE.md"

STAGE_TEXT = {
    "INITIALIZED": "The requirements are being read.",
    "SOURCE_INDEXED": "The requirements are indexed. Gaps are being looked for.",
    "GAP_ANALYSIS": "Gaps are being looked for.",
    "WAITING_FOR_GAP_INPUT": "Gaps are waiting for answers from the decision owner.",
    "AC_DRAFTING": "Acceptance criteria are being drafted for the requirements that are not blocked.",
    "WAITING_FOR_AC_REVIEW": "Acceptance criteria are waiting for your review.",
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
    owner = st.get("decision_owner")
    reqs = p.requirements()
    files = sorted({e["file"] for e in reqs})
    enriched = p.enriched_root
    try:
        enriched_link = f"[enriched]({enriched.relative_to(p.root).as_posix()})"
    except ValueError:
        enriched_link = f"`{enriched}`"
    lines = [f"# {st['project_id']} — start here", "",
             f"Updated {human_ist(ist_now_iso()).replace(' (Asia/Kolkata, UTC+05:30)', '')}. "
             "Everything is saved here automatically as you go — there is nothing to copy or export.", "",
             f"**Where you are:** {STAGE_TEXT.get(st['stage'], st['stage'])}  ",
             f"**Decision owner:** {owner['name'] + ' — ' + owner['role'] if owner else 'not named yet'}  ",
             f"**Your requirements (never changed by the BA Agent):** `{st['source']['path']}`  ",
             f"**Enriched copies of them** (the same text plus approved acceptance criteria and open gaps): {enriched_link}  ",
             "**To carry on:** run `/ba-agent:start` and choose this folder (or `/ba-agent:review`).", "",
             "| What | How many | Open |", "|---|---|---|",
             _row("Requirements found", f"{len(reqs)} in {len(files)} file(s)", "index"),
             _row("Enriched copies", len(files), None, enriched_link),
             _row("Open gaps (waiting for an answer, including carried forward)", _count(p, "gaps/open", "GAP-*.md"), "gaps/open"),
             _row("Deferred gaps", _count(p, "gaps/deferred", "GAP-*.md"), "gaps/deferred"),
             _row("Resolved gaps", _count(p, "gaps/resolved", "GAP-*.md"), "gaps/resolved"),
             _row("Decisions", _count(p, "decisions", "DEC-*.md"), "decisions"),
             _row("Acceptance criteria waiting for review", _count(p, "acceptance/candidates", "*.md"), "acceptance/candidates"),
             _row("Approved acceptance criteria", _count(p, "acceptance/approved", "*.md"), "acceptance/approved"),
             _row("Acceptance criteria not approved", _count(p, "acceptance/unapproved", "*.md"), "acceptance/unapproved"),
             _row("Deferred acceptance criteria", _count(p, "acceptance/deferred", "*.md"), "acceptance/deferred"),
             _row("Changes found in the requirements or in approved criteria", _count(p, "changes", "CHG-*.md"), "changes"),
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
