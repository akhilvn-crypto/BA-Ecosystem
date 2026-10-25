"""`help [group [action]]`: the CLI reference, generated from the argparse definitions plus one line of purpose per command.

Options and positionals come from the parser itself, so they cannot drift from the code; `PURPOSE` carries only what a
parser cannot say. A test asserts that every command has a purpose. Error codes are not listed here: every error carries an
instructive `error.message`.
"""
from __future__ import annotations

import argparse

PURPOSE = {
    "init": "Create the project, fingerprint and outline the BRD (md/txt/docx), stage BRD_PARSED. --copy-source copies, never moves, the BRD into source/. Returns outline, batches and delegate.",
    "status": "Stage, counts, blockers, validation and the NEXT action (text; --json for data).",
    "find": "Needs no project: BA projects and likely BRDs in a folder. readable_directly=false means PDF/DOC/RTF/XLSX: use `convert`.",
    "convert": "Needs --root only: PDF/DOC/RTF/XLSX -> source/<name>.md with local tools (CONVERTER_UNAVAILABLE -> transcribe by hand). Then `init` that .md.",
    "files": "Read-only: every artifact folder with files, absolute md/ and json/ paths and counts.",
    "manifest": "Compare the project tree with state/manifest.json (the state committed at the end of the last stage): in_sync and drift {edited, added, missing}. --write records the current tree; --full lists every sha256. Global --changed-files adds changed_files/removed_files to any command. --stage-list returns the files to stage into a cloud workspace (no backups, inbox or md views).",
    "bridge": "`bridge on` records in state/bridge.json that every command reports changed_files/removed_files (Cowork file bridge), so --changed-files need not be repeated; `bridge off` clears it; no argument shows it.",
    "validate": "Integrity checks (schemas, references, audit trail).",
    "trace": "Requirement-to-story coverage, orphans and duplicates (--write saves the report).",
    "recover": "Roll forward interrupted multi-file work.",
    "complete": "Finish the workflow, or report PARTIAL with the outstanding items.",
    "report": "Write reports/workflow-summary.md.",
    "fingerprint": "sha256 of a file.",
    "help": "This reference: `help` lists groups, `help <group>` its actions, `help <group> <action>` one command.",
    "source check": "Alias of `changes check`.",
    "source accept": "Accept the reassessed BRD (stores the new snapshot). Needs --by-name --by-role --statement.",
    "source section": "Fetch ONE section's text (context-efficient). --allow-changed after a BRD edit.",
    "source outline": "Section sizes (chars), batches and delegate. --delegate-above / --batch-chars override the limits.",
    "source history": "Stored BRD versions.",
    "changes check": "Compare the BRD with its accepted snapshot and every approved story with what was approved. Changes nothing downstream (formatting-only differences are accepted and recorded). Returns CHG records: what changed, classification, impact, required_actions.",
    "changes apply": "After the user saw the impact: BRD change -> requirements NEEDS_REVIEW, gaps/stories flagged, baseline stale; edited approved story -> approved version restored, edit kept as next CANDIDATE; deleted story recorded. Needs identity and their words.",
    "changes list": "Change register (optionally by status).",
    "changes show": "One change record.",
    "changes status": "Detected / open / stale overview (read-only).",
    "changes dismiss": "Close a new_section or story_removal item with a reason and identity.",
    "req add": "Add requirements from a JSON list (see the atomic-requirements stage). Quotes are checked against the BRD; one bad item saves nothing. Returns IDs and advisory duplicate/conflict flags.",
    "req commit": "Validate the decomposition, stage REQUIREMENTS_READY.",
    "req pairs": "Cross-requirement duplicate/conflict candidates.",
    "req list": "Requirements: id, title, statement, type, status, version, source.",
    "req show": "One requirement.",
    "req update": "New requirement version (old kept). --cause decision|source-change|correction.",
    "req confirm": "After a BRD change: the requirement is still valid.",
    "req remove": "After a BRD change: the requirement is dropped (kept as REMOVED).",
    "owner set": "Decision owner (default, or per gap with --gap). Name and role come from the user.",
    "owner unknown": "The owner is not known yet: --choice pause|analysis-only.",
    "owner show": "Decision owner and history.",
    "gaps add": "Add gaps from a JSON list (see the gap-analysis stage); each is filed in gaps/open at once.",
    "gaps commit": "Finish gap analysis (--cross-pass-done after the cross-requirement pass).",
    "gaps next": "Present gap(s); needs the owner. Packet = lean fields + present {inline, card, dialog}; relay it. --id presents one chosen gap (DEFERRED and carried-forward are returned unchanged). --carried revisits carried-forward gaps. --fallback adds the numbered text for when the dialog tool fails.",
    "gaps list": "Gaps with status, priority and carried_forward flag.",
    "gaps triage": "Review-order list (read-only): one-line summary, sparse rows, owner reported once.",
    "gaps carry-forward-all": "Carry every eligible MEDIUM/LOW non-material gap forward in one step. Needs identity.",
    "gap show": "One gap record.",
    "gap answer": "Store the raw answer (or the evidence-backed suggestion with --use-suggestion); returns candidate_conflicts. Never resolves.",
    "gap explain": "Facts for Explain This Question; records no answer.",
    "gap carry-forward": "MEDIUM/LOW non-material only: the gap stays OPEN, stops blocking stories, baseline marked stale. Needs identity.",
    "gap followup": "The answer is incomplete.",
    "gap conflict": "Record a conflict between an answer and requirements/decisions (--with REQ-003,DEC-001).",
    "gap resolve": "The ONLY way to RESOLVED: validated answer, interpretation, conflict status. Closer options are optional (the decision owner's own answer closes it). Writes decision + audit atomically and moves the gap to gaps/resolved.",
    "gap defer": "Reason and identity mandatory; --revisit-by mandatory when policy says so. Deferred is not resolved.",
    "gap answer-later": "Keep OPEN and ask again later.",
    "gap skip": "Alias of answer-later.",
    "gap revisit": "Deferred or carried-forward -> ask again (history kept).",
    "gap reopen": "Reopen a resolved gap (reason and identity).",
    "gap keep": "Triage 'keep': open gap = answer later; deferred or carried-forward left untouched, decision audited.",
    "policy set": "Human-accepted policy: non-blocking MEDIUM/LOW gaps, and which priorities need a revisit date on deferral.",
    "baseline create": "Versioned baseline (PROPOSED). Unchanged inputs are reused.",
    "baseline confirm": "Human approval of version N (default approver: the decision owner). --statement is their words.",
    "baseline show": "A baseline version.",
    "story add": "Add candidate stories from a JSON list (see the user-story-generation stage). One unresolved_decisions entry per open gap touching the story.",
    "story show": "The raw story.",
    "story gaps": "Unresolved gaps a story for these requirements must declare. Run before writing stories.",
    "story triage": "Review-order list (read-only): summary, preflight problems that block every story, per-story blockers.",
    "story keep": "Triage 'keep as is': no change, decision audited.",
    "story next": "Present a story (marks CANDIDATE as IN_REVIEW). Returns meta + present {card, dialog}; the dialog withholds Approve while blocked. --full adds the raw story. --fallback adds numbered text for when the dialog tool fails.",
    "story approve": "Exact version, the human's words in --statement; writes to stories/approved.",
    "story edit": "New version from a JSON of changed fields; needs new approval.",
    "story defer": "Pending or not-approved story -> stories/deferred.",
    "story revisit": "Deferred or not-approved story back to review at the same version (not an approval).",
    "story history": "Every approved version with approver, words and snapshot.",
    "story restore": "Bring back a deleted approved story from its snapshot.",
    "story revert-edit": "Discard an unapproved edited version, keeping the approved one in force.",
    "story request-changes": "Not approved -> stories/unapproved.",
    "story confirm-unaffected": "Clear an impact flag when the requirements are truly unchanged (refused if the cause is a resolved or new gap).",
}


