---
name: story-review
description: Present candidate user stories one at a time for human approval, edit, defer or revisit; only explicitly approved versions are stored in the approved folder. Use when stories are awaiting review, when deferred stories are revisited, or when an approved story needs changes.
---

# Story review

Read `../ba-workflow/references/human-approval.md` (Approvals) and `../ba-workflow/references/choice-prompts.md` (how to show the dialog).

## Present ONE story at a time, with a response box
`story next` (default `--batch 1`) returns the single story to review. Never list several stories in one message: the user concentrates on one story, answers, then sees the next. Say where they are ("Story 2 of 8") so progress is visible. Only if the user explicitly asks for a batch, use `story next --batch N`.

Two steps, in the same turn:
1. **Chat message** (normal markdown) with the full story: `US-003 · title · vN (Story 2 of 8)`; the story (As a … I want … so that …); the acceptance criteria (Given/When/Then, short); requirement/decision IDs and source reference; the story's **readiness** (`READY_FOR_DEVELOPMENT`, `NEEDS_CLARIFICATION` or `NOT_READY`) and, if it has `unresolved_decisions`, each open gap with its priority, the unresolved question, the potential impact and any working assumption — stated plainly as *open, not an approved decision*; any `live_open_gap_ids` that differ from the declared gaps, or an impact flag, prominently.
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
4. Stories flagged for impact review (BRD, decision, baseline or gap changed): show why; either `story edit` (new version) or, if the requirements are truly unchanged, `story confirm-unaffected US-001 --statement "<reviewer's words>"`. When the cause is a **carried-forward gap that has now been resolved** (or a new gap opened), `confirm-unaffected` is refused — the story must be updated with `story edit` (see `user-story-generation`), and the new version is reviewed here again, with its new readiness. Approving a story with open gaps is allowed when the human understands them (the approval records the open gap IDs and readiness); a `NOT_READY` story is approved as a document, not as ready to build, and keeps the workflow PARTIAL.
5. **Approved story edited by hand** (the BRD-independent case): `changes check` reports it with the changed fields; once the user applies it, the approved version is restored untouched and the edit comes back here as the next version (`MANUAL_EDIT_DETECTED` in its history) — present it like any edit and ask again; they can also discard it (`story revert-edit`). A deleted approved story is restored (`story restore`) or its removal confirmed. Never approve an edited version on the user's behalf.
6. Do not place candidate or deferred stories in `approved/`, and never approve because the user is silent, time passed, or you judge it good.

## Triage mode (`/ba-agent:triage-stories`)
Triage reviews every story that is not yet approved — pending review (`stories/candidates/`), **not approved** (`stories/unapproved/`) and **deferred** (`stories/deferred/`) — with the same presentation, dialog, approval evidence and `story edit` versioning described above. Only the entry point and the one-by-one order differ.
1. `story triage` (read-only) → one chat table: ID, version, title, status, readiness, open gaps, deferral reason or the user's earlier note, and `eligible` / `blockers`. A story is not eligible while it is flagged for impact review, has a linked requirement that changed, depends on a gap that blocks (open CRITICAL/HIGH/material gap), or the baseline is stale — `approve` would be refused for the same reason. Ineligible stories are still shown (deferred ones must stay available), with the blocker in plain words.
2. For each story, one at a time: `story next --id US-00x` (works from every folder; presenting does not move it), the full story in chat as above, then a short `AskUserQuestion` with a text box. Options by situation (2–4, each with a description):
   - Pending review: `Approve vN`, `Defer story`, `Not approved`.
   - Not approved earlier: `Approve vN` (*“Reconsider and approve this exact version”*), `Defer story`, `Keep as is`.
   - Deferred: `Approve vN`, `Not approved`, `Keep deferred`.
   - Not eligible: no `Approve`; offer `Keep as is` / `Keep deferred` and `Defer story` (when not deferred), and say what must happen first (resolve the gap with `/ba-agent:triage-gaps`, refresh the baseline, update the story).
   Typed text in “Other” is **edit suggestions** (or an instruction); never force it into an option.
3. Act on what the user chose:
   - **Approve** (explicit words, exact version): a story in `unapproved/` or `deferred/` is first brought back with `story revisit US-00x` (same version, `STORY_REVISITED` audit, review history kept), then `story next --id` and `story approve US-00x --version N --statement "<their words>"` as above. “Approve but change X” is an edit, not an approval.
   - **Edit**: `story edit US-00x --file changes.json --reason "<their words>"` → a **new version** (`CANDIDATE`, earlier version archived, never approved by the edit). Show vN+1 in a new prompt for the same story and ask again; an already approved version stays in force until the new one is approved.
   - **Defer**: `story defer US-00x --version N --reason "<their words>"` (from pending review or not approved; the story moves to `stories/deferred/` and is listed again next time). A story already deferred is `Keep deferred`.
   - **Not approved**: `story request-changes` for a pending story (a deferred one is revisited first); it is saved to `stories/unapproved/`, never to `approved/`.
   - **Keep as is**: `story keep US-00x` — nothing moves; only the decision is audited (`STORY_TRIAGE_KEPT`).
   - **Skip / stop**: change nothing; it is listed again next time.
4. State lives in the story folders and the audit log, so an interrupted triage resumes with `story triage`: approved stories are gone from the list, the rest keep their status, version and history. Finish with `status`; when the queue is empty run `complete` and relay PARTIAL items honestly. If blockers came from gaps, offer `/ba-agent:triage-gaps`.

When the queue is empty run `complete`. If it reports PARTIAL, relay the outstanding items (blocked requirements, deferred stories, uncovered requirements, stories not ready for development) instead of saying the work is done. Always mention the carried-forward gaps that are still open and the stories waiting on them, and offer to revisit them (`gaps next --carried`). Offer the summary at `reports/workflow-summary.md` and how to resume (`/ba-agent:review`).
