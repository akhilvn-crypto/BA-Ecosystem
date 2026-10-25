---
name: user-story-generation
description: Generate candidate user stories with Given/When/Then criteria from the approved baseline only, linked to requirement and decision IDs. Use after a baseline is confirmed.
---

# User-story generation

Stories come from the **approved baseline** (`baseline show`), never from the raw BRD alone, and only for requirements that are `READY`. The core refuses blocked or unknown requirements, a stale/unapproved baseline, duplicates and non-Given/When/Then criteria.

Per story (JSON list → `state/inbox/stories.json` → `story add --file …`; IDs `US-001…` are assigned):
```json
{"title": "short outcome-focused title",
 "user_story": {"role": "confirmed role", "capability": "to <do something> (rendered as: I want to …)", "value": "business value"},
 "acceptance_criteria": ["Given … when … then … (observable)", "negative/exception path if supported by the requirements"],
 "requirement_ids": ["REQ-001"], "dependencies": [], "assumptions": [],
 "notes": {"business_rules": "…", "validation_exceptions": "…", "non_functional": "…"},
 "unresolved_decisions": []}
```
`unresolved_decisions` is **always present** (empty when no gap is open). For a requirement that carries an open gap, it holds one entry per gap:
```json
{"gap_id": "GAP-002", "potential_impact": "what in THIS story may change or be wrong depending on the answer",
 "readiness_effect": "NEEDS_CLARIFICATION", "interim_assumption": "optional, clearly provisional"}
```
Rules
- Phrase `capability` starting with "to" (e.g. "to create a task") and `value` as the benefit without "so that" (it is added for you).
- Small enough to deliver and verify while keeping business value; split when outcomes are independently deliverable or criteria become unwieldy.
- Use the role and value the BRD/decisions actually give. If a material role/value is unknown, ask the user or list it under `assumptions` — never fabricate.
- Don't write stories for unsupported features. Criteria must be observable and testable and include negative/exception paths where requirements or decisions support them.
- Decision IDs and open gap IDs are linked automatically from the data; `source_references` too.

## Stories that depend on open (carried-forward) gaps
A requirement can be `READY` while a MEDIUM/LOW gap on it is still open because the human chose **Carry Forward**. Such stories are written, but honestly:
1. Before writing, run `story gaps --requirements REQ-008[,REQ-…]` (or read `open_gap_ids` in `baseline show`). It returns each open gap with its question, description, why it matters and whether it blocks. Gaps that still **block** cannot have stories (the core refuses `REQUIREMENT_BLOCKED`); never work around that.
2. Declare **every** open gap in `unresolved_decisions` (the core refuses `UNRESOLVED_DECISION_MISSING`; it also refuses a gap that does not exist, is already resolved, or is not a gap of the story's requirements). Write `potential_impact` from the gap and the BRD only — which criterion, rule, value or test depends on the answer. Do not invent requirements, values or outcomes.
3. Choose `readiness_effect`: `NEEDS_CLARIFICATION` (default) when the story can be built and tested but a detail may change; `NOT_READY` when the core behaviour cannot be built or verified until the gap is answered. The core derives the story's `readiness` (`READY_FOR_DEVELOPMENT` / `NEEDS_CLARIFICATION` / `NOT_READY`) from these entries — you cannot set it directly.
4. Acceptance criteria must stay inside what the requirements and approved decisions support. Where a criterion depends on the open point, say so in the criterion (e.g. *"…then the page loads within the target set by GAP-002 (not yet decided)"*) rather than inventing a number. An `interim_assumption` (e.g. for estimation) is rendered as "NOT approved, to be confirmed"; it is never an approved decision, never goes into `decision_ids`, and must not be copied into the acceptance criteria as if agreed.
5. In your summary to the human, list stories by readiness: ready, needs clarification (with the gap IDs) and not ready.

### When a carried-forward gap is resolved
`gap resolve` flags every story that named the gap (including approved ones) and reports `stories_to_update`. After the baseline is refreshed and confirmed (`requirements-baseline`), update each story with `story edit` (write a JSON file of changed fields): remove the resolved gap from `unresolved_decisions` (the core refuses a story that still lists it), apply the decision to the acceptance criteria and assumptions using the decision's validated interpretation, and keep any other open gaps declared. The core recomputes `open_gap_ids`, `decision_ids` and `readiness` and creates a new version that goes through `story-review` again; an approved earlier version stays in force until the new version is approved. A story that still names a resolved gap cannot be cleared with `confirm-unaffected`. If a new gap is opened later for a story's requirements, the story is flagged the same way and must declare it.

Before presenting: `validate`, `trace` (coverage: every READY requirement should end up with a story; list `uncovered_by_any_story`), then a brief semantic self-review (unsupported assumptions, ambiguity, consistency). For more than a handful of stories, give the requirement IDs to `ba-agent:story-builder` (and then `ba-agent:quality-reviewer`) instead of drafting them here: they return at most 8 lines and a file, and you still run `story add`.

**Where they are saved:** `story add` writes each candidate to `stories/candidates/md/US-nnn.md` (records in `json/`). Tell the user the `md_folder` path and `md_files` count from `files_saved` before review starts (`../ba-workflow/references/save-checkpoint.md`).

Next: `story-review`.
