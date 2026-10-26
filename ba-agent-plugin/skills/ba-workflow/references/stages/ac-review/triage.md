# Acceptance-criteria triage (`/ba-agent:triage-ac`)

Triage reviews every record that is not yet approved — pending review (`acceptance/candidates/`), **not approved** (`acceptance/unapproved/`) and **deferred** (`acceptance/deferred/`) — with the same card, dialog, approval evidence and versioning as `../ac-review.md`.

## 1. List — one line, no table
`ac triage` (read-only; it runs the change check first — handle `change_check` per `../change-detection.md` before anything else). Post its `summary` string only. The rows are the review order and the data behind it; print them only if the user asks.

## 2. Preflight — clear shared blockers once, before any record
`preflight` lists problems that stop **every** record from being approved: a change in the requirement files that nobody has reviewed yet (`source_change` → `/ba-agent:changes` first). Never walk the user through records that cannot be approved when one step fixes all of them. Then run `ac triage` again.

## 3. One record at a time
For each record in order: `ac next --key <key>`, then `../ac-review.md` §Present (the card is always sent; the dialog's `position` is “n of N”). Options by situation are built by the core:
- pending review: `Approve vN`, `Defer`, `Not approved` · not approved earlier: `Approve vN`, `Defer`, `Keep as is` · deferred: `Approve vN`, `Not approved`, `Keep deferred`.
- cannot be approved yet: no `Approve`. A record flagged for impact review or whose requirement text changed gets `Update criteria` (and `No change needed` when the cause is not a resolved gap); a record blocked by an open gap gets `Keep as is` / `Defer` — resolve the gap first with `/ba-agent:triage-gaps`.

## 4. Act on the choice
- **Approve** (explicit words, exact version): a record in `unapproved/` or `deferred/` first goes `ac revisit <key>` (same version, review history kept), then `ac approve <key> --version N --statement "<their words>"`.
- **Update criteria:** the card shows **Decision to apply** (the validated interpretation from the closed gap) and the requirement text. Build the changed fields — remove the resolved gap from `open_gap_refs`, apply the decision to the criteria and assumptions, keep any other open gaps declared — write them to `state/inbox/`, then `ac edit <key> --file … --reason "Applied DEC-nnn"`. The result is version N+1 (`CANDIDATE`, recomputed readiness, never approved by the edit). Present N+1 straight away: it now has `Approve`. For many records `ba-agent:ac-writer` can draft the edits (give it only the keys; it writes a changed-fields file per requirement under `state/inbox/`); you still run `ac edit`.
- **No change needed:** ask for the reviewer's words, then `ac confirm-unaffected <key> --statement "<their words>"`. Refused when a resolved gap is still named.
- **Edit suggestions** (typed): as in `../ac-review.md`.
- **Defer / Not approved / Keep:** as in `../ac-review.md` (`ac defer`, `ac request-changes`, `ac keep`).
- **Skip / stop:** change nothing; it is listed again next time.

## 5. Finish
State lives in the acceptance folders and the audit log, so an interrupted triage resumes with `ac triage`. Run `status`; report in 3–4 lines: approved, edited and awaiting approval, deferred, not approved, and anything blocked (open gaps → `/ba-agent:triage-gaps`, impact review). Name the enriched folder once so they can open the result. When the queue is empty run `complete` and report honestly if the workflow is only PARTIAL.