def _commands(ap: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    out = {}
    for act in ap._actions:
        if isinstance(act, argparse._SubParsersAction):
            out.update(act.choices)
    return out


def _usage(sp: argparse.ArgumentParser) -> str:
    parts = []
    for a in sp._actions:
        if isinstance(a, (argparse._HelpAction, argparse._SubParsersAction)):
            continue
        if not a.option_strings:
            parts.append(a.dest.upper())
            continue
        flag = a.option_strings[0] + ("" if a.nargs == 0 else " N" if a.type is int else " X")
        parts.append(flag if a.required else f"[{flag}]")
    return " ".join(parts)


def commands(ap: argparse.ArgumentParser) -> list[tuple[str, argparse.ArgumentParser]]:
    """Every runnable command as ('group action' | 'name', its parser)."""
    rows = []
    for name, sp in _commands(ap).items():
        subs = _commands(sp)
        rows += [(f"{name} {a}", s) for a, s in subs.items()] if subs else [(name, sp)]
    return rows


def render(ap: argparse.ArgumentParser, group: str | None = None, action: str | None = None) -> dict:
    rows = commands(ap)
    if not group:
        groups: dict[str, list[str]] = {}
        for cmd, _ in rows:
            g, _, a = cmd.partition(" ")
            groups.setdefault(g, []).append(a)
        return {"usage": "ba.py [--root DIR] [--args-file FILE.json] [--pretty] <group> <action> [options]",
                "groups": {g: (" · ".join(a) if a[0] else PURPOSE.get(g, "")) for g, a in groups.items()},
                "more": "`help <group>` lists a group's commands with options; `help <group> <action>` shows one."}
    want = f"{group} {action}" if action else group
    hits = [(c, s) for c, s in rows if c == want or c.startswith(want + " ")]
    if not hits:
        raise_unknown(group, action, rows)
    return {"commands": [{"cmd": c, "usage": _usage(s), "does": PURPOSE.get(c, "")} for c, s in hits]}


def raise_unknown(group, action, rows):
    from .util import BAError
    raise BAError("USAGE", f"No command '{(group + ' ' + (action or '')).strip()}'. Run `help` for the groups.",
                  groups=sorted({c.split()[0] for c, _ in rows}))
