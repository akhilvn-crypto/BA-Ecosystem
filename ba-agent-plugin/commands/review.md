---
description: Continue pending gap resolution or story review, and revisit deferred gaps or stories
argument-hint: "[BA project folder]"
---

Continue the human review work of an existing BA project. Input: `$ARGUMENTS`

The general “carry on where I left off” entry point. Focused queues have their own commands: `/ba-agent:triage-gaps` (every OPEN and DEFERRED gap) and `/ba-agent:triage-stories` (every unapproved story).

1. Load `ba-agent:ba-workflow`, locate the project as `/ba-agent:status` does, run `status`, then `changes check`. A change was detected → `references/stages/change-detection.md` (or `/ba-agent:changes`) before anything else.
2. Follow `NEXT:` through the router table in `ba-workflow`: gaps waiting → `/ba-agent:triage-gaps`; stories waiting → `/ba-agent:triage-stories`; anything else (stale baseline, impact review, interrupted closure) → the stage file the router names.
3. When the queue is empty run `complete` and report honestly: if blocking work remains, the workflow is partial; list what is outstanding.
