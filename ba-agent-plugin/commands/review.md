---
description: Continue pending gap resolution or story review, and revisit deferred gaps or stories
argument-hint: "[BA project folder]"
---

Continue the human review work of an existing BA project. Input: `$ARGUMENTS`

This is the general “carry on where I left off” entry point. It routes by `status`; the focused queues have their own commands: `/ba-agent:triage-gaps` (every OPEN and DEFERRED gap) and `/ba-agent:triage-stories` (every unapproved story). Use those when you want the whole backlog listed and reviewed; use this when you just want to continue, or when the next step is something else (stale baseline, impact review, interrupted closure).

1. Load the `ba-agent:ba-workflow` skill, then locate the project folder exactly as `/ba-agent:status` does and run `status`, then `changes check`. If a change was detected, follow `ba-agent:change-detection` (or `/ba-agent:changes`) before anything else: show the change and its impact, and only apply it on the user's explicit decision.
2. Follow `NEXT:` from the status output:
   - Gaps awaiting answers (highest priority first), answered-later gaps, reopened gaps, carried-forward gaps, or deferred gaps (due or not) → follow `/ba-agent:triage-gaps` (the `ba-agent:gap-resolution` skill; ask for the decision owner first if none is recorded).
   - Stories awaiting review, not approved, deferred, or flagged because a carried-forward gap was resolved → follow `/ba-agent:triage-stories` (the `ba-agent:story-review` skill; one story at a time, with its open gaps and readiness).
   - A detected or applied change (BRD edit, approved story edited or deleted), a stale baseline, impact review or interrupted closure → use the matching skill (`change-detection`, `requirements-baseline`, `quality-traceability`, `resume-workflow`).
3. Present exactly one gap or one story at a time, each as a prompt with a text box (see `gap-resolution` and `story-review`). For a story the user can type *approve*, their suggestions, or *not approved*. Never approve, resolve or defer anything the user has not explicitly decided.
4. When the queue is empty, run `complete`. Report honestly: if blocking work remains, say the workflow is partial and list what is outstanding.
