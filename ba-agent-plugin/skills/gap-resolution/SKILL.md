---
name: gap-resolution
description: Interactively resolve gaps with the human - identify the decision owner first, present a few gaps at a time, accept free-text answers, check each answer for completeness and conflicts, and close gaps with an IST-stamped audit record, or defer, skip and reopen them. Use when gaps are waiting for input.
---

# Gap resolution (human in the loop)

Read `../ba-workflow/references/human-approval.md` first — it holds the identity, material-gap and conflict rules you must follow.

## 0. Decision-owner preflight (once, before the first gap)
Ask: *"Who will make/confirm the business decisions for these gaps? Please provide their name and role (for example, Priya Nair — Product Owner)."* Allow "that's me" (still get name and role). Then `owner set --name … --role …`.
Owner unknown → `owner unknown --choice pause|analysis-only`, explain that triage cannot start, ask which they prefer. If a gap needs a different owner → `owner set … --gap GAP-00x`. Owner changes are audited; prior decisions keep their original owner.

## 1. Present (≤ 3 at a time)
`gaps next --limit 3`. For each gap show: ID · category · priority, impacted requirement + source reference, concise evidence, why it matters, **the one question**, and the options: *answer · ask me to reword/clarify · defer · skip for now*. Accept free text; don't force options.

## 2. For each answer
1. `gap answer GAP-00x --response "<their words>"` (use `--args-file` for odd characters). Add `--respondent-name/--respondent-role` if the answerer is not obviously the owner. Look at the returned `candidate_conflicts`.
2. **Complete?** If not: `gap followup …` then ask one concise follow-up (the earlier answer is kept).
3. **Conflict?** Check against the BRD section, requirements, earlier decisions, earlier answers. If there is one: `gap conflict …`, explain both statements with their IDs and the impact, and ask the human to *clarify · confirm a change · defer*. Never pick a winner. A confirmed change goes through `gap resolve --conflict-status CHANGE_CONFIRMED …` (see human-approval.md); afterwards reassess impacted requirements, gaps and stories.
4. **Resolve** only when complete and conflict-free. Ask the human: *"Shall I close GAP-00x as resolved with this meaning: '<interpretation>'? Please give your name and role as the person closing it."* Then:
   `gap resolve GAP-00x --answer-id A1 --interpretation "…" --closed-by-name … --closed-by-role … --closure-summary "…" --confirmation "<their confirming words>" --conflict-status PASSED --reviewed <ids you checked>`
   - Material gap and closer ≠ owner → the core answers `AWAITING_OWNER_CONFIRMATION`; the gap stays open until the owner confirms (`--owner-confirmation`).
   - Missing identity → ask; do not guess. The gap stays pending.
5. Report back: the decision ID, who closed it, the IST closure time (as returned by the core).

## 3. Other actions
`gap defer GAP-00x --reason … [--owner …] [--revisit-by …]` (kept visible; blocks affected requirements) · `gap skip` · `gap revisit` (deferred → ask again) · `gap reopen GAP-00x --reason … --by-name … --by-role …` (invalidates its decision; baseline/stories get impact flags).

Never close a gap on your own recommendation, on silence, or because the BRD embeds a "decision". When none remain to present: `baseline create` (→ `requirements-baseline`). If the user stops mid-way, nothing is lost: answers and presented gaps are saved; resume with `/ba-agent:review`.
