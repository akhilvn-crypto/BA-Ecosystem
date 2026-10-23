---
description: Check the BRD and the approved stories for changes, explain the downstream impact, and walk the review until everything affected is reconciled
argument-hint: "[BA project folder]"
---

Detect and review changes in a BA project. Input: `$ARGUMENTS`

This command adds no rules of its own: it runs the `ba-agent:change-detection` skill. Detection is read-only for requirements, gaps, stories and the baseline; only an explicit decision by a named person makes the core mark anything stale.

1. Load the `ba-agent:ba-workflow` and `ba-agent:change-detection` skills. Locate the project folder exactly as `/ba-agent:status` does (use the input if given; otherwise look inside the current working directory only).
2. Run `changes check`. If it reports nothing, say so in one line (and mention any formatting-only differences the core accepted), run `changes status` and stop.
3. For each change: show what changed, the impact and the required actions in chat (nothing downstream has changed yet), then ask with a short `AskUserQuestion` (`Apply impact` / `Review later`; numbered-list fallback in Cowork). Only on an explicit yes, ask for their name and role and run `changes apply --id CHG-nnn --by-name … --by-role … --statement "<their words>"`.
4. Then follow `NEXT:` from `status` and the open items from `changes status`, one decision at a time, with the matching skills (`atomic-requirements` for requirements, `gap-resolution`, `requirements-baseline`, `story-review`). Offer `/ba-agent:triage-gaps` and `/ba-agent:triage-stories` when gaps or stories are waiting.
5. Finish with `changes status` and report in 3–4 lines: changes applied, items still open, anything deferred ("Review later"), and whether `complete` can run.
