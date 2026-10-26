# Validate an answer and close the gap

## Identities — never merge them silently
- **Decision owner** — accountable for confirming business answers (asked once, `owner set`; per-gap override `owner set --gap GAP-00x`).
- **Respondent** — who actually answered, if known (`--respondent-name/--respondent-role`).
- **Gap closer** — confirms the gap is resolved after validation. **Default: the decision owner.** If the owner (or nobody else, as `respondent`) gave the answer, leave `--closed-by-*`, `--confirmation` and `--closure-summary` out: the core records the owner as closer and their answer as the confirmation (audit `closer_basis: DECISION_OWNER_ANSWER`). If another person answered, the core refuses with `IDENTITY_REQUIRED`: ask the owner to confirm (their words go in `--confirmation`, with `--closed-by-name/--closed-by-role`). Anyone else closing it is named by the user.
Never use the account display name, e-mail or "the user" as a person. Missing identity ⇒ the gap stays pending; ask.

## Validate (before `gap resolve`)
`gap answer` stores the raw answer and returns `candidate_conflicts` (advisory: the text of every requirement, active decisions, accepted answers). Read them yourself; the check is a safety net, not a substitute.
1. **Complete?** Values, limits, exceptions answered? If not: `gap followup GAP-00x --answer-id A1 --note "<what is missing>"`, ask one concise follow-up; the earlier answer is kept.
2. **Conflict?** (contradiction, changed threshold/permission/timing, new scope, ambiguity.) If yes: `gap conflict GAP-00x --answer-id A1 --with <ids> --description "…"`, explain both statements neutrally with their IDs ("LIB-003 says at most 5; your answer says 8") and the impact, then ask: `Clarify`, `Confirm a change`, `Defer This Gap` (or `Carry Forward` if the gap allows it). Do not pick a winner; the gap stays open. A clarifying answer is a new `gap answer`, validated again.
3. **Confirmed change:** `gap resolve … --conflict-status CHANGE_CONFIRMED --conflicting <ids> --change-by-name … --change-by-role … --change-statement "<their words>"`. The core supersedes contradicted decisions, reopens affected gaps and flags the criteria of the affected requirements. A requirement among `--conflicting` is not edited (it belongs to the upstream document): the result lists it as `upstream_change_required`, so tell the user who must change it. Then reassess the impacted gaps.

## Close
Only when complete, conflict-free and reviewed. Do **not** ask "shall I close it?" - an answer that passed your checks is the decision. Run
`gap resolve GAP-00x --answer-id A1 --interpretation "<the meaning, in one sentence>" --conflict-status PASSED --reviewed <ids you checked>`
(add `--closed-by-name ... --closed-by-role ... --confirmation "<their words>"` only when someone other than the owner closes it). If the answer is open to more than one reading, ask which they meant before closing; never close on a guess.
- **Material gap** (scope, policy, security, compliance) and closer ≠ owner → the core answers `AWAITING_OWNER_CONFIRMATION`; the gap stays open until the owner confirms (`--owner-confirmation "<owner's words>"`). A recommendation is not a decision.
- Report in one line: the decision ID, that the gap moved to `gaps/resolved` (`gap_file`), and `acs_to_update` if any. The IST time and closer are in the record and `ADR-LOG.md`; repeat them only if asked.
