---
name: change-detection
description: Detect changes to the BRD or to approved user stories (edited by a person or by the BA Agent), explain their downstream impact before anything is updated, tell material changes from formatting-only ones, and walk the review until every affected artifact is reconciled. Use when the pipeline starts or resumes, before gap or story triage, before writing updated artifacts, when `status` shows a change, or when the user says the BRD or a story was edited.
---

# Change detection and impact analysis

The core keeps a **snapshot of the accepted BRD** and, for every approved story, a **hash and an immutable copy of exactly what was approved** (`snapshots/`). A change is anything that makes the files differ from those. Detection and explanation are free of side effects; only a named person can make the core act on a change.

## When to run `changes check`
- **Pipeline start / resume** (right after `status`), **before gap or story triage**, and before any step that writes artifacts. The core also runs it by itself in front of `req add`, `gaps add|commit|next`, `gap resolve`, `baseline create|confirm`, `story add|next|approve|edit` and refuses with `CHANGES_UNREVIEWED` while a material change has not been shown to the user — it never works around this; you show the change.
- After the user says they edited the BRD or a story.

## What it tells you
`python … changes check` → for each change a record (`changes/CHG-nnn.md` is the readable copy): **what changed** (sections added / modified / deleted with the old and new lines; story fields), **when detected**, **impact** (affected requirements, open gaps, confirmed decisions to re-validate, linked and approved stories, baseline, workflow stage), **required actions**.
- `MATERIAL` — the meaning changed (a word, number, rule, a section added or removed, an acceptance criterion). Needs the user's decision.
- `FORMATTING_ONLY` — layout/markup/whitespace/re-wrapping/bold only (BRD) or whitespace/Markdown decoration (story). The core accepts these itself, records them (`RECORDED`, audit event) and nothing is flagged: **do not ask the user to review them**; mention them in one line.

## The loop (material changes)
1. **Show** each change in chat: one line summary, the old → new text, then the impact as a short list (requirements, gaps, decisions, stories incl. *approved* ones whose approval stays on record, baseline) and the required actions. Say plainly that **nothing downstream has changed yet**. The original BRD is only ever read.
2. **Ask** (short `AskUserQuestion`; details stay in chat — see `../ba-workflow/references/choice-prompts.md`): `Apply impact`, `Review later`, plus `Open CHG file` if they want the detail. For an edited approved story: `Apply (keep edit as new version)`, `Review later`. `Review later` changes nothing; the change stays `DETECTED` and is raised again next time. Silence is not consent.
3. **Apply** only on an explicit yes, with the person's name, role and own words: `changes apply --id CHG-nnn --by-name … --by-role … --statement "<their words>"`. Never invent them. This is not an approval of the new content — it authorises the core to mark the affected artifacts stale / under review:
   - BRD change → requirements in changed/deleted sections `NEEDS_REVIEW`, linked gaps and stories flagged (approved stories **keep their approval** and are flagged with the reason), baseline stale, workflow reopened to gap analysis, fingerprint **not** replaced yet.
   - Edited approved story → what was approved is put back unchanged (the edit is kept verbatim in `snapshots/stories/…observed-CHG-nnn.*`), the edit becomes the next version as a `CANDIDATE` that needs re-approval, the approval history is untouched. If the edit cannot become a valid version the approved story is flagged for review instead.
   - Approved story deleted → its approved record stays in `snapshots/`; the user decides: `story restore US-nnn --reason … --by-name … --by-role …` or confirm removal (`changes dismiss CHG-nnn --item story_removal:US-nnn --reason … --by-name … --by-role …`).
4. **Work the open items** with the normal skills — each is closed by the real work, tracked in `changes status` / `changes show CHG-nnn`:
   - requirements: `req update --cause source-change` / `req confirm` / `req remove` (read changed text with `source section SID --allow-changed`), then `source accept`; new sections → add requirements or `changes dismiss --item new_section:<key>` with a reason;
   - gaps: re-check flagged gaps, `gaps commit --cross-pass-done`; decisions listed for re-validation are **not** invalidated automatically — reopen one (`gap reopen`) only if the user decides the new text contradicts it;
   - baseline: `baseline create` → confirm (`requirements-baseline`); stories: `story edit` or `story confirm-unaffected`, then `story-review`;
   - edited story: approve the new version, edit further, or discard it with `story revert-edit US-nnn --reason … --by-name … --by-role …` (the approved version stays in force; the discarded version is archived).
5. When the last item is closed the record becomes `RECONCILED` (audit `CHANGE_RECONCILED`). `complete` stays PARTIAL until every record is reconciled.

## Rules
- Never edit state/story files to "fix" a change, never delete a change record, never re-run `changes apply` to hide something. A newer BRD edit supersedes an unapplied record (`SUPERSEDED`); putting the file back reverts it (`REVERTED`) — both are recorded.
- Changes made by you through `story edit` are recorded too (origin `BA_AGENT`); an edit that only changes wording layout of an approved story keeps its approval and version.
- `story history US-nnn` shows every approved version with approver, their words and the snapshot; `source history` lists the BRD versions.
- Projects created before change detection have no BRD snapshot yet: the core stores one on the first check while the BRD still matches (line-level diffs are unavailable if it already changed; say so). Approved stories are adopted as-is the first time.
- Same commands in Claude Code and Cowork. Cowork has no hooks: the checks run because the core runs them, and `/ba-agent:start`, `/ba-agent:review` and `/ba-agent:changes` call `changes check` first. Use the numbered-list fallback where `AskUserQuestion` is unavailable.
