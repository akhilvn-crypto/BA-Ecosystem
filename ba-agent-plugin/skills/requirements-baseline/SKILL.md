---
name: requirements-baseline
description: Consolidate requirements, decisions and outstanding gaps into a versioned baseline and get explicit human confirmation. Use when gap resolution is done or the baseline is stale.
---

# Requirements baseline

1. `baseline create [--summary "<what changed>"]`.
   - Needs: gap analysis committed, no pending BRD-change review, no interrupted closure. Otherwise follow the error.
   - Unchanged inputs ⇒ `reused: true` (nothing regenerated). Otherwise a new `baseline-vN` (`PROPOSED`) with: requirements (each `READY` or `BLOCKED by GAP-…`), confirmed decisions, outstanding gaps with status and impact, source fingerprint, change summary vs. the previous version. Previous versions are kept.
2. **Strict default policy:** any unresolved gap (deferred, open, waiting, reopened) blocks stories for the requirements it affects; CRITICAL/HIGH and material (scope/policy/security/compliance) gaps always, whether they are open, answered later or deferred. Say clearly which requirements are blocked and why. Offer: resolve/revisit those gaps, or — only for MEDIUM/LOW non-material gaps — let the human **Carry Forward** each one in `gap-resolution` (`gap carry-forward`; the gap stays OPEN), or accept a project-wide policy (`policy set --carry-forward true --by-name … --by-role … --statement "<their words>"`), then `baseline create` again.
   A requirement whose only unresolved gaps are carried forward is `READY` with `open_gap_ids`: say so ("REQ-008 is ready, but GAP-002 is still open; its stories will be flagged"). Carried-forward gaps appear in the outstanding-gaps list marked `CARRIED FORWARD`, never as decisions. Carrying a gap forward, revisiting one, or resolving a carried gap marks the baseline stale: create and confirm a new version before generating or updating stories.
3. Show a 2-3 line summary (ready vs. blocked, new decisions, changes since previous) and the full path of the saved baseline: `baselines/md/baseline-vN.md` (the record is `baselines/json/baseline-vN.json`). `files_saved` in the result is the proof it exists; the user reviews that file, so give them the path before asking for confirmation. `baseline confirm` rewrites the same files with the approval details; tell them again where they are.
4. Ask for explicit confirmation with an `AskUserQuestion` (`Confirm vN`, `Not now`; short plain-text question, summary stays in chat — see `../ba-workflow/references/choice-prompts.md`), then get name and role in chat: *"Confirm baseline vN as the working baseline? (name and role please)"*. Then `baseline confirm --version N --by-name … --by-role … --statement "<their words>"`. Do not confirm on their behalf.
5. Confirming flags existing stories whose requirements changed or became blocked for impact review.
6. A baseline made stale by a BRD change (`changes apply`) is refreshed only after the changed requirements are reassessed and `source accept` has run; the change record's `baseline` item closes when the new version is confirmed (`change-detection`).

Next: `user-story-generation`.
