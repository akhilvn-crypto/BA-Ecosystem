---
description: List every unapproved user story (pending review, not approved, deferred) and review them one by one (approve, edit or defer)
argument-hint: "[BA project folder]"
---

Triage the unapproved user stories of a BA project. Input: `$ARGUMENTS`

This command adds no rules of its own: it lists the queue and then runs the `ba-agent:story-review` skill ("Triage mode") for each story. Every decision goes through the same core actions, so versioning, approval evidence, traceability, audit history and resume behave exactly as in `/ba-agent:start`.

1. Load the `ba-agent:ba-workflow` skill, locate the project folder exactly as `/ba-agent:status` does (use the input if given; otherwise look inside the current working directory only) and run `status`. If there is no approved baseline or no stories yet, say so and point to `/ba-agent:start`. Finish a pending `recover` or impact review first (`ba-agent:resume-workflow`).
2. Run `story triage` (it runs the change check first and returns a `change_check` block: if it lists unreviewed changes - BRD edits or edited/deleted approved stories - show them and decide with the user per `ba-agent:change-detection` before presenting any story). Show it in one chat message: a short table with **ID · version · title · status (pending review / not approved / deferred) · readiness · open gaps · deferral reason or your earlier note · can it be approved now? (blockers if not)**. If the list is empty, say so, run `complete` / `status` and stop.
3. **One story at a time**, in the order the list gives. For each: `story next --id US-00x`, then follow `story-review` → *Triage mode*: the full story in chat, then a short `AskUserQuestion` with a text box (plain-chat fallback in Cowork). Say where they are ("Story 2 of 6"). The user can **approve**, **edit** (type suggestions), **defer**, mark **not approved**, keep it as it is, skip, or stop.
4. Never approve, edit or defer a story the user has not explicitly decided. Approval records their own words for the exact version. An edit is a new version that comes straight back for approval — the earlier approval never carries over. A deferred story stays in `stories/deferred/` and is listed again next time.
5. At the end (or when the user stops) run `status` and report in 3–4 lines: approved, edited and awaiting approval, deferred, not approved, and anything blocked (open gaps, impact review, stale baseline). When the queue is empty run `complete` and report honestly if the workflow is only partial. If gaps are blocking stories, offer `/ba-agent:triage-gaps`.
