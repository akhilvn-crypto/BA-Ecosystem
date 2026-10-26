# User guide

## 1. Start

```
claude --plugin-dir /path/to/ba-agent-plugin
> /ba-agent:start D:\docs\my-requirements
```

Claude looks in the folder you attached (or the one you name) for requirement files, and if it cannot tell where the requirements are it asks: **a folder, or a single `.md` file?** It proposes a project folder next to them (`ba-output/<name>`) in one question and starts. Your requirement files are never changed and nothing is written inside their folder. Requirements must be Markdown; ask whoever produces them for an `.md` export of anything else.

Each requirement is found by the ID it already carries: a heading such as `### REQ-012 Password reset` (also `FR-2.1`, `AUTH-004`), or a table row whose ID cell holds one. If your files use another ID style Claude asks for the pattern once; if they have no IDs at all, every heading with text is treated as a requirement and identified by its heading path.

## 2. What you will be asked

1. **Decision owner** — "Who will make/confirm the business decisions for these gaps? Name and role." (Before the first gap is shown.) If you do not know yet you can pause, or continue with analysis only.
2. **Gaps** — one at a time, each as a single prompt with a text box that shows the priority, the question, why it matters, the source and the affected requirement (no separate summary message unless the gap has history). Highest priority first. In the **Other** box type your answer in your own words (or an instruction such as "explain" or "carry it forward"). The other choices depend on the gap:
   - **Medium and Low gaps** (not security/compliance): **Suggested Answer** (shown only when the document, a requirement or an earlier decision supports one — it counts only if you pick it, and it is checked like any other answer), **Explain This Question** (what is missing, why it matters and what it changes in the requirement, with a real-world example) and **Carry Forward**. Carry Forward keeps the gap **open** (never resolved), records who chose it, and lets acceptance criteria be written: the criteria for each requirement the gap touches name it, describe what may change depending on the answer, and are marked *needs clarification* or *not ready for development*. You can revisit it any time.
   - **Critical and High gaps, and any security/compliance-sensitive gap**: **Explain This Question**, **Answer Later** (no reason needed; the gap stays open and is asked again — the requirement stays blocked) or **Defer This Gap** (you give a reason, your name and role, and a revisit date if you have one; the gap is marked DEFERRED and still blocks). These gaps cannot be carried forward.
   **Before the first Medium/Low gap** you are asked once: **Go through each** (one gap at a time, as above) or **Carry all forward** (all eligible Medium/Low gaps stay open in one step; you give your name and role once). Critical/High and sensitive gaps are never carried forward this way.
   Your answer is checked against the requirements, approved decisions and business rules; if it conflicts you are asked to clarify before the gap can be resolved.
