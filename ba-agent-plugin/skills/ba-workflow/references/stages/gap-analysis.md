# Gap analysis

Taxonomy and priority rubric: `gap-analysis/taxonomy.md` (load it now).

**Per requirement** (`req show <key>` for that one only, never every file):
1. Assess completeness, clarity, consistency, feasibility and testability.
2. Raise a gap **only if** the missing or unclear information could change implementation, acceptance, risk or business outcome, and the document does not already answer it. If `req show` lists `existing_ac_hint` (acceptance statements the upstream wrote), build on them; do not ask what they answer.
3. Every gap needs: `category` (exact taxonomy name), `title`, `description`, `evidence` (where in the document), `evidence_quote` (a **verbatim** quote from the requirement text; the core checks it; leave it out when the gap is about something the text does not say), `why_it_matters`, **one** focused `question` whose answer would resolve it, `impacted_req_keys` (full keys or IDs that are unique in the folder), `priority` + `priority_rationale`, optionally a `suggested_answer` `{answer, source}` — **only** when the document, a requirement or an earlier decision actually supports one, with `source` naming it (otherwise leave it out; it is shown to the human as a suggestion, never as an answer) — and `material: true` if the answer could change scope, policy, security, compliance, a legal/financial business rule or an existing requirement. `material` and `priority` decide whether the human may later **Carry Forward** the gap: only non-material MEDIUM/LOW gaps can be carried forward, so never under-rate a gap that touches a critical security, compliance or business rule to make it "easier".
4. Avoid generic questions ("anything else we should know?").

**Cross-requirement pass** (after the individual pass; spans all files): run `req pairs`; look for contradictions, duplicated rules, dependencies, and gaps that only appear when requirements are read together. Contradictions become gaps in the category *Contradiction, duplication, or dependency* and cite both requirement keys.

Save as JSON (`state/inbox/gaps.json`) → `gaps add --file …` → when both passes are done: `gaps commit --cross-pass-done`.

Do **not** present gaps to the user here, and never before the decision owner is known — that is `gap-resolution` (the core refuses `gaps next` without an owner).

For more than about ten requirements, give batches of requirement keys to `ba-agent:gap-analyst` helpers (in parallel) instead of analysing them here: each writes JSON under `state/inbox/` and returns at most 8 lines; you run `gaps add`.

**Saved as found:** `gaps add` files every gap in `gaps/open/` immediately (readable copy in `md/`) and shows it under its requirement in the enriched copy. After `gaps commit` tell the user in one line how many gaps were found, by priority, and that they are in `gaps/open/`; then move straight to `gap-resolution` (which asks for the decision owner). Gaps move to `gaps/resolved/` by themselves when answered.

Re-analysis after requirement changes: only the changed or flagged requirements, then `gaps commit` again.
