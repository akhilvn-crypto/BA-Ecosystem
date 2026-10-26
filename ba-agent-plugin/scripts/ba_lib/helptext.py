"""`help [group [action]]`: the CLI reference, generated from the argparse definitions plus one line of purpose per command.

Options and positionals come from the parser itself, so they cannot drift from the code; `PURPOSE` carries only what a
parser cannot say. A test asserts that every command has a purpose. Error codes are not listed here: every error carries an
instructive `error.message`.
"""
from __future__ import annotations

import argparse

PURPOSE = {
    "init": "Create the project from a requirements folder or one .md file (--requirements): read-only index of every requirement, stage SOURCE_INDEXED. --recursive reads sub-folders; --locator sets the ID pattern; --enriched-dir moves the generated copies; --copy-source keeps a copy of the files in source/. Returns warnings, batches and delegate.",
    "status": "Stage, counts, blockers, enriched-copy sync, validation and the NEXT action (text; --json for data).",
    "find": "Needs no project: BA projects and folders that hold requirement .md files (with a count of requirements found).",
    "files": "Read-only: every artifact folder with files, absolute md/ and json/ paths and counts, and the enriched folder.",
    "manifest": "Compare the project tree with state/manifest.json (the state committed at the end of the last stage): in_sync and drift {edited, added, missing}. --write records the current tree; --full lists every sha256. Global --changed-files adds changed_files/removed_files to any command. --stage-list returns the files to stage into a cloud workspace (no backups, inbox or md views).",
    "bridge": "`bridge on` records in state/bridge.json that every command reports changed_files/removed_files (Cowork file bridge), so --changed-files need not be repeated; `bridge off` clears it; no argument shows it.",
    "validate": "Integrity checks (schemas, references, audit trail, source and enriched-copy drift).",
    "trace": "Requirement -> gaps -> acceptance-criteria coverage with file:line, orphans and dangling references (--write saves the report).",
    "recover": "Roll forward interrupted multi-file work and re-render stale enriched copies.",
    "complete": "Finish the workflow, or report PARTIAL with the outstanding items.",
    "report": "Write reports/workflow-summary.md.",
    "fingerprint": "sha256 of a file.",
    "help": "This reference: `help` lists groups, `help <group>` its actions, `help <group> <action>` one command.",
    "source index": "The accepted index: files, requirement counts, sizes (chars), batches and delegate. --delegate-above / --batch-chars override the limits. Reads state; does not re-read the files (use `changes check`).",
    "source check": "Alias of `changes check`.",
    "source history": "Stored versions of every requirement file (snapshots/source/).",
    "changes check": "Compare the requirement files with the accepted index, every approved criteria record with what was approved and every enriched copy with what was generated. Changes nothing downstream (formatting-only differences are accepted and recorded; text outside a block is restored). Returns CHG records: what changed, classification, impact, required_actions.",
    "changes apply": "After the user saw the impact: source change -> new text accepted, criteria and gaps flagged, workflow reopened, enriched copies re-rendered; hand-edited criterion in an enriched copy -> kept, becomes the next version for review, approved version stays; deleted record noted. Needs identity and their words.",
    "changes list": "Change register (optionally by status).",
    "changes show": "One change record.",
    "changes status": "Detected / open / stale overview (read-only).",
    "changes dismiss": "Close a new_requirement or ac_removal item with a reason and identity.",
    "req list": "Active requirements: key, title, live status (READY | READY_WITH_OPEN_GAPS | BLOCKED), blocking gaps and where their criteria stand.",
    "req show": "One requirement: its text (read from the accepted copy), open gaps, decisions, criteria state and what the document already says about acceptance.",
    "req pairs": "Cross-requirement duplicate/conflict candidates across all files.",
    "owner set": "Decision owner (default, or per gap with --gap). Name and role come from the user.",
    "owner unknown": "The owner is not known yet: --choice pause|analysis-only.",
    "owner show": "Decision owner and history.",
    "gaps add": "Add gaps from a JSON list (see the gap-analysis stage); each is filed in gaps/open at once. impacted_req_keys take keys or unique IDs; evidence_quote is checked against the requirement text.",
    "gaps commit": "Finish gap analysis (--cross-pass-done after the cross-requirement pass).",
    "gaps next": "Present gap(s); needs the owner. Packet = lean fields + present {inline, card, dialog}; relay it. --id presents one chosen gap (DEFERRED and carried-forward are returned unchanged). --carried revisits carried-forward gaps. --fallback adds the numbered text for when the dialog tool fails.",
    "gaps list": "Gaps with status, priority and carried_forward flag.",
    "gaps triage": "Review-order list (read-only): one-line summary, sparse rows, owner reported once.",
    "gaps carry-forward-all": "Carry every eligible MEDIUM/LOW non-material gap forward in one step. Needs identity.",
    "gap show": "One gap record.",
    "gap answer": "Store the raw answer (or the evidence-backed suggestion with --use-suggestion); returns candidate_conflicts. Never resolves.",
    "gap explain": "Facts for Explain This Question; records no answer.",
    "gap carry-forward": "MEDIUM/LOW non-material only: the gap stays OPEN, stops blocking acceptance criteria, shown in the enriched copy. Needs identity.",
    "gap followup": "The answer is incomplete.",
    "gap conflict": "Record a conflict between an answer and requirements/decisions (--with REQ-003,DEC-001).",
    "gap resolve": "The ONLY way to RESOLVED: validated answer, interpretation, conflict status. Closer options are optional (the decision owner's own answer closes it). Writes decision + audit atomically and moves the gap to gaps/resolved. A CHANGE_CONFIRMED conflict with a requirement records that the document must change upstream; it never edits it.",
    "gap defer": "Reason and identity mandatory; --revisit-by mandatory when policy says so. Deferred is not resolved.",
    "gap answer-later": "Keep OPEN and ask again later.",
    "gap skip": "Alias of answer-later.",
    "gap revisit": "Deferred or carried-forward -> ask again (history kept).",
    "gap reopen": "Reopen a resolved gap (reason and identity).",
    "gap keep": "Triage 'keep': open gap = answer later; deferred or carried-forward left untouched, decision audited.",
    "policy set": "Human-accepted policy: non-blocking MEDIUM/LOW gaps, and which priorities need a revisit date on deferral.",
    "ac add": "Add candidate acceptance-criteria records from a JSON list (see the acceptance-criteria stage): req_key, Given/When/Then criteria, one open_gap_refs entry per open gap on the requirement. Needs gap analysis committed; a BLOCKED requirement is refused.",
    "ac show": "The raw record.",
    "ac gaps": "Per requirement: live status and the open gaps its criteria must declare, plus what the document already states. Run before writing criteria.",
    "ac triage": "Review-order list (read-only): summary, preflight problems that block every record, per-record blockers.",
    "ac keep": "Triage 'keep as is': no change, decision audited.",
    "ac next": "Present a record (marks CANDIDATE as IN_REVIEW). Returns meta + present {card, dialog}; the dialog withholds Approve while blocked. --full adds the raw record. --fallback adds numbered text for when the dialog tool fails.",
    "ac approve": "Exact version, the human's words in --statement; writes to acceptance/approved and into the enriched copy.",
    "ac edit": "New version from a JSON of changed fields (acceptance_criteria, open_gap_refs, assumptions, notes); needs new approval.",
    "ac defer": "Pending or not-approved record -> acceptance/deferred.",
    "ac revisit": "Deferred or not-approved record back to review at the same version (not an approval).",
    "ac history": "Every approved version with approver, words and snapshot.",
    "ac restore": "Bring back deleted approved criteria from their snapshot.",
    "ac revert-edit": "Discard an unapproved edited version, keeping the approved one in force.",
    "ac archive": "Move the criteria of a requirement that was removed from the document to the archive (reason and identity).",
    "ac request-changes": "Not approved -> acceptance/unapproved.",
    "ac confirm-unaffected": "Clear an impact flag when the criteria are truly unaffected (re-pins them to the changed requirement text; refused if the cause is a resolved or new gap).",
    "doc sync": "Re-render the enriched copies (all, or the file holding --key). Refuses while a copy was hand-edited (run `changes check`); --force overwrites after the edit is recorded.",
    "doc check": "Read-only: is each enriched copy IN_SYNC, STALE, MISSING or DRIFT (hand-edited)?",
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
