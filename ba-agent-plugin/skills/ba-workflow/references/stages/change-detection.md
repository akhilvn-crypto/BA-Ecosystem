# Change detection and impact analysis

The core keeps **the exact bytes of every accepted requirement file** (`snapshots/source/`), for every approved criteria record **a hash and an immutable copy of exactly what was approved** (`snapshots/ac/`), and for every enriched copy **a manifest of what it generated** (`state/enriched-manifest.json`). A change is anything that makes the files differ from those. Detection and explanation are free of side effects; only a named person can make the core act on a change. The supplied files are only ever read.

## When to run `changes check`
- **Pipeline start / resume** (right after `status`), **before gap or criteria triage**, and before any step that writes artifacts. The core also runs it by itself in front of `gaps add|commit|next`, `gap resolve` and `ac add|next|approve|edit` and refuses with `CHANGES_UNREVIEWED` while a material change has not been shown to the user — it never works around this; you show the change.
- After the user says they edited a requirement file, approved criteria or an enriched copy, or the upstream agent regenerated the requirements.

## What it tells you
`changes check` → for each change a record (`changes/md/CHG-nnn.md` readable, `changes/json/CHG-nnn.json` the record): **what changed** (requirements added / modified / removed with old and new lines; files added or removed; criteria fields), **when detected**, **impact** (affected requirements, open gaps, confirmed decisions to re-validate, linked and approved criteria, workflow stage), **required actions**.
- `MATERIAL` — the meaning changed (a word, number, rule, a requirement added or removed, an approved criterion). Needs the user's decision.
- `FORMATTING_ONLY` — layout, markup, whitespace, re-wrapping, or text outside the requirements (source); whitespace/Markdown decoration (criteria). The core accepts these itself, records them (`RECORDED`, audit event), re-renders the copies and nothing is flagged: **do not ask the user to review them**; mention them in one line.
- **Enriched copy edited by hand.** Text outside a BA block, a damaged marker, generated text (the open-gaps table, readiness) or a deleted copy is simply put back by the core: the edit is kept in `state/backups/` and recorded (`RECORDED`); tell the user in one line that the source file is where requirement text changes. Only an edit of an **approved criterion inside a block** is material (`MANUAL_EDIT`).

## The loop (material changes)
1. **Show** each change in chat: one line summary, the old → new text, then the impact as a short list (requirements, gaps, decisions, criteria incl. *approved* ones whose approval stays on record) and the required actions. Say plainly that **nothing downstream has changed yet**.
2. **Ask** (short `AskUserQuestion`; details stay in chat — see `../choice-prompts.md`): `Apply impact`, `Review later`, plus `Open CHG file` if they want the detail. For an edited approved criterion: `Apply (keep edit as new version)`, `Review later`. `Review later` changes nothing; the change stays `DETECTED` and is raised again next time. Silence is not consent.
3. **Apply** only on an explicit yes, with the person's name, role and own words: `changes apply --id CHG-nnn --by-name … --by-role … --statement "<their words>"`. Never invent them. This is not an approval of the new content — it authorises the core to act:
   - Source change → the new text becomes the accepted source (new snapshots, index and enriched copies), the criteria of changed or removed requirements and their gaps are flagged (approved criteria **keep their approval** and show *Under review* in the copy), gap analysis is marked incomplete and the workflow reopens to `GAP_ANALYSIS`.
   - Edited approved criterion (record file or block) → what was approved stays in force and in the copy (the edit is kept verbatim in `snapshots/ac/…observed-CHG-nnn.*`); the edit becomes the next version as a `CANDIDATE` that needs re-approval. If it cannot become a valid version the approved record is flagged for review instead.
   - Approved criteria deleted → the approved record stays in `snapshots/`; the user decides: `ac restore <key> --reason … --by-name … --by-role …` or confirm removal (`changes dismiss CHG-nnn --item ac_removal:<key> --reason … --by-name … --by-role …`).
4. **Work the open items** with the normal stages — each is closed by the real work, tracked in `changes status` / `changes show CHG-nnn`:
   - `requirement:<key>` / `new_requirement:<key>`: read the new text (`req show`), find its gaps (`gaps add`), `gaps commit --cross-pass-done`; a new requirement may be dismissed with a reason (`changes dismiss --item new_requirement:<key>`);
   - `gap:<id>`: re-check flagged gaps; decisions listed for re-validation are **not** invalidated automatically — reopen one (`gap reopen`) only if the user decides the new text contradicts it;
   - `ac:<key>`: `ac edit` (new version) or `ac confirm-unaffected`, then `ac-review`; criteria of a removed requirement are archived (`ac archive`);
   - `reapproval:<key>:vN`: approve the new version, edit further, or discard it with `ac revert-edit <key> --reason … --by-name … --by-role …` (the approved version stays in force; the discarded one is archived).
5. When the last item is closed the record becomes `RECONCILED` (audit `CHANGE_RECONCILED`). `complete` stays PARTIAL until every record is reconciled.

## Rules
- Never delete a change record or re-run `changes apply` to hide something (files are changed only through the core: `../artifact-integrity.md`). A newer edit supersedes an unapplied record (`SUPERSEDED`); putting the file back reverts it (`REVERTED`) — both are recorded.
- Changes you make through `ac edit` are recorded too (origin `BA_AGENT`); an edit that only changes wording layout of approved criteria keeps its approval and version.
- `ac history <key>` shows every approved version with approver, their words and the snapshot; `source history` lists the stored versions of each requirement file.
- A requirement whose ID changed upstream shows as one removed and one added requirement (there is no old-key → new-key mapping): re-do gaps and criteria for the new one and archive the old criteria.
- A source file that cannot be read, or that now carries BA markers, assesses nothing: the check says so; nothing is changed.
- Same commands in Claude Code and Cowork. Cowork has no hooks: the checks run because the core runs them, and `/ba-agent:start`, `/ba-agent:review` and `/ba-agent:changes` call `changes check` first. On a file bridge, stage fresh copies of the requirement files and `enriched/` first (`../cowork-sync.md`): the check cannot see edits that were never staged.
