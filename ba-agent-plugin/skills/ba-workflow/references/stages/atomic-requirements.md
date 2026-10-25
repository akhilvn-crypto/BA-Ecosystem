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

**Large BRDs:** `init` / `source outline` return `batches` and `delegate`. When `delegate` is true, do not read the sections here: give each batch of section IDs to a `ba-agent:requirement-analyst` helper (batches run in parallel). Each returns at most 8 lines and a file under `state/inbox/`; you run `req add --file` for each file, then `req pairs` and `req commit`.

**Where they are saved:** the core writes every requirement into the confirmed project folder (readable copies in `atomic-requirements/md/`, records in `json/`); you never write requirement files yourself. `req add` writes each requirement the moment it is accepted - nothing waits for `req commit`, so an interrupted session loses no requirement already added. After `req commit`, say in one line how many requirements were saved and where (`files_saved` -> `atomic-requirements/`), then go straight on to gap analysis without waiting (`../save-checkpoint.md`). If `files_saved` is missing or empty, say so plainly and stop.
