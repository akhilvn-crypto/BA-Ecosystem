# Acceptance criteria

One record per requirement, written from the requirement text and the validated decisions, only for requirements that are not **BLOCKED** (`req list` shows each one's live status: `READY`, `READY_WITH_OPEN_GAPS` or `BLOCKED`). The core refuses blocked or unknown requirements, a requirement that already has criteria, and criteria that are not Given/When/Then. Gap analysis must be committed first.

Per requirement (JSON list → `state/inbox/ac.json` → `ac add --file …`):
```json
{"req_key": "lending.md::LIB-006",
 "acceptance_criteria": ["Given … when … then … (observable)", "negative/exception path if the requirement or a decision supports it"],
 "assumptions": [], "notes": {"business_rules": "…", "validation_exceptions": "…", "non_functional": "…"},
 "open_gap_refs": []}
```
`req_key` may be the bare ID when it is unique in the folder. `open_gap_refs` is **always present** (empty when no gap is open). For a requirement that carries an open gap it holds one entry per gap:
```json
{"gap_id": "GAP-002", "potential_impact": "what in THESE criteria may change or be wrong depending on the answer",
 "readiness_effect": "NEEDS_CLARIFICATION", "interim_assumption": "optional, clearly provisional"}
```
Rules
- Criteria must be observable and testable, include negative/exception paths where the requirement or a decision supports them, and stay inside what the text and the approved decisions say. Never invent values, roles or behaviour.
- If `ac gaps` lists `document_already_states` (`req show`: `existing_ac_hint`), those are acceptance statements the upstream document already makes: do not contradict or duplicate them.
- Decision IDs and the requirement's text hash are linked automatically; the record is pinned to the requirement text it was written for.
- The enriched copy shows only **approved** criteria. A candidate lives in `acceptance/candidates/` until a human approves it.

## Criteria that depend on open (carried-forward) gaps
A requirement can be `READY_WITH_OPEN_GAPS` because the human chose **Carry Forward**. Its criteria are written, but honestly:
1. Before writing, run `ac gaps --requirements LIB-007[,…]` (empty = every requirement). It returns each requirement's status and every open gap with its question, description, why it matters and whether it blocks. A gap that still **blocks** means no criteria (`REQUIREMENT_BLOCKED`); never work around that.
2. Declare **every** open gap in `open_gap_refs` (the core refuses `OPEN_GAP_MISSING`; it also refuses a gap that does not exist, is already resolved, or is not a gap of this requirement). Write `potential_impact` from the gap and the requirement only: which criterion, rule, value or test depends on the answer.
3. Choose `readiness_effect`: `NEEDS_CLARIFICATION` (default) when it can be built and tested but a detail may change; `NOT_READY` when the core behaviour cannot be built or verified until the gap is answered. The core derives the record's `readiness` (`READY_FOR_DEVELOPMENT` / `NEEDS_CLARIFICATION` / `NOT_READY`); you cannot set it.
4. Where a criterion depends on the open point, say so in it (e.g. *"…then the page loads within the target set by GAP-002 (not yet decided)"*) rather than inventing a number. An `interim_assumption` is rendered as "NOT approved, to be confirmed"; it never goes into the decisions and must not be copied into the criteria as if agreed.
5. In your summary to the human, list requirements by readiness: ready, needs clarification (with the gap IDs) and not ready.

### When a carried-forward gap is resolved
`gap resolve` flags every record that named the gap (including approved ones) and reports `acs_to_update`. Update each with `ac edit KEY --file … --reason "Applied DEC-nnn"` (a JSON of the changed fields: `acceptance_criteria`, `open_gap_refs`, `assumptions`, `notes`): remove the resolved gap from `open_gap_refs` (the core refuses a record that still lists it), apply the decision's validated interpretation to the criteria and assumptions, and keep any other open gaps declared. The core recomputes `decision_ids` and `readiness` and creates a new version that goes through `ac-review` again; an approved earlier version stays in force, and in the enriched copy, until the new one is approved. If a new gap is opened later for a requirement, its criteria are flagged the same way and must declare it.

Before presenting: `validate`, `trace` (list `uncovered_by_any_ac`), then a brief semantic self-review (unsupported assumptions, ambiguity, consistency). For more than a handful of requirements, give their keys to `ba-agent:ac-writer` (and then `ba-agent:quality-reviewer`): they return at most 8 lines and a file, and you still run `ac add`.

**Where they are saved:** `ac add` writes each candidate to `acceptance/candidates/md/` (records in `json/`). Tell the user the `md_folder` path and `md_files` count from `files_saved` before review starts (`../save-checkpoint.md`).

Next: `ac-review`.
