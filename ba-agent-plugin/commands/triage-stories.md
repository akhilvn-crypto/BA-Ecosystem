---
description: Review every unapproved user story one by one (approve, edit, defer), refreshing a stale baseline first
argument-hint: "[BA project folder]"
---

Triage the unapproved user stories of a BA project. Input: `$ARGUMENTS`

Load `ba-agent:ba-workflow`, locate the project as `/ba-agent:status` does and run `status`; then follow `references/stages/story-review/triage.md` (and `stages/story-review.md` for each story). Only those files.

- No baseline yet or no stories → say so and point to `/ba-agent:start`. A pending `recover` or impact review comes first (`stages/resume-workflow.md`).
- `story triage` runs the change check first; unreviewed changes are decided with the user before any story. Post its `summary` (no table). Empty queue → say so, run `complete`, stop.
- Clear `preflight` once before any story (usually a stale baseline: one refresh-and-confirm dialog), then run `story triage` again.
- One story at a time (`story next --id US-00x`): send `present.card`, then `AskUserQuestion` with `present.dialog` unchanged.
- At the end report in 3–4 lines: approved, edited and awaiting approval, deferred, not approved, anything blocked (open gaps → `/ba-agent:triage-gaps`). Empty queue → `complete`; say honestly if only PARTIAL.
