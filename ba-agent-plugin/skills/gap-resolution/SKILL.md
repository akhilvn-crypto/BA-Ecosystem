---
name: gap-resolution
description: Resolve gaps with the human, one at a time, highest priority first, and close a gap only with a validated, conflict-free answer. Use when gaps are waiting for input.
---

# Gap resolution (human in the loop)

This file is the loop. The CLI builds every gap view; **relay it, do not compose it**. Open a reference only when you reach its branch (listed at the end) — never preload them.

## 0. Decision owner (once, before the first gap)
`status` shows the owner. If none: ask *"Who will make/confirm the business decisions for these gaps? Please provide their name and role (for example, Priya Nair — Product Owner)."* ("That's me" still needs name and role.) Then `owner set --name … --role …`. Unknown → `owner unknown --choice pause|analysis-only` and ask which they prefer.

## 1. Present ONE gap
`gaps next --limit 1` (or `gaps next --id GAP-00x` in triage) returns a packet. The part you act on is `gaps[0].present`:
- `inline: true` → call `AskUserQuestion` with `present.dialog` **as it is** (load it with ToolSearch `select:AskUserQuestion` if needed). Send **no** chat message — the dialog already carries the priority, question, evidence and affected requirement.
- `inline: false` → send `present.card` as one chat message, then the same dialog. The card only exists for gaps with history (earlier answers, conflicts, deferral, carry-forward); never restate what the dialog says.
- `AskUserQuestion` unavailable or failing → send `present.card` (when there is one) and `present.fallback`, wait for the reply, do not retry in a loop.
Never pre-select or mark anything “(Recommended)”. A dismissed or empty dialog is **no answer**: say so in one line and ask again in chat.

Before the first MEDIUM/LOW gap, `bulk_carry_forward.offer_now: true` means ask the one-time *Go through each / Carry all forward* choice first (`references/actions.md` → Bulk).

## 2. Route what the human chose
| They picked / typed | Do | Detail |
|---|---|---|
| a typed answer | `gap answer GAP-00x --response "<their words>"` (`--args-file` for odd characters), then validate | `references/validate-and-resolve.md` |
| Suggested Answer | `gap answer GAP-00x --use-suggestion`, then validate exactly like typed text | same |
| Explain This Question | `gap explain GAP-00x`, explain in plain words with a real example, show the same dialog again — it is **not** an answer | `references/actions.md` |
| Carry Forward / Answer Later / Defer This Gap / Keep … | collect only what the action needs, run its command | `references/actions.md` |
| unclear or mixed text | one short question; never record an instruction as an answer | — |
If they ask for something the gap does not allow (carry forward a HIGH gap), say why in one sentence (`carry_forward_not_allowed_because`) and show the dialog again.

## 3. Close only when validated
A gap is `RESOLVED` only through `gap resolve`, after the answer is complete and checked against the BRD, related requirements, earlier decisions and earlier answers. Conflicts are shown to the human, never decided by you. When the decision owner gave the answer, **close it straight away** with `gap resolve GAP-00x --answer-id A1 --interpretation "..."`: no second question and no closer to ask for - the owner's own words are the confirmation and the core records the owner as the closer. Ask for a name only if someone else answered or is closing it. Report in one line: the decision ID and that the gap moved to `gaps/resolved` (`gap_file`). All steps: `references/validate-and-resolve.md`.

## 4. Next gap
After each gap is settled run `gaps next --limit 1`. Mention `deferred_due_for_revisit` or `carried_forward_open` once, only when non-empty. When nothing is left: `baseline create` (`requirements-baseline`). Unresolved CRITICAL/HIGH and sensitive gaps keep blocking their requirements and stories; say so plainly and never close a gap on your own recommendation, on silence, or because the BRD embeds a "decision". Interrupted? Everything is saved — resume with `/ba-agent:review`.

## References (load on demand)
- `references/actions.md` — Explain, Carry Forward, bulk carry-forward, Answer Later, Defer, Keep.
- `references/validate-and-resolve.md` — identities, completeness, conflicts, material gaps, `gap resolve`.
- `references/revisit.md` — reopen a carried-forward or deferred gap and update the stories that named it.
- `references/triage.md` — `/ba-agent:triage-gaps`.
- `../ba-workflow/references/choice-prompts.md` — only if a dialog needs something the packet did not provide.
