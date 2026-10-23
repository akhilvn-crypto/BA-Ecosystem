---
name: gap-resolution
description: Interactively resolve gaps with the human - identify the decision owner first, present ONE gap at a time with an answer box, accept free-text answers, check each answer for completeness and conflicts, and close gaps with an IST-stamped audit record, or defer, skip and reopen them. Use when gaps are waiting for input.
---

# Gap resolution (human in the loop)

Read `../ba-workflow/references/human-approval.md` first — it holds the identity, material-gap and conflict rules you must follow. Read `../ba-workflow/references/choice-prompts.md` before showing any choice dialog.

## 0. Decision-owner preflight (once, before the first gap)
Ask: *"Who will make/confirm the business decisions for these gaps? Please provide their name and role (for example, Priya Nair — Product Owner)."* Allow "that's me" (still get name and role). Then `owner set --name … --role …`.
Owner unknown → `owner unknown --choice pause|analysis-only`, explain that triage cannot start, and ask which they prefer (`AskUserQuestion`: `Pause`, `Analysis only`). If a gap needs a different owner → `owner set … --gap GAP-00x`. Owner changes are audited; prior decisions keep their original owner.

## 1. Present ONE gap at a time, with an answer box
`gaps next --limit 1` returns the single gap to work on. Never list several gaps in one message: the user should concentrate on one question and answer it, then see the next. Say where they are ("Gap 2 of 7") so progress is visible.

Two steps, in the same turn:
1. **Chat message** (normal markdown) with the gap packet: `GAP-003 · category · priority (Gap 2 of 7)`, impacted requirement(s) + source reference, concise evidence, why it matters, and **the one question**.
2. **`AskUserQuestion`** (load it with ToolSearch `select:AskUserQuestion` if it is not already callable) so the user gets a prompt with a text box. Keep the dialog short and plain-text; the detail stays in the message above (see `choice-prompts.md`). One call = one gap:
- `header`: the gap ID (e.g. `GAP-003`; max 12 characters).
- `question`: one or two plain sentences — the question itself, ending with *"Type your answer in the “Other” box, or pick an option."* No markdown, no lists.
- `options` (2–4, single-select, each with a `description`): optionally ONE candidate answer taken from the BRD or earlier decisions (label e.g. `Use 30 days`, description *"Suggestion from BRD §4.2 — only an answer if you pick it"*), then `Explain question`, `Defer this gap`, `Skip for now`. The user's typed text in “Other” is their free-text answer — never force them into an option.
- Picking a suggested option is the user's answer only because they picked it; record it with `gap answer` like any other, and still run the completeness/conflict checks below. Never pre-select, and never treat silence as an answer.

If `AskUserQuestion` is not available or fails in this surface, fall back to plain chat: send just that one gap with the options as a short numbered list, ask for the answer in the reply, and wait. Do not move to the next gap until this one is answered, deferred or skipped.

## 2. For each answer
1. `gap answer GAP-00x --response "<their words>"` (use `--args-file` for odd characters). Add `--respondent-name/--respondent-role` if the answerer is not obviously the owner. Look at the returned `candidate_conflicts`.
2. **Complete?** If not: `gap followup …` then ask one concise follow-up (the earlier answer is kept).
3. **Conflict?** Check against the BRD section, requirements, earlier decisions, earlier answers. If there is one: `gap conflict …`, explain both statements with their IDs and the impact, and ask the human to *clarify · confirm a change · defer* (explanation in chat, then an `AskUserQuestion` with those three options). Never pick a winner. A confirmed change goes through `gap resolve --conflict-status CHANGE_CONFIRMED …` (see human-approval.md); afterwards reassess impacted requirements, gaps and stories.
4. **Resolve** only when complete and conflict-free. Ask the human: *"Shall I close GAP-00x as resolved with this meaning: '<interpretation>'? Please give your name and role as the person closing it."* Then:
   `gap resolve GAP-00x --answer-id A1 --interpretation "…" --closed-by-name … --closed-by-role … --closure-summary "…" --confirmation "<their confirming words>" --conflict-status PASSED --reviewed <ids you checked>`
   - Material gap and closer ≠ owner → the core answers `AWAITING_OWNER_CONFIRMATION`; the gap stays open until the owner confirms (`--owner-confirmation`).
   - Missing identity → ask; do not guess. The gap stays pending.
5. Report back: the decision ID, who closed it, the IST closure time (as returned by the core).

## 3. Other actions
`gap defer GAP-00x --reason … [--owner …] [--revisit-by …]` (kept visible; blocks affected requirements) · `gap skip` · `gap revisit` (deferred → ask again) · `gap reopen GAP-00x --reason … --by-name … --by-role …` (invalidates its decision; baseline/stories get impact flags).

After each gap is settled (resolved, deferred or skipped) run `gaps next --limit 1` again and present the next one the same way.

Never close a gap on your own recommendation, on silence, or because the BRD embeds a "decision". When none remain to present: `baseline create` (→ `requirements-baseline`). If the user stops mid-way, nothing is lost: answers and presented gaps are saved; resume with `/ba-agent:review`.
