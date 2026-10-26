# Resume

State lives in files, not in the conversation. To resume:

1. Locate the project folder (user-provided, or `state/workflow.json` under the current directory). Do not search elsewhere. State is shared across sessions/surfaces **only if the same folder is accessible** — don't assume cross-device sync.
2. `status` → read stage, decision owner, counts, validation, `NEXT:`. Summarise in ≤ 4 lines.
3. If `NEXT` or `validate` mentions an interrupted closure/approval, run `recover` (idempotent), then `validate`. Report what was repaired.
4. `changes check` — a requirement file, approved criteria or an enriched copy may have been edited (or the upstream agent may have regenerated the requirements) while you were away. Follow `change-detection`: show what changed and its impact, apply only on the user's decision, then work the open items, before anything else. Detected and applied changes live in `changes/` and survive the session; `status` shows them (`Changes:`) and `NEXT:` says what is waiting.
5. Re-orient the human without repeating work: presented gaps come back first (`gaps next` returns them with their saved answers); incomplete answers are retained; answered-later gaps are still open and come back in priority order; carried-forward gaps stay OPEN and visible (`status` shows `CARRIED FORWARD (open)`; `gaps next` returns `carried_forward_open`) and are not re-asked until the user wants to revisit them (`gaps next --carried`) — offer that once and remind the user which criteria are flagged because of them; deferred gaps (with their reason, actor and revisit date) stay deferred until the user chooses to revisit — mention any whose revisit date has arrived (`deferred_due_for_revisit`); if `validate`/`status` reports a missing audit event, run `recover` first; no decision is ever recorded twice.
6. If the decision owner is missing/unknown, ask for it before presenting any gap.
7. If the stage is `PAUSED`/`BLOCKED`, explain what is needed to resume (it only resumes to the saved stage).
8. Continue with the skill for the current stage (see `ba-workflow`).

If a write failed earlier (`WRITE_FAILED`), confirm the destination is writable now, run `recover`, and tell the user what was and wasn't saved.
