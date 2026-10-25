# User guide

## 1. Start

```
claude --plugin-dir D:\BA-Agents\ba-agent-plugin
> /ba-agent:start D:\docs\MyProject-BRD.docx
```

Claude looks in the folder you attached (or the one you name) for the BRD, proposes it with an output folder (the folder that contains the BRD) in one question, and starts. Nothing is written elsewhere; your BRD is never changed. A PDF, Word 97 `.doc`, RTF or Excel BRD works too: Claude reads it and keeps a faithful Markdown copy in `source/` - you are not asked to convert anything.

## 2. What you will be asked

1. **Decision owner** — "Who will make/confirm the business decisions for these gaps? Name and role." (Before the first gap is shown.) If you do not know yet you can pause, or continue with analysis only.
2. **Gaps** — one at a time, each as a single prompt with a text box that shows the priority, the question, why it matters, the source and the affected requirement (no separate summary message unless the gap has history). Highest priority first; each prompt shows the gap's priority. In the **Other** box type your answer in your own words (or an instruction such as "explain" or "carry it forward"). The other choices depend on the gap:
   - **Medium and Low gaps** (not security/compliance): **Suggested Answer** (shown only when the BRD, a requirement or an earlier decision supports one — it counts only if you pick it, and it is checked like any other answer), **Explain This Question** (what is missing, why it matters and what it changes in the requirement, with a real-world example) and **Carry Forward**. Carry Forward keeps the gap **open** (never resolved), records who chose it, and lets story generation continue: every story that depends on the gap names it, describes what may change depending on the answer, and is marked *needs clarification* or *not ready for development*. You can revisit it any time.
   - **Critical and High gaps, and any security/compliance-sensitive gap**: **Explain This Question**, **Answer Later** (no reason needed; the gap stays open and is asked again — the stories it blocks stay blocked) or **Defer This Gap** (you give a reason, your name and role, and a revisit date if you have one; the gap is marked DEFERRED and still blocks stories). These gaps cannot be carried forward.
   **Before the first Medium/Low gap** you are asked once: **Go through each** (one gap at a time, as above) or **Carry all forward** (all eligible Medium/Low gaps stay open in one step; you give your name and role once). Critical/High and sensitive gaps are never carried forward this way.
   Your answer is checked against the BRD, related requirements, approved decisions and business rules; if it conflicts you are asked to clarify before the gap can be resolved.
