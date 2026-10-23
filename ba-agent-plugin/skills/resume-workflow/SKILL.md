---
name: resume-workflow
description: Resume an interrupted or later-session BA workflow from saved artifacts - find the project, read status, repair interrupted writes, and continue from the right stage. Use when the user returns to an existing BA project folder or a previous session ended mid-way.
---

# Resume

State lives in files, not in the conversation. To resume:

1. Locate the project folder (user-provided, or `state/workflow.json` under the current directory). Do not search elsewhere. State is shared across sessions/surfaces **only if the same folder is accessible** — don't assume cross-device sync.
2. `status` → read stage, decision owner, counts, validation, `NEXT:`. Summarise in ≤ 4 lines.
3. If `NEXT` or `validate` mentions an interrupted closure/approval, run `recover` (idempotent), then `validate`. Report what was repaired.
4. `source check` — if the BRD changed while you were away, follow the impact-assessment steps in `quality-traceability` before anything else.
5. Re-orient the human without repeating work: presented gaps come back first (`gaps next` returns them with their saved answers); incomplete answers are retained; deferred items stay deferred until the user chooses to revisit; no decision is ever recorded twice.
6. If the decision owner is missing/unknown, ask for it before presenting any gap.
7. If the stage is `PAUSED`/`BLOCKED`, explain what is needed to resume (it only resumes to the saved stage).
8. Continue with the skill for the current stage (see `ba-workflow`).

If a write failed earlier (`WRITE_FAILED`), confirm the destination is writable now, run `recover`, and tell the user what was and wasn't saved.
