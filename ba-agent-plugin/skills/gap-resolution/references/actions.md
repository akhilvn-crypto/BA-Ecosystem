# Gap actions — read the section for the action the human chose

Each action is a request. Collect only what is listed, never invent a name, role, reason or date, and run the one command.

## Explain This Question
Not an answer; record nothing as one. `gap explain GAP-00x` returns the facts (it audits the request). Then, for a non-technical Product Manager and in under a minute:
1. **What we are asking** — one simple sentence (`what_we_are_asking`).
2. **What is missing** — vague, absent or contradictory in the BRD (`missing_information`, `evidence`) and what developers/testers would have to guess.
3. **Why it matters** — `why_it_matters`, and `impact_on_requirements` (which behaviour or test changes; whether stories are blocked).
4. **A real-world example** from the BRD's own domain where different answers lead to visibly different outcomes.
5. **What a good answer looks like** — a number, a yes/no, a role, a list of exceptions; one sample, marked as illustration only.
6. `if_carried_forward`, one sentence, only when present.
Then show the same dialog again. They may ask for another example in “Other”.

## Carry Forward (MEDIUM/LOW, non-material gaps only)
The gap stays **OPEN — not resolved, no decision recorded** — and stops blocking story generation. Ask once, in chat: **who is choosing this** (name and role; the decision owner if they say so — still both) and an optional short reason. Then
`gap carry-forward GAP-00x --by-name … --by-role … [--reason "<their words>"]`.
Say plainly: *"GAP-002 stays open. Stories for REQ-008 will be written, each naming this gap and its possible impact, marked as needing clarification. We will not treat any assumption as a decision."* The core refuses CRITICAL/HIGH and sensitive gaps (`CARRY_FORWARD_NOT_ALLOWED`); never offer it for them.

## Bulk: Go through each / Carry all forward (once, before the first MEDIUM/LOW gap)
When `gaps next` returns `bulk_carry_forward.offer_now: true` and you have not asked this round, do not show the gap yet. Send **one** chat line: *"N non-blocking MEDIUM/LOW gaps are left (IDs from `eligible_gap_ids`). Go through each, or carry all forward? Carrying forward keeps every one OPEN and visible; stories are still written and flagged."* Then `AskUserQuestion` (`header: "MEDIUM/LOW"`, exactly two options, no “(Recommended)”): `Go through each` — *"Show me each MEDIUM/LOW gap one at a time"*; `Carry all forward` — *"Keep all N open, continue to the stories, and flag them until answered"*.
- Go through each → carry on, do not ask again this round.
- Carry all forward → collect name, role and optional reason in one chat message, then `gaps carry-forward-all --by-name … --by-role … [--reason …]`. CRITICAL/HIGH, sensitive, deferred and answered-later gaps are untouched (`not_carried` says why). Tell them: *"N gaps stay OPEN, none is resolved."*
- Typed “all”/“carry them” → second path; “one by one” → first; anything else, one short question.
Triage mode never asks this. No `AskUserQuestion`: *1 Go through each · 2 Carry all forward*.

## Answer Later (CRITICAL/HIGH/sensitive)
No reason needed. `gap answer-later GAP-00x`. The gap stays OPEN, is asked again after this round and keeps blocking. One sentence: "GAP-003 stays open and still blocks the stories for REQ-003."

## Defer This Gap (CRITICAL/HIGH/sensitive)
Collect in one chat message: the **reason** (mandatory), the **revisit date** YYYY-MM-DD (mandatory when `defer_requires_revisit_date`, otherwise record one if given), **who is deferring** (name and role), optionally who the gap waits on. Then
`gap defer GAP-00x --reason "<their words>" --by-name … --by-role … [--revisit-by YYYY-MM-DD] [--owner "<waiting on>"]`.
Say plainly: **deferred is not resolved** — it stays outstanding and keeps blocking its requirements' stories. A deferred gap returns with `gap revisit GAP-00x`.

## Keep (triage)
`gap keep GAP-00x`. An open gap becomes Answer Later; a deferred or carried-forward gap is left untouched and only the decision is audited. Nothing becomes resolved.

## Other
`gap reopen GAP-00x --reason … --by-name … --by-role …` invalidates its decision (baseline and stories get impact flags). `policy set …` only when the human asks (`--deferral-revisit-required CRITICAL,HIGH`, or `--carry-forward true` for project-wide acceptance).

Fallback without `AskUserQuestion` — use `present.fallback`; the behaviour and recorded data are identical.
