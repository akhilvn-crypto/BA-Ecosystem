---
description: List every OPEN and DEFERRED gap, then review them one by one (answer, explain, defer, carry forward or keep as is)
argument-hint: "[BA project folder]"
---

Triage the unresolved gaps of a BA project. Input: `$ARGUMENTS`

This command adds no rules of its own: it lists the queue and then runs the `ba-agent:gap-resolution` skill ("Triage mode") for each gap. Every decision goes through the same core actions, so validation, IST audit history, baseline staleness and resume behave exactly as in `/ba-agent:start`.

1. Load the `ba-agent:ba-workflow` skill, locate the project folder exactly as `/ba-agent:status` does (use the input if given; otherwise look inside the current working directory only) and run `status`. If there is no project, or no gap analysis yet, say so and point to `/ba-agent:start`. Finish a pending `recover` or impact review first (`ba-agent:resume-workflow`).
2. **Decision owner.** If `status` shows none, ask for the owner's name and role (see `gap-resolution` §0) before showing any gap.
3. Run `gaps triage` (it runs the change check first and returns a `change_check` block: if it lists unreviewed changes, show them and decide with the user per `ba-agent:change-detection` before presenting any gap - a changed BRD can change what a gap means). Show it in one chat message: a short table with **ID · priority · OPEN or DEFERRED · deferral reason (who, when, revisit date) · decision owner / waiting on · linked requirements · linked stories · blocks stories?**. Mark carried-forward gaps and deferred gaps whose revisit date is due. If the list is empty, say so, run `complete` / `status` and stop.
4. **One gap at a time**, in the order the list gives (priority first). For each: `gaps next --id GAP-00x`, then follow `gap-resolution` → *Triage mode*: a chat message with the gap, then a short `AskUserQuestion` with a text box (numbered-list fallback in Cowork). Say where they are ("Gap 2 of 5"). The user may answer, ask for an explanation, defer, carry forward (MEDIUM/LOW only), keep the gap as it is, skip, or stop.
5. Never answer, resolve, defer, carry forward or reopen anything the user has not explicitly decided. A typed answer is stored and checked against the BRD, requirements, earlier decisions and earlier answers before the gap can be resolved; a conflict is shown to the user, never decided by you.
6. At the end (or when the user stops) run `status` and report in 3–4 lines: resolved, deferred, carried forward, still open, which of them still block stories. If gaps were resolved and stories exist, say the affected stories were flagged and offer `/ba-agent:triage-stories`; if the baseline is stale, offer to refresh it.
