---
name: story-review
description: Present candidate user stories one at a time for human approval, edit, defer or revisit; only explicitly approved versions reach the approved folder. Use when stories await review.
---

# Story review

The CLI builds every story view; **relay it, do not compose it**. Approval rules: `../ba-workflow/references/human-approval.md` (Approvals section only). Triage, the baseline refresh and updating flagged stories: `references/triage.md` — open it only for `/ba-agent:triage-stories` or when a story is flagged for impact review.

## Present ONE story
`story next` (or `story next --id US-00x`) returns `stories[0]` with `present`:
1. Send `present.card` as one chat message — the story, its acceptance criteria, traceability, open gaps, and any blocker or decision to apply. A story must be read to be approved, so this message is always sent.
2. Call `AskUserQuestion` (ToolSearch `select:AskUserQuestion` if needed) with `present.dialog` **as it is**. The options already respect eligibility: `Approve vN` is absent while the story cannot be approved. Typed text in “Other” is edit suggestions or an instruction.
3. No dialog tool: send `present.fallback` after the card and wait. Do not show the next story until this one is settled.
Never restate the card inside the dialog, never pre-select, never mark “(Recommended)”. `story next --full` returns the raw story (needed only to build a `story edit` file; `story show US-00x` is the same on demand).

## Act on what the user actually said
- **Approve vN** (or “approve”, “looks good, approve”): `story approve US-00x --version N --statement "<their words>" [--approver-name … --approver-role …]` — name the exact version; ask for approver name/role once (optional, never invented). “Approve but change X” is an edit, not an approval. Relay `saved_to`.
- **Suggestions** (typed text asking for a change): write the changed fields as JSON in `state/inbox/`, then `story edit US-00x --file … --reason "<their words>"`. That creates version N+1 (validated again, never approved by the edit). Present N+1 as a new prompt for the same story; an earlier approval never carries over and stays in force until N+1 is approved. Unclear → one follow-up question.
- **Not approved**: `story request-changes US-00x --version N --note "<their words>"` → `stories/unapproved/`. Ask once, optionally, what should change; suggestions follow the path above.
- **Defer story**: `story defer US-00x --version N --reason "<their words>"` → `stories/deferred/`.
- **Keep as is / Keep deferred / Skip**: `story keep US-00x` audits the decision (triage); skip changes nothing. The story comes back later.
- **Update story / No change needed** (impact-flagged stories): `references/triage.md`.
Anything else, or a dismissed dialog: no decision. Never approve because the user is silent, time passed, or you judge it good.

## Revisit
`story next --deferred` / `story next --unapproved` (or `story revisit US-00x`), then approve / edit / keep, individually. An unapproved story cannot be approved as is; a deferred or unapproved story is brought back with `story revisit` before `story approve`.

## Special cases
- **Impact review** (BRD, decision, baseline or gap changed): show why; `story edit`, or `story confirm-unaffected US-00x --statement "<reviewer's words>"` if the requirements are truly unchanged. `confirm-unaffected` is refused when the cause is a resolved or new gap — the story must be updated with `story edit` (`user-story-generation`).
- **Approving with open gaps** is allowed when the human understands them (the approval records gap IDs and readiness). A `NOT_READY` story is approved as a document, not as ready to build, and keeps the workflow PARTIAL.
- **Approved story edited by hand:** `changes check` reports it; once applied, the approved version is restored and the edit comes back here as the next version (`MANUAL_EDIT_DETECTED`). They may discard it (`story revert-edit`). A deleted approved story is restored (`story restore`) or its removal confirmed.

## When the queue is empty
Run `complete`. If it reports PARTIAL, relay the outstanding items (blocked requirements, deferred stories, uncovered requirements, not-ready stories, carried-forward gaps and the stories waiting on them) instead of saying the work is done, and offer `gaps next --carried`. The summary is `reports/workflow-summary.md`; resume with `/ba-agent:review`.
