# Story triage (`/ba-agent:triage-stories`)

Triage reviews every story that is not yet approved — pending review (`stories/candidates/`), **not approved** (`stories/unapproved/`) and **deferred** (`stories/deferred/`) — with the same card, dialog, approval evidence and versioning as `SKILL.md`.

## 1. List — one line, no table
`story triage` (read-only; it runs the change check first — handle `change_check` per `change-detection` before anything else). Post its `summary` string only. The rows are the review order and the data behind it; print them only if the user asks.

## 2. Preflight — clear shared blockers once, before any story
`preflight` lists problems that stop **every** story from being approved. The usual one after `/ba-agent:triage-gaps` is `baseline_stale`: resolving a gap marks the baseline stale, and without a fresh baseline no story can be approved. Never walk the user through stories that cannot be approved when one step fixes all of them.
- `baseline_stale` / `no_baseline` → `baseline create`. Post a 2-line summary from its result (`ready_requirements`, `blocked_requirements`, `changes`); the readable file is `files_saved.baselines.md_folder`. Then `AskUserQuestion` (`header: "BASELINE"`, no “(Recommended)”): `Confirm vN` — *“Approve this baseline; it is the version stories are checked against”*, `Not now` — *“Review stories without approving any”*. Name and role of the approver come from the user (the decision owner if they say so — still both); never invented. Confirm → `baseline confirm --version N --by-name … --by-role … --statement "<their words>"`. Its result lists `stories_flagged_for_impact_review`; then run `story triage` again.
- `baseline_unconfirmed` → same dialog, no `baseline create`.
- `brd_impact_review` → `/ba-agent:changes` first.
- `Not now` → continue; the dialogs withhold `Approve` and say why.

## 3. One story at a time
For each story in order: `story next --id US-00x`, then `SKILL.md` §Present (the card is always sent; the dialog's `position` is “n of N”). Options by situation are built by the core:
- pending review: `Approve vN`, `Defer story`, `Not approved` · not approved earlier: `Approve vN`, `Defer story`, `Keep as is` · deferred: `Approve vN`, `Not approved`, `Keep deferred`.
- cannot be approved yet: no `Approve`. A story flagged for impact review or with a changed requirement gets `Update story` (and `No change needed` when the cause is not a resolved gap); a story blocked by an open gap gets `Keep as is` / `Defer story` — resolve the gap first with `/ba-agent:triage-gaps`.

## 4. Act on the choice
- **Approve** (explicit words, exact version): a story in `unapproved/` or `deferred/` first goes `story revisit US-00x` (same version, review history kept), then `story approve US-00x --version N --statement "<their words>"`.
- **Update story:** the card shows **Decision to apply** (the validated interpretation from the closed gap). Build the changed fields — remove the resolved gap from `unresolved_decisions`, apply the decision to the acceptance criteria and assumptions, keep any other open gaps declared — write them to `state/inbox/`, then `story edit US-00x --file … --reason "Applied DEC-nnn"`. The result is version N+1 (`CANDIDATE`, recomputed readiness, never approved by the edit). Present N+1 straight away: it now has `Approve`. For many stories `ba-agent:story-builder` can draft the edits (give it only the story IDs; it writes a changed-fields file per story under `state/inbox/`); you still run `story edit`.
- **No change needed:** ask for the reviewer's words, then `story confirm-unaffected US-00x --statement "<their words>"`. Refused when a resolved gap is still named.
- **Edit suggestions** (typed): as in `SKILL.md`.
- **Defer / Not approved / Keep:** as in `SKILL.md` (`story defer`, `story request-changes`, `story keep`).
- **Skip / stop:** change nothing; it is listed again next time.

## 5. Finish
State lives in the story folders and the audit log, so an interrupted triage resumes with `story triage`. Run `status`; report in 3–4 lines: approved, edited and awaiting approval, deferred, not approved, and anything blocked (open gaps → `/ba-agent:triage-gaps`, impact review, stale baseline). When the queue is empty run `complete` and report honestly if the workflow is only PARTIAL.
