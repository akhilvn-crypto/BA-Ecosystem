---
name: gap-analysis
description: Find missing, ambiguous, conflicting or untestable information in the requirements and record evidence-based gaps with priority and one question each. Use when the stage is REQUIREMENTS_READY or GAP_ANALYSIS.
---

# Gap analysis

Taxonomy and priority rubric: `references/taxonomy.md` (load it now).

**Per requirement** (load only that requirement and its own BRD section via `source section`, never the whole BRD):
1. Assess completeness, clarity, consistency, feasibility and testability.
2. Raise a gap **only if** the missing or unclear information could change implementation, acceptance, risk or business outcome, and the BRD does not already answer it.
3. Every gap needs: `category` (exact taxonomy name), `title`, `description`, `evidence` (quote/location), `why_it_matters`, **one** focused `question` whose answer would resolve it, `impacted_requirement_ids`, `priority` + `priority_rationale`, optionally a `suggested_answer` `{answer, source}` — **only** when the BRD, a requirement or an earlier decision actually supports one, with `source` naming it (otherwise leave it out; it is shown to the human as a suggestion, never as an answer) — and `material: true` if the answer could change scope, policy, security, compliance, a legal/financial business rule or an existing requirement. `material` and `priority` decide whether the human may later **Carry Forward** the gap: only non-material MEDIUM/LOW gaps can be carried forward, so never under-rate a gap that touches a critical security, compliance or business rule to make it "easier".
4. Avoid generic questions ("anything else we should know?").

**Cross-requirement pass** (after the individual pass): run `req pairs`; look for contradictions, duplicated rules, dependencies, and gaps that only appear when requirements are read together. Contradictions become gaps in the category *Contradiction, duplication, or dependency* and cite both requirement IDs.

Save as JSON (`state/inbox/gaps.json`) → `gaps add --file …` → when both passes are done: `gaps commit --cross-pass-done`.

Do **not** present gaps to the user here, and never before the decision owner is known — that is `gap-resolution` (the core refuses `gaps next` without an owner).

For more than about ten requirements, give batches of requirement IDs to `ba-agent:gap-analyst` helpers (in parallel) instead of analysing them here: each writes JSON under `state/inbox/` and returns at most 8 lines; you run `gaps add`.

**Saved as found:** `gaps add` files every gap in `gaps/open/` immediately (readable copy in `md/`). After `gaps commit` tell the user in one line how many gaps were found, by priority, and that they are in `gaps/open/`; then move straight to `gap-resolution` (which asks for the decision owner). Gaps move to `gaps/resolved/` by themselves when answered.

Re-analysis after requirement changes: only the changed or flagged requirements, then `gaps commit` again.
