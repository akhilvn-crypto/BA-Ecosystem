---
description: Review every unapproved acceptance-criteria record one by one (approve, edit, defer)
argument-hint: "[BA project folder]"
---

Triage the unapproved acceptance criteria of a BA project. Input: `$ARGUMENTS`

Load `ba-agent:ba-workflow`, locate the project as `/ba-agent:status` does and run `status`; then follow `references/stages/ac-review/triage.md` (and `stages/ac-review.md` for each record). Only those files.

- No gap analysis yet or no criteria → say so and point to `/ba-agent:start`. A pending `recover` or impact review comes first (`stages/resume-workflow.md`).
- `ac triage` runs the change check first; unreviewed changes are decided with the user before any record. Post its `summary` (no table). Empty queue → say so, run `complete`, stop.
- Clear `preflight` once before any record (an unreviewed change in the requirements: `/ba-agent:changes`), then run `ac triage` again.
- One record at a time (`ac next --key <key>`): send `present.card`, then `AskUserQuestion` with `present.dialog` unchanged.
- At the end report in 3–4 lines: approved (and which enriched copy now shows them), edited and awaiting approval, deferred, not approved, anything blocked (open gaps → `/ba-agent:triage-gaps`). Empty queue → `complete`; say honestly if only PARTIAL.
