# Validate an answer and close the gap

## Identities — never merge them silently
- **Decision owner** — accountable for confirming business answers (asked once, `owner set`; per-gap override `owner set --gap GAP-00x`).
- **Respondent** — who actually answered, if known (`--respondent-name/--respondent-role`).
- **Gap closer** — confirms the gap is resolved after validation. May be the owner or another authorised person; name and role come from the user. If one person is both, record both explicitly.
Never use the account display name, e-mail or "the user" as a person. Missing identity ⇒ the gap stays pending; ask.

## Validate (before `gap resolve`)
`gap answer` stores the raw answer and returns `candidate_conflicts` (advisory: requirements incl. business rules, the BRD excerpts, active decisions, accepted answers). Read them yourself; the check is a safety net, not a substitute.
1. **Complete?** Values, limits, exceptions answered? If not: `gap followup GAP-00x --answer-id A1 --note "<what is missing>"`, ask one concise follow-up; the earlier answer is kept.
2. **Conflict?** (contradiction, changed threshold/permission/timing, new scope, ambiguity.) If yes: `gap conflict GAP-00x --answer-id A1 --with <ids> --description "…"`, explain both statements neutrally with their IDs ("REQ-003 says at most 5; your answer says 8") and the impact, then ask: `Clarify`, `Confirm a change`, `Defer This Gap` (or `Carry Forward` if the gap allows it). Do not pick a winner; the gap stays open. A clarifying answer is a new `gap answer`, validated again.
3. **Confirmed change:** `gap resolve … --conflict-status CHANGE_CONFIRMED --conflicting <ids> --change-by-name … --change-by-role … --change-statement "<their words>" --requirement-updates <json file>`. The core versions the requirement, supersedes decisions, reopens affected gaps and flags stories and the baseline. Then reassess the impacted requirements and gaps.

## Close
Only when complete, conflict-free and reviewed. Ask: *"Shall I close GAP-00x as resolved with this meaning: '<interpretation>'? Please give your name and role as the person closing it."* Then
`gap resolve GAP-00x --answer-id A1 --interpretation "…" --closed-by-name … --closed-by-role … --closure-summary "…" --confirmation "<their confirming words>" --conflict-status PASSED --reviewed <ids you checked>`
- **Material gap** (scope, policy, security, compliance) and closer ≠ owner → the core answers `AWAITING_OWNER_CONFIRMATION`; the gap stays open until the owner confirms (`--owner-confirmation "<owner's words>"`). A recommendation is not a decision.
- Report in two lines: the decision ID, who closed it, the IST time returned by the core, and `stories_to_update` if any.
