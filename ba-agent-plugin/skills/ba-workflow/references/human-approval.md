# Human approval, identity and conflicts (shared reference)

## Three identities (never merge them silently)
- **Decision owner** — accountable for confirming business answers. Asked **once**, before the first gap: *"Who will make/confirm the business decisions for these gaps? Please provide their name and role (for example, Priya Nair — Product Owner)."* If unknown: record it (`owner unknown --choice pause|analysis-only`), explain triage cannot start, and ask whether to pause or continue non-decision analysis. Owner change ⇒ `owner set` again (history is kept). A different owner for one gap ⇒ `owner set --gap GAP-00x`.
- **Respondent** — who actually answered, if known (`--respondent-name/--respondent-role`).
- **Gap closer** — who confirms the gap is resolved after validation. May be the owner or another authorised participant; name and role must be given by the user. If the user is both owner and closer, record both explicitly (same person, two roles).
Never use the account display name, e-mail, or "the user" as a person. If identity is missing, keep the gap pending and ask.

## Material gaps
Answers that change scope, policy, security, compliance or an existing requirement need the **decision owner's** explicit confirmation. If the closer is someone else, `gap resolve` refuses (`AWAITING_OWNER_CONFIRMATION`) until `--owner-confirmation "<owner's words>"` is supplied. A recommendation is not a decision.

## Validating every answer (before `gap resolve`)
1. **Complete?** Does it actually answer the question (values, limits, exceptions)? If not → `gap followup`, ask one concise follow-up; keep the earlier answer.
2. **Conflicts?** Compare with the BRD text, related requirements, earlier decisions, earlier answers. `gap answer` returns advisory `candidate_conflicts`; review each one yourself — the check is a safety net, not a substitute for reading. Look for contradictions, changed thresholds/permissions/timing, new scope, ambiguity.
3. If conflicting: `gap conflict` (cite both IDs), explain neutrally ("REQ-003 says at most 5; your answer says 8"), and ask the human to **clarify, explicitly confirm a change, or defer**. Do not choose a winner. Preserve the raw answer and the original requirement.
4. If the human confirms a change: `gap resolve --conflict-status CHANGE_CONFIRMED --conflicting <ids> --change-by-name … --change-by-role … --change-statement "<their words>" --requirement-updates <json>`. The core versions the requirement, supersedes decisions, reopens affected gaps and flags stories/baseline for impact review. Then reassess the impacted requirements and gaps.
5. Only when complete and conflict-free: `gap resolve --conflict-status PASSED --reviewed <candidate ids you checked>`.

## Approvals
Stories and baselines are approved only by a human's explicit words, for an exact version. Pass them as `--statement`. Ask for approver name/role once; they are optional for stories (store `null` if the user declines) but never invented. After any edit, the new version needs a new approval; the previously approved version stays in force and in history until then.

## Timestamps
IST (`Asia/Kolkata`, `+05:30`) from the host clock, written by the core. Response time (`received_at_ist`) and closure time (`closed_at_ist`) are separate. If the core reports `CLOCK_UNRELIABLE`, say so — never substitute a guessed time.
