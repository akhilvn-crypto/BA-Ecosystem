---
name: story-review
description: Present candidate user stories one at a time for human approval, edit, defer or revisit; only explicitly approved versions are stored in the approved folder. Use when stories are awaiting review, when deferred stories are revisited, or when an approved story needs changes.
---

# Story review

Read `../ba-workflow/references/human-approval.md` (Approvals) and `../ba-workflow/references/choice-prompts.md` (how to show the dialog).

## Present ONE story at a time, with a response box
`story next` (default `--batch 1`) returns the single story to review. Never list several stories in one message: the user concentrates on one story, answers, then sees the next. Say where they are ("Story 2 of 8") so progress is visible. Only if the user explicitly asks for a batch, use `story next --batch N`.

Two steps, in the same turn:
1. **Chat message** (normal markdown) with the full story: `US-003 · title · vN (Story 2 of 8)`; the story (As a … I want … so that …); the acceptance criteria (Given/When/Then, short); requirement/decision IDs and source reference; any `live_open_gap_ids` or impact flag, prominently.
2. **`AskUserQuestion`** (load it with ToolSearch `select:AskUserQuestion` if it is not already callable) so the user gets a prompt with a text box. Keep the dialog short and plain-text; do not repeat the story in it (see `choice-prompts.md`). One call = one story:
- `header`: the story ID (e.g. `US-003`; max 12 characters).
- `question`: one plain sentence, e.g. *"US-003 v2: approve it, suggest changes, or mark it not approved? Type suggestions in the “Other” box."*
- `options` (2–4, single-select, each with a `description`): `Approve v2` (*"Saves this exact version to stories/approved/"*), `Not approved` (*"Saves it to stories/unapproved/, never to approved/"*), `Defer story` (*"Parks it in stories/deferred/ to revisit later"*), and optionally `Skip for now`. The user's typed text in “Other” is their free-text response — never force them into an option. Never pre-select.

If `AskUserQuestion` is not available or fails in this surface, fall back to plain chat: send just that one story, ask for *approve / suggestions / not approved* in the reply, and wait. Do not show the next story until this one is settled.

Loop:
1. `story next` → show the story as above. Put the full template (title, status, version, requirement/decision IDs, source references, open gaps, user story, acceptance criteria, notes) in the chat message, trimmed so it reads at a glance.
2. Read the response and act on **what the user actually said**:
   - **Approval** (picked *Approve*, or typed "approve", "approved", "looks good, approve" …) → ask for approver name/role once (optional; never invent) and use their own words: `story approve US-001 --version N --statement "<their words>" [--approver-name … --approver-role …]`. The exact version must be named. Result goes to `stories/approved/`. A comment such as "approve but change X" is **not** an approval: treat it as suggestions.
   - **Suggestions** (any typed text that asks for a change) → apply them as JSON of changed fields: `story edit US-001 --file changes.json --reason "<their words>"`. This creates version N+1 (validated again). Show N+1 in a new prompt for the same story and ask again; the earlier approval never carries over. If the story was already approved, the approved version stays in force and in history until N+1 is approved. If the suggestion is unclear, ask one follow-up question instead of guessing.
   - **Not approved** (picked *Not approved*, or typed "unapproved", "not approved", "reject" …) → record it: `story request-changes US-001 --version N --note "<their words>"`. The core saves it to `stories/unapproved/` (the result returns `location`) — tell the user that exact path; it stays out of `approved/`. If they gave no reason, ask once, optionally, what should change; if they give suggestions, follow the *Suggestions* path.
   - **Defer** → `story defer US-001 --version N --reason "…"` (moves to `stories/deferred/`).
   - **Skip for now** → leave it as is and continue with the next story; it will come back.
3. **Revisit deferred**: `story next --deferred` (or `story revisit US-001`), then approve/edit/keep deferred, individually. **Revisit unapproved**: `story next --unapproved`; ask what should change, then `story edit` (new version, back to review) or leave it in `stories/unapproved/`. An unapproved story cannot be approved as is.
4. Stories flagged for impact review (BRD, decision or baseline changed): show why; either `story edit` (new version) or, if the requirements are truly unchanged, `story confirm-unaffected US-001 --statement "<reviewer's words>"`.
5. Do not place candidate or deferred stories in `approved/`, and never approve because the user is silent, time passed, or you judge it good.

When the queue is empty run `complete`. If it reports PARTIAL, relay the outstanding items (blocked requirements, deferred stories, uncovered requirements) instead of saying the work is done. Offer the summary at `reports/workflow-summary.md` and how to resume (`/ba-agent:review`).
