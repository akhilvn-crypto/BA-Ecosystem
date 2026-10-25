---
description: Review every OPEN and DEFERRED gap one by one (answer, explain, defer, carry forward or keep)
argument-hint: "[BA project folder]"
---

Triage the unresolved gaps of a BA project. Input: `$ARGUMENTS`

Load `ba-agent:ba-workflow`, locate the project as `/ba-agent:status` does and run `status`; then follow `references/stages/gap-resolution/triage.md` (and `stages/gap-resolution.md` for each gap). Only those files.

- No project or no gap analysis yet → say so and point to `/ba-agent:start`. A pending `recover` or impact review comes first (`stages/resume-workflow.md`).
- No decision owner in `status` → ask name and role before any gap.
- `gaps triage` runs the change check first; unreviewed changes are decided with the user before any gap. Post its `summary` (no table). Empty queue → say so, run `complete`, stop.
- One gap at a time (`gaps next --id GAP-00x`), relaying `present`. The user may answer, ask for an explanation, defer, carry forward (MEDIUM/LOW only), keep, skip or stop.
- At the end report in 3–4 lines: resolved, deferred, carried forward, still open and still blocking. If stories exist, say the baseline is now stale and `/ba-agent:triage-stories` refreshes it first.
