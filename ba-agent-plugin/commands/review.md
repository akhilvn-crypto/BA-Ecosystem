---
description: Continue pending gap resolution or story review, and revisit deferred gaps or stories
argument-hint: "[BA project folder]"
---

Continue the human review work of an existing BA project. Input: `$ARGUMENTS`

1. Load the `ba-agent:ba-workflow` skill, then locate the project folder exactly as `/ba-agent:status` does and run `status`.
2. Follow `NEXT:` from the status output:
   - Gaps awaiting answers, reopened gaps, or deferred gaps the user wants to revisit → use the `ba-agent:gap-resolution` skill (ask for the decision owner first if none is recorded).
   - Stories awaiting review, or deferred stories → use the `ba-agent:story-review` skill (one story at a time).
   - A stale baseline, impact review or interrupted closure → use the matching skill (`requirements-baseline`, `quality-traceability`, `resume-workflow`).
3. Present exactly one gap or one story at a time, each as a prompt with a text box (see `gap-resolution` and `story-review`). For a story the user can type *approve*, their suggestions, or *not approved*. Never approve, resolve or defer anything the user has not explicitly decided.
4. When the queue is empty, run `complete`. Report honestly: if blocking work remains, say the workflow is partial and list what is outstanding.
