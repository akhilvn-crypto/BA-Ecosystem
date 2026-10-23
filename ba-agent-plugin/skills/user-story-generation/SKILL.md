---
name: user-story-generation
description: Generate candidate user stories with Given/When/Then acceptance criteria from the approved requirements baseline only, linked to requirement and decision IDs. Use when a baseline has been confirmed and stories have not been generated yet or new READY requirements need stories.
---

# User-story generation

Stories come from the **approved baseline** (`baseline show`), never from the raw BRD alone, and only for requirements that are `READY`. The core refuses blocked or unknown requirements, a stale/unapproved baseline, duplicates and non-Given/When/Then criteria.

Per story (JSON list → `state/inbox/stories.json` → `story add --file …`; IDs `US-001…` are assigned):
```json
{"title": "short outcome-focused title",
 "user_story": {"role": "confirmed role", "capability": "to <do something> (rendered as: I want to …)", "value": "business value"},
 "acceptance_criteria": ["Given … when … then … (observable)", "negative/exception path if supported by the requirements"],
 "requirement_ids": ["REQ-001"], "dependencies": [], "assumptions": [],
 "notes": {"business_rules": "…", "validation_exceptions": "…", "non_functional": "…"}}
```
Rules
- Phrase `capability` starting with "to" (e.g. "to create a task") and `value` as the benefit without "so that" (it is added for you).
- Small enough to deliver and verify while keeping business value; split when outcomes are independently deliverable or criteria become unwieldy.
- Use the role and value the BRD/decisions actually give. If a material role/value is unknown, ask the user or list it under `assumptions` — never fabricate.
- Don't write stories for unsupported features. Criteria must be observable and testable and include negative/exception paths where requirements or decisions support them.
- Decision IDs and open gaps are linked automatically from the data; `source_references` too.

Before presenting: `validate`, `trace` (coverage: every READY requirement should end up with a story; list `uncovered_by_any_story`), then a brief semantic self-review (unsupported assumptions, ambiguity, consistency). For large sets the optional `ba-agent:story-builder` and `ba-agent:quality-reviewer` agents can draft and independently review; you still run `story add`.

Next: `story-review`.
