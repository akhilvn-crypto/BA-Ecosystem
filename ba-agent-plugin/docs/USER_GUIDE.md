# User guide

## 1. Start

```
claude --plugin-dir D:\BA-Agents\ba-agent-plugin
> /ba-agent:start D:\docs\MyProject-BRD.docx
```

Claude greets you, proposes an output folder (default `<BRD folder>/ba-output/<name>`) and asks you to confirm it. Nothing is written elsewhere; your BRD is never changed. If the BRD is a PDF you will be asked for a `.docx`/`.md`/`.txt` export.

## 2. What you will be asked

1. **Decision owner** — "Who will make/confirm the business decisions for these gaps? Name and role." (Before the first gap is shown.) If you do not know yet you can pause, or continue with analysis only.
2. **Gaps** — up to three at a time, each with evidence, why it matters, and one question. Answer in your own words, or: ask for a rewording, **defer**, or **skip for now**.
3. **Closing a gap** — Claude summarises the meaning it will record and asks you to confirm and to state your name and role as the person closing it. If the answer contradicts the BRD or an earlier decision you are asked to clarify, explicitly confirm a change (named authoriser), or defer.
4. **Baseline** — you confirm the consolidated requirements baseline.
5. **Stories** — one at a time: **approve**, **edit**, or **defer**. Say "approve" in your own words; Claude records them. Edits create a new version that needs approval again.

## 3. Where things are saved

```
<project>/
  extracted/    BRD outline + fingerprint metadata
  requirements/ REQ-001.json|md …              (atomic requirements)
  gaps/         gap-register.json, GAP-001.md …
  decisions/    decision-log.jsonl, DEC-001.md …
  baselines/    baseline-v1.json|md, baseline-manifest.json
  stories/      candidates/  approved/  deferred/  archive/
  reports/      workflow-summary.md, traceability-report.md
  state/        workflow.json, audit.jsonl (IST), pending/, backups/, inbox/
```
**Only `stories/approved/` holds approved work.** `archive/` keeps every superseded story version.

## 4. Resume, status, review

- `/ba-agent:status [folder]` — stage, counts, blockers, validation, and the next step.
- `/ba-agent:review [folder]` — continue answering gaps, reviewing stories, revisiting deferred items.
- Closing the session loses nothing. Re-run `/ba-agent:start <project folder>`. State is shared between sessions/devices only if they can all open the same folder.

## 5. If the BRD changes

Run `/ba-agent:start <project folder>` (or ask for a source check). Claude compares section by section, marks only affected requirements for review, flags related stories/baseline, and asks you to accept the reassessed change. Earlier decisions and versions stay on record.

## 6. Closure audit (what is recorded for every resolved gap)

Closer name + role · decision owner name + role · respondent (if different) · decision ID · closure summary · conflict/completeness validation result · closure time in **IST (Asia/Kolkata, UTC+05:30)** from the computer's clock, stored as e.g. `2026-10-22T18:45:00+05:30`. When the answer arrived and when the gap was formally closed are stored separately.

## 7. Running the core by hand (optional)

```
python scripts/ba.py --root <project> status
python scripts/ba.py --root <project> validate
python scripts/ba.py --root <project> trace --write
python scripts/ba.py --root <project> recover
```
Reference: `skills/ba-workflow/references/cli-reference.md`.

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
| `UNSUPPORTED_FORMAT` | Export the BRD to `.docx`, `.md` or `.txt`. |
| `python` not found | Install Python 3.9+ (the plugin installs nothing itself). |
