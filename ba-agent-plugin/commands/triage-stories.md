---
description: Review every unapproved user story one by one (approve, edit, defer), refreshing a stale baseline first
argument-hint: "[BA project folder]"
---

Triage the unapproved user stories of a BA project. Input: `$ARGUMENTS`

No rules of its own: it lists the queue, clears whatever blocks every story, then runs `ba-agent:story-review` → `story-review/references/triage.md` for each story, so versioning, approval evidence, traceability, audit history and resume behave exactly as in `/ba-agent:start`. Load only that skill and that reference.

1. Load `ba-agent:ba-workflow`, find the project exactly as `/ba-agent:status` does and run `status`. No baseline yet or no stories → say so and point to `/ba-agent:start`. Finish a pending `recover` or impact review first (`ba-agent:resume-workflow`).
2. `story triage` runs the change check first. If `change_check.unreviewed` is non-empty (BRD edits, edited or deleted approved stories), decide it with the user per `ba-agent:change-detection` before presenting any story. Post the result's one-line `summary` (no table). Empty queue → say so, run `complete` / `status`, stop.
3. **Clear `preflight` first, once.** After `/ba-agent:triage-gaps` the baseline is stale and no story can be approved until it is refreshed. Refresh and confirm it in one dialog (`story-review/references/triage.md` §2), then run `story triage` again. Never walk the user through stories that cannot be approved when one step unblocks them all.
4. **One story at a time** in the order given: `story next --id US-00x`, then send `present.card` and call `AskUserQuestion` with `present.dialog` unchanged (card + `present.fallback` where the tool is unavailable). The options already follow eligibility: `Approve vN` appears only when the story can be approved, and a story flagged by a resolved gap offers `Update story`. Typed text in “Other” is edit suggestions.
5. Never approve, edit or defer a story the user has not explicitly decided. Approval records their own words for the exact version; an edit is a new version that comes straight back for approval — an earlier approval never carries over.
6. At the end run `status` and report in 3–4 lines: approved, edited and awaiting approval, deferred, not approved, and anything blocked (open gaps → `/ba-agent:triage-gaps`, impact review, stale baseline). Empty queue → `complete`, and say honestly if the workflow is only PARTIAL.
