---
name: atomic-requirements
description: Decompose a parsed BRD into atomic, independently understandable requirements with stable IDs, verbatim source quotes and section references. Use after BRD ingestion, when the stage is BRD_PARSED, or when new BRD sections need requirements.
---

# Atomic requirements

Work section by section: `source section <SID>` (one section at a time; tables included), then write requirements for it.

**An atomic requirement** is one obligation, behaviour, rule, constraint or quality attribute that can be understood alone. Keep conditions/exceptions with the obligation; split independent obligations; never paraphrase away numbers, thresholds, roles or timing.

For each requirement produce (a JSON list, saved to `<project>/state/inbox/reqs.json` via `scripts/safe_write.py`):

```json
{"title": "…", "statement": "one precise sentence",
 "type": "FUNCTIONAL|BUSINESS_RULE|DATA|VALIDATION|INTEGRATION|SECURITY|NON_FUNCTIONAL|WORKFLOW|CONSTRAINT|OTHER",
 "source_section_ids": ["S1.2"], "source_excerpt": "VERBATIM text copied from the section",
 "actors": [], "preconditions": [], "outcomes": [], "constraints": [], "dependencies": [],
 "acceptance_info": ["only if the BRD states it"],
 "assumptions": ["anything you inferred — never present as fact"],
 "flags": [{"kind": "AMBIGUITY", "note": "…"}]}
```

- `source_excerpt` is checked against the real section text; a paraphrase or invented quote is rejected and **nothing in the batch is saved**.
- Mark ambiguity, possible duplication or contradiction in `flags`; never resolve them silently. The core also adds advisory `POSSIBLE_DUPLICATE` / `POSSIBLE_CONFLICT` flags (identical wording with a different number is reported as a conflict).
- Do not invent requirements the BRD does not support.

Then: `req add --file …` (IDs `REQ-001…` are assigned for you) → `req pairs` (cross-check candidates) → `req commit` (validates schema, IDs, source references; stage → `REQUIREMENTS_READY`).

**Where they are saved:** the core writes every requirement to `<project>/atomic-requirements/REQ-nnn.md` (readable) and `.json` (authoritative). Never write requirement files yourself. After `req commit`, use its `saved_to` and `files_on_disk` values, confirm the folder exists by listing it with your own file tools, and tell the user the full folder path. If the folder is empty or missing, say so plainly and stop — do not claim the requirements were saved.

Tell the user how many requirements were created and anything flagged. For large BRDs you may delegate a section range to the optional `ba-agent:requirement-analyst` agent (it writes a JSON file and returns a short summary — you still run `req add`).

Next: `gap-analysis`.
