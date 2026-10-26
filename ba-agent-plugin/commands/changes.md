---
description: Check the requirement files, approved criteria and enriched copies for changes, explain the downstream impact, and walk the review until everything affected is reconciled
argument-hint: "[BA project folder]"
---

Detect and review changes in a BA project. Input: `$ARGUMENTS`

No rules of its own: load `ba-agent:ba-workflow`, locate the project as `/ba-agent:status` does (input, else the current directory only), and follow `references/stages/change-detection.md`.

1. `changes check`. Nothing reported → say so in one line (mention formatting-only differences the core accepted and any enriched copy it restored), run `changes status`, stop.
2. Each change: show what changed, the impact and the required actions in chat; ask with a short `AskUserQuestion` (`Apply impact` / `Review later`). Only on an explicit yes, ask their name and role and run `changes apply --id CHG-nnn --by-name … --by-role … --statement "<their words>"`.
3. Follow `NEXT:` and the open items in `changes status`, one decision at a time, with the stage file the router names. Offer `/ba-agent:triage-gaps` and `/ba-agent:triage-ac` when gaps or criteria are waiting.
4. Finish with `changes status` and report in 3–4 lines: applied, still open, deferred ("Review later"), and whether `complete` can run.
