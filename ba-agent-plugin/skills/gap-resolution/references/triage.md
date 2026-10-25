# Triage mode (`/ba-agent:triage-gaps`)

Triage reviews every unresolved gap — `OPEN` (presented, answered later, reopened, carried forward) and `DEFERRED` — instead of only what `gaps next` would pick. Dialogs, validation and actions are unchanged; only the entry point differs.

1. `gaps triage` (read-only; `--status OPEN|DEFERRED` narrows it). Post its `summary` string — one line, **no table** — and go straight to the first gap. The rows are the review order; print them only if the user asks for the list. The decision owner is still required first.
2. For each gap, one at a time: `gaps next --id GAP-00x`, then relay `present` exactly as in `SKILL.md` §1. An `OPEN` gap becomes `WAITING_FOR_ANSWER`; a deferred or carried-forward gap is shown as it is (looking changes nothing; `revisit_needed_to_answer: true`).
3. Act on the choice:
   - **Answer** (typed or suggested): for a deferred/carried-forward gap run `gap revisit GAP-00x` first, then `gap answer` and validate (`validate-and-resolve.md`). Only a complete, conflict-free, reviewed answer is closed with `gap resolve`.
   - **Explain / Carry Forward / Defer / Answer Later / Keep**: `actions.md`. A deferred gap's `Keep Deferred` and a carried gap's `Keep Carried Forward` call `gap keep`.
   - **Skip / stop**: change nothing; it is listed again next time.
4. State is saved as it happens, so an interrupted triage resumes by running `gaps triage` again — resolved gaps drop out, the rest keep their state.
5. Finish with `status` and report in 3–4 lines: resolved, deferred, carried forward, still open, and which still block stories. If gaps were resolved while stories exist, the affected stories are flagged and the baseline is stale: say that `/ba-agent:triage-stories` refreshes the baseline and updates them as its first step.
