# Acceptance-criteria review

The CLI builds every view; **relay it, do not compose it**. Approval rules: `../human-approval.md` (Approvals section only). Triage and updating flagged records: `ac-review/triage.md` — open it only for `/ba-agent:triage-ac` or when a record is flagged for impact review.

## Present ONE record
`ac next` (or `ac next --key <key>`) returns `acs[0]` with `present`:
1. Send `present.card` as one chat message — the requirement text, its criteria, open gaps, readiness, and any blocker or decision to apply. Criteria must be read to be approved, so this message is always sent.
2. Call `AskUserQuestion` directly with `present.dialog` **as it is**. The options already respect eligibility: `Approve vN` is absent while the record cannot be approved. Typed text in “Other” is edit suggestions or an instruction.
3. The dialog tool fails or is not in the tool list: re-run with `--fallback`, send `present.fallback` after the card and wait. Do not show the next record until this one is settled.
Dialog rules: `../choice-prompts.md`. `ac next --full` returns the raw record (needed only to build an `ac edit` file; `ac show <key>` is the same on demand).

## Act on what the user actually said
- **Approve vN** (or “approve”, “looks good, approve”): `ac approve <key> --version N --statement "<their words>" [--approver-name … --approver-role …]` — name the exact version; ask for approver name/role once (optional, never invented). “Approve but change X” is an edit, not an approval. The result names the enriched copy that now shows the criteria (`enriched_copy`): tell the user.
- **Suggestions** (typed text asking for a change): write the changed fields as JSON in `state/inbox/`, then `ac edit <key> --file … --reason "<their words>"`. That creates version N+1 (validated again, never approved by the edit). Present N+1 as a new prompt for the same requirement; an earlier approval never carries over and stays in force until N+1 is approved. Unclear → one follow-up question.
- **Not approved**: `ac request-changes <key> --version N --note "<their words>"` → `acceptance/unapproved/`. Ask once, optionally, what should change; suggestions follow the path above.
- **Defer**: `ac defer <key> --version N --reason "<their words>"` → `acceptance/deferred/`.
- **Keep as is / Keep deferred / Skip**: `ac keep <key>` audits the decision (triage); skip changes nothing. The record comes back later.
- **Update criteria / No change needed** (impact-flagged records): `ac-review/triage.md`.
Anything else, or a dismissed dialog: no decision.

## Revisit
`ac next --deferred` / `ac next --unapproved` (or `ac revisit <key>`), then approve / edit / keep, individually. An unapproved record cannot be approved as is; a deferred or unapproved record is brought back with `ac revisit` before `ac approve`.

## Special cases
- **Impact review** (requirement text, decision or gap changed): show why; `ac edit`, or `ac confirm-unaffected <key> --statement "<reviewer's words>"` if the criteria truly still hold (it re-pins them to the changed requirement text). `confirm-unaffected` is refused when the cause is a resolved or new gap: update with `ac edit` (`acceptance-criteria`). A requirement removed from the document: `ac archive <key> --reason … --by-name … --by-role …`.
- **Approving with open gaps** is allowed when the human understands them (the record carries the gap IDs and readiness, and the enriched copy lists them). A `NOT_READY` record is approved as a document, not as ready to build, and keeps the workflow PARTIAL.
- **Approved criteria edited by hand** (the record file, or the block in the enriched copy): `changes check` reports it; once applied, the approved version is restored and the edit comes back here as the next version (`MANUAL_EDIT_DETECTED`). They may discard it (`ac revert-edit`). Deleted approved criteria are restored (`ac restore`) or the removal confirmed.

## When the queue is empty
Run `complete`. If it reports PARTIAL, relay the outstanding items (blocked requirements, requirements without approved criteria, deferred records, not-ready records, carried-forward gaps and the criteria waiting on them) instead of saying the work is done, and offer `gaps next --carried`. The summary is `reports/workflow-summary.md`; resume with `/ba-agent:review`.
