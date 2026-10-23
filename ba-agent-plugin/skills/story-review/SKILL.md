---
name: story-review
description: Present candidate user stories one at a time for human approval, edit, defer or revisit; only explicitly approved versions are stored in the approved folder. Use when stories are awaiting review, when deferred stories are revisited, or when an approved story needs changes.
---

# Story review

Read `../ba-workflow/references/human-approval.md` (Approvals).

Loop (one story at a time; a small batch only if the user asks: `story next --batch N`):
1. `story next` → show the story in the template (title, status, version, requirement/decision IDs, source references, open gaps, user story, acceptance criteria, notes). Mention any `live_open_gap_ids` and impact flags prominently.
2. Ask: **approve · edit · defer**.
   - **Approve** → ask for approver name/role once (optional; never invent) and use their own words: `story approve US-001 --version N --statement "<their words>" [--approver-name … --approver-role …]`. The exact version must be named. Result goes to `stories/approved/`.
   - **Edit** → apply the requested change as JSON of changed fields: `story edit US-001 --file changes.json --reason "…"`. This creates version N+1 (validated again); show it and ask again. If the story was already approved, the approved version stays in force and in history until N+1 is approved.
   - **Defer** → `story defer US-001 --version N --reason "…"` (moves to `stories/deferred/`).
3. **Revisit deferred**: `story next --deferred` (or `story revisit US-001`), then approve/edit/keep deferred, individually.
4. Stories flagged for impact review (BRD, decision or baseline changed): show why; either `story edit` (new version) or, if the requirements are truly unchanged, `story confirm-unaffected US-001 --statement "<reviewer's words>"`.
5. Do not place candidate or deferred stories in `approved/`, and never approve because the user is silent, time passed, or you judge it good.

When the queue is empty run `complete`. If it reports PARTIAL, relay the outstanding items (blocked requirements, deferred stories, uncovered requirements) instead of saying the work is done. Offer the summary at `reports/workflow-summary.md` and how to resume (`/ba-agent:review`).