3. **Closing a gap** — when the decision owner answers and the answer is complete and consistent with the BRD and earlier decisions, the gap is closed straight away (you are not asked to confirm again or to give a closer's name) and moves to `gaps/resolved/`. If the answer contradicts the BRD or an earlier decision you are asked to clarify, explicitly confirm a change (named authoriser), or defer. If someone other than the decision owner answered, the owner is asked to confirm.
4. **Baseline** — you confirm the consolidated requirements baseline (recorded as approved by the decision owner unless you name someone else).
5. **Stories** — one at a time, each in a prompt with a text box. A story that depends on an open (carried-forward) gap shows the gap, the unresolved question, its potential impact and its readiness; working assumptions are labelled *not approved*. When you later answer a carried-forward gap (ask Claude to revisit it, or `/ba-agent:review`), the baseline is refreshed, the stories that named the gap are updated with the decision and shown to you again for approval (a changed story is a new version; the version you approved stays in force until you approve the new one). Type **approve**, type your **suggestions**, or type **not approved** (or pick **defer**). Claude records your own words. Suggestions create a new story version that is shown again and needs approval again; a story you did not approve never reaches `stories/approved/`.

## 3. Where things are saved

```
<project>/
  Every folder below has two subfolders:  md/ = files you read,  json/ = records the tool uses
  extracted/    md/brd-outline.md · json/source-metadata.json
  atomic-requirements/  md/REQ-001.md …  json/REQ-001.json …   (atomic requirements; open the md folder)
  gaps/         open/ deferred/ resolved/   (each with md/GAP-001.md and json/GAP-001.json; a gap moves folder when its status changes)
  decisions/    md/DEC-001.md …  json/decision-log.jsonl
  baselines/    md/baseline-v1.md …  json/baseline-v1.json, baseline-manifest.json
  stories/      candidates/  approved/  unapproved/  deferred/  archive/   (each with md/US-001.md and json/US-001.json)
  changes/      md/CHG-001.md …  json/CHG-001.json, change-register.json   (what changed, impact, actions, progress)
  snapshots/    brd/ (accepted BRD text per version)  stories/ (every approved story version + hashes)
  reports/      workflow-summary.md, traceability-report.md
  state/        workflow.json, audit.jsonl (IST), pending/, backups/, inbox/
```
**Files are saved as each stage finishes**, straight into your project folder, and Claude tells you the folder to open before it asks you to review or approve (no copying needed). Say "where are my files?" at any time and it lists every folder.

**Only `stories/approved/` holds approved work** (open `stories/approved/md/`). Stories you marked *not approved* are saved in `stories/unapproved/` (with your note in their review history). Gaps are saved to `gaps/open/md/` as soon as they are found and move to `gaps/resolved/md/` when you answer them (deferred ones wait in `gaps/deferred/md/`). `archive/` keeps every superseded story version. `START-HERE.md` at the top of the folder lists every folder with its current count, so you can open the folder and see where things stand without asking.

## 4. Resume, status, review

- `/ba-agent:status [folder]` — stage, counts, blockers, validation, and the next step.
- `/ba-agent:review [folder]` — carry on where you left off: it looks at the status and continues with the next gaps or stories (or a stale baseline / impact review).
- `/ba-agent:triage-gaps [folder]` — see **all** unresolved gaps in one list (OPEN and DEFERRED: priority, deferral reason, decision owner, linked requirements and stories, whether they block stories), then go through them one by one. For each gap you can type an answer (it is checked against the BRD, the requirements and earlier decisions before the gap can be closed), ask for an explanation, defer it (reason, your name and role, optional revisit date), carry it forward (MEDIUM/LOW only), or keep it as it is. A deferred gap stays deferred, and visible, until you choose to answer it.
- `/ba-agent:changes [folder]` — check the BRD and your approved stories for changes, see the impact first, then decide (section 5).
- `/ba-agent:triage-stories [folder]` — see **all** stories that are not approved yet (pending review, not approved, deferred) in one list, with their readiness, open gaps and whether anything stops you approving them now, then go through them one by one (if resolving gaps made the baseline stale, it first asks you once to confirm a refreshed baseline, because no story can be approved without it): **approve**, **edit** (type your suggestions: this makes a new version that comes back for your approval), **defer** or mark **not approved**. Deferred stories are kept in `stories/deferred/` and listed again next time.
- Triage never decides for you: nothing is approved, resolved or deferred without your explicit words, and stopping half-way loses nothing — run the command again and the remaining items are listed.
- Closing the session loses nothing. Re-run `/ba-agent:start <project folder>`. State is shared between sessions/devices only if they can all open the same folder.

## 5. Changes: the BRD, or a story you already approved

Every time you resume (`/ba-agent:start <project folder>`, `/ba-agent:review`) and before the gap and story lists are shown, Claude runs a **change check**. You can also run it any time with `/ba-agent:changes`.

- **What it compares:** the BRD against the copy of its text stored when you last accepted it (`snapshots/brd/`), and every approved story against exactly what was approved (`snapshots/stories/`).
- **What you are told first (nothing is changed yet):** what was added, modified or deleted (old and new wording side by side), when it was detected, which requirements, open gaps, confirmed decisions, stories (approved ones named) and baseline it touches, and what you need to do. The same is saved as `changes/CHG-001.md`.
- **Formatting-only edits** (bold, bullets, spacing, re-wrapped lines in the BRD; whitespace in a story) are accepted automatically and logged — you are not asked to review them. A changed word, number or rule is always treated as a real change.
- **You decide:** *Apply impact* (Claude asks for your name and role and your own words) or *Review later* (nothing changes; it is raised again next time). Applying marks the affected requirements, gaps, stories and the baseline as needing review — it approves nothing and removes no approval. Confirmed decisions are listed for you to re-validate, not cancelled.
- **If you edited an approved story by hand** (the `.json` or the readable `.md`): applying the change puts back exactly what was approved, keeps your edit word for word (`snapshots/stories/…observed-CHG-001…`) and brings it back as the next version for you to approve, change or discard (`story revert-edit`). If you deleted an approved story, its approved record is kept and you choose to restore it or confirm the removal.
- **Until it is reconciled:** `status` shows `Changes: …`, affected items show as stale / under review, the core refuses to write new gap, baseline or story artifacts on top of an unreviewed change, and the workflow cannot be completed. Every step is in the audit log (IST) and `story history US-001` lists each approved version with who approved it and in which words.

## 6. Closure audit (what is recorded for every resolved gap)

Closer name + role · decision owner name + role · respondent (if different) · decision ID · closure summary · conflict/completeness validation result · closure time in **IST (Asia/Kolkata, UTC+05:30)** from the computer's clock, stored as e.g. `2026-10-22T18:45:00+05:30`. When the answer arrived and when the gap was formally closed are stored separately.

## 7. Running the core by hand (optional)

```
python scripts/ba.py --root <project> status
python scripts/ba.py --root <project> validate
python scripts/ba.py --root <project> trace --write
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
| `IMPACT_REVIEW_PENDING` | The BRD (or a decision) changed; finish the impact review. |
| `CHANGES_UNREVIEWED` | A change was detected that you have not seen yet. Claude shows it; apply it or choose *Review later*, then continue. |
| `CHANGE_RECORD_STALE` | The BRD or story changed again after the impact was shown; run the change check again. |
| `UNSUPPORTED_FORMAT` | Export the BRD to `.docx`, `.md` or `.txt`. |
| `python` not found | Install Python 3.9+ (the plugin installs nothing itself). |
