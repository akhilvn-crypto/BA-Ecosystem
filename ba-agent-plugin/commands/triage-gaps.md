---
description: Review every OPEN and DEFERRED gap one by one (answer, explain, defer, carry forward or keep)
argument-hint: "[BA project folder]"
---

Triage the unresolved gaps of a BA project. Input: `$ARGUMENTS`

No rules of its own: it lists the queue, then runs `ba-agent:gap-resolution` → `gap-resolution/references/triage.md` for each gap, so validation, IST audit history, baseline staleness and resume behave exactly as in `/ba-agent:start`. Load only that skill and that reference.

1. Load `ba-agent:ba-workflow`, find the project exactly as `/ba-agent:status` does (the input, else the current directory only) and run `status`. No project or no gap analysis yet → say so and point to `/ba-agent:start`. Finish a pending `recover` or impact review first (`ba-agent:resume-workflow`).
2. No decision owner in `status` → ask for name and role (`gap-resolution` §0) before any gap.
3. `gaps triage` runs the change check first. If `change_check.unreviewed` is non-empty, decide it with the user per `ba-agent:change-detection` before presenting any gap. Then post the result's one-line `summary` (no table) and start. Empty queue → say so, run `complete` / `status`, stop.
4. **One gap at a time** in the order given: `gaps next --id GAP-00x`, then relay `present` (an inline dialog alone, or card + dialog). Say where they are only through the dialog's own “(n left)”. The user may answer, ask for an explanation, defer, carry forward (MEDIUM/LOW only), keep, skip or stop.
5. Never answer, resolve, defer, carry forward or reopen anything the user has not explicitly decided. A typed answer is stored and checked against the BRD, requirements, earlier decisions and answers before it can resolve a gap; a conflict goes to the user.
6. At the end run `status` and report in 3–4 lines: resolved, deferred, carried forward, still open, which still block stories. If gaps were resolved while stories exist, say the baseline is now stale and the affected stories are flagged, and that `/ba-agent:triage-stories` refreshes the baseline and updates them as its first step.