3. **Closing a gap** — when the decision owner answers and the answer is complete and consistent, the gap is closed straight away (you are not asked to confirm again or to give a closer's name) and moves to `gaps/resolved/`. If the answer contradicts a requirement or an earlier decision you are asked to clarify, explicitly confirm a change (named authoriser), or defer. A confirmed change that contradicts a requirement is recorded, but your requirement file is not edited: it is listed as needing a change upstream. If someone other than the decision owner answered, the owner is asked to confirm.
4. **Acceptance criteria** — for every requirement that is not blocked by an unresolved gap, Claude drafts Given/When/Then criteria from the requirement and the decisions (never inventing values) and shows them to you **one requirement at a time**: the requirement text, the criteria, any open gap with its potential impact, and its readiness. Type **approve**, type your **suggestions**, or type **not approved** (or pick **defer**). Claude records your own words. Suggestions create a new version that is shown again and needs approval again; criteria you did not approve never reach `acceptance/approved/` or the enriched copy. A requirement that is blocked by a Critical/High gap gets no criteria until the gap is resolved.

## 3. Where things are saved

```
<project>/
  Every folder below has two subfolders:  md/ = files you read,  json/ = records the tool uses
  enriched/     your requirement files, copied at the same relative paths, plus under each requirement its approved
                acceptance criteria, its open gaps and a readiness flag (generated: change the source file, not this copy)
  index/        md/requirements-index.md · json/requirements-index.json   (what was found, with key, title and lines)
  gaps/         open/ deferred/ resolved/   (each with md/GAP-001.md and json/GAP-001.json; a gap moves folder when its status changes)
  decisions/    md/DEC-001.md …  json/decision-log.jsonl
  acceptance/   candidates/  approved/  unapproved/  deferred/  archive/   (one record per requirement, each with md/ and json/)
  changes/      md/CHG-001.md …  json/CHG-001.json, change-register.json   (what changed, impact, actions, progress)
  snapshots/    source/ (the exact bytes of every accepted version of each requirement file)  ac/ (every approved version + hashes)
  reports/      workflow-summary.md, traceability-report.md
  state/        workflow.json, audit.jsonl (IST), enriched-manifest.json, pending/, backups/, inbox/
```
**Files are saved as each stage finishes**, straight into your project folder, and Claude tells you the folder to open before it asks you to review or approve (no copying needed). Say "where are my files?" at any time and it lists every folder.

**Only approved criteria appear in `enriched/`** (and in `acceptance/approved/md/`). Criteria you marked *not approved* are saved in `acceptance/unapproved/` (with your note in their review history). Gaps are saved to `gaps/open/md/` as soon as they are found and move to `gaps/resolved/md/` when you answer them (deferred ones wait in `gaps/deferred/md/`); the open ones also appear in the enriched copy, under their requirement. `archive/` keeps every superseded version. `START-HERE.md` at the top of the folder lists every folder with its current count.

A requirement in the enriched copy looks like this (the part between the two comment lines is generated):

```md
### REQ-012 Password reset
A registered user can request a password reset link by email.

<!-- ba:begin key="auth.md::REQ-012" ac="v2" rendered="sha256:…" -->
#### Acceptance Criteria
*Approved by Priya Nair (Product Owner), 2026-10-28 14:05 IST · AC v2*
1. Given a registered user, when they request a reset, then an email with a single-use link is sent.

#### Open Gaps
| Gap | Priority | Status | Question | Potential impact |
|---|---|---|---|---|
| GAP-004 | MEDIUM | OPEN (carried forward) | What is the link expiry? | Criterion 3 cannot state a number |

**Readiness:** NEEDS_CLARIFICATION
<!-- ba:end key="auth.md::REQ-012" -->
```

Take the enriched files from `<project>/enriched/`. They are a view: removing the generated blocks gives your original text back exactly.

## 4. Resume, status, review

- `/ba-agent:status [folder]` — stage, counts, blockers, whether the enriched copies are in step, validation, and the next step.
- `/ba-agent:review [folder]` — carry on where you left off: it looks at the status and continues with the next gaps or criteria (or an impact review).
- `/ba-agent:triage-gaps [folder]` — see **all** unresolved gaps in one list (OPEN and DEFERRED: priority, deferral reason, decision owner, linked requirements, whether they block criteria), then go through them one by one. For each gap you can type an answer, ask for an explanation, defer it (reason, your name and role, optional revisit date), carry it forward (MEDIUM/LOW only), or keep it as it is. A deferred gap stays deferred, and visible, until you choose to answer it.
- `/ba-agent:changes [folder]` — check the requirement files, your approved criteria and the enriched copies for changes, see the impact first, then decide (section 5).
- `/ba-agent:triage-ac [folder]` — see **all** acceptance-criteria records that are not approved yet (pending review, not approved, deferred) in one list, with their readiness, open gaps and whether anything stops you approving them now, then go through them one by one: **approve**, **edit** (type your suggestions: this makes a new version that comes back for your approval), **defer** or mark **not approved**. Deferred records are kept in `acceptance/deferred/` and listed again next time.
- Triage never decides for you: nothing is approved, resolved or deferred without your explicit words, and stopping half-way loses nothing — run the command again and the remaining items are listed.
- Closing the session loses nothing. Re-run `/ba-agent:start <project folder>`. State is shared between sessions/devices only if they can all open the same folder.

## 5. Changes: the requirements, or criteria you already approved

Every time you resume (`/ba-agent:start <project folder>`, `/ba-agent:review`) and before the gap and criteria lists are shown, Claude runs a **change check**. You can also run it any time with `/ba-agent:changes`.

- **What it compares:** each requirement file against the exact copy stored when you last accepted it (`snapshots/source/`), every approved record against exactly what was approved (`snapshots/ac/`), and every enriched copy against what the plugin generated.
- **What you are told first (nothing is changed yet):** which requirements were added, modified or removed (old and new wording side by side), when it was detected, which open gaps, confirmed decisions and criteria (approved ones named) it touches, and what you need to do. The same is saved as `changes/CHG-001.md`.
- **Formatting-only edits** (bold, bullets, spacing, re-wrapped lines, text outside the requirements) are accepted automatically and logged — you are not asked to review them. A changed word, number or rule is always treated as a real change.
- **You decide:** *Apply impact* (Claude asks for your name and role and your own words) or *Review later* (nothing changes; it is raised again next time). Applying accepts the new text and marks the affected gaps and criteria as needing review — it approves nothing and removes no approval. Gap analysis is re-opened for the changed and new requirements, and confirmed decisions are listed for you to re-validate, not cancelled. A requirement whose ID changed shows up as one removed and one added requirement.
- **If you edit an enriched copy by hand:** text outside the generated blocks, or generated text such as the gap table, is put back (your edit is kept in `state/backups/`) — change the requirement in the source file instead. If you edit an **approved criterion inside its block**, applying the change keeps your wording word for word (`snapshots/ac/…observed-CHG-001…`), keeps the approved version in force, and brings your edit back as the next version for you to approve, change or discard (`ac revert-edit`). If you delete approved criteria, the approved record is kept and you choose to restore it or confirm the removal.
- **Until it is reconciled:** `status` shows `Changes: …`, affected items show as under review, the core refuses to write new gap or criteria artifacts on top of an unreviewed change, and the workflow cannot be completed. Every step is in the audit log (IST) and `ac history <key>` lists each approved version with who approved it and in which words.

## 6. Closure audit (what is recorded for every resolved gap)

Closer name + role · decision owner name + role · respondent (if different) · decision ID · closure summary · conflict/completeness validation result · closure time in **IST (Asia/Kolkata, UTC+05:30)** from the computer's clock, stored as e.g. `2026-10-22T18:45:00+05:30`. When the answer arrived and when the gap was formally closed are stored separately.

## 7. Running the core by hand (optional)

```
python scripts/ba.py --root <project> status
python scripts/ba.py --root <project> validate
python scripts/ba.py --root <project> trace --write
python scripts/ba.py --root <project> doc check
python scripts/ba.py --root <project> recover
```
Reference: `python scripts/ba.py help` (groups), `help <group> [<action>]` (options and purpose of each command), generated from the code.

## 8. Fewer permission prompts (optional)

Claude Code asks before running the core script. If you trust this plugin you can allow it in your own settings, e.g. `Bash(python:*)` scoped to the plugin path. The plugin never changes your settings itself.

## 9. Troubleshooting

| Message / symptom | Meaning |
|---|---|
| `OWNER_REQUIRED` | Provide the decision owner's name and role first. |
| `AWAITING_OWNER_CONFIRMATION` | A material answer needs the decision owner's explicit confirmation. |
| `CONFLICT_REVIEW_REQUIRED` | The answer resembles an existing requirement/decision; review it. |
| `CLOSURE_IN_PROGRESS` / validation says "run recover" | A previous run was interrupted; run `recover`. |
| `CHANGES_UNREVIEWED` | A change was detected that you have not seen yet. Claude shows it; apply it or choose *Review later*, then continue. |
| `CHANGE_RECORD_STALE` | A file changed again after the impact was shown; run the change check again. |
| `REQUIREMENT_BLOCKED` | The requirement has an unresolved gap that blocks its criteria; resolve, or (MEDIUM/LOW) carry it forward. |
| `ENRICHED_DRIFT` / `Enriched copy … was changed by hand` | An enriched copy was edited; run the change check, which keeps the edit before restoring the copy. |
| `ENRICHED_FILE_AS_SOURCE` | The files you pointed at contain generated blocks: they are enriched copies. Point at the originals. |
| `OUTPUT_INSIDE_SOURCE` | The project folder must be next to the requirements folder, not inside it. |
| `NO_REQUIREMENTS_FOUND` / `UNSUPPORTED_FORMAT` | No Markdown requirement files found there; ask for a Markdown export. |
| `LEGACY_PROJECT` | A ba-agent 1.x project; use ba-agent 1.10.2 for it or start a new project. |
| `python` not found | Install Python 3.9+ (the plugin installs nothing itself). |
