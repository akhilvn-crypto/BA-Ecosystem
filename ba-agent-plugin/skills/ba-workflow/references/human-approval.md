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
2. **Conflicts?** Compare with the BRD text, related requirements (business rules included), earlier decisions, earlier answers — for typed answers and for a chosen suggested answer alike. `gap answer` returns advisory `candidate_conflicts`; review each one yourself — the check is a safety net, not a substitute for reading. Look for contradictions, changed thresholds/permissions/timing, new scope, ambiguity.
3. If conflicting: `gap conflict` (cite both IDs), explain neutrally ("REQ-003 says at most 5; your answer says 8"), and ask the human to **clarify, explicitly confirm a change, or defer (`Defer This Gap`, with a reason)**. Do not choose a winner; the gap stays open until a validated, conflict-free answer exists. Preserve the raw answer and the original requirement. A later clarifying answer marks the earlier conflict `CLARIFIED` when it resolves the gap.
4. If the human confirms a change: `gap resolve --conflict-status CHANGE_CONFIRMED --conflicting <ids> --change-by-name … --change-by-role … --change-statement "<their words>" --requirement-updates <json>`. The core versions the requirement, supersedes decisions, reopens affected gaps and flags stories/baseline for impact review. Then reassess the impacted requirements and gaps.
5. Only when complete and conflict-free: `gap resolve --conflict-status PASSED --reviewed <candidate ids you checked>`.

## Answer later and defer
- **Answer Later** needs no reason: `gap answer-later` keeps the gap `OPEN` in the outstanding queue; nothing is recorded as an answer.
- **Defer This Gap** needs a stated reason (never invented by Claude), the name and role of the person deferring, and a revisit date when the human has one or policy requires one (`policy set --deferral-revisit-required`). The core records actor, decision owner, IST timestamp, reason and sets `DEFERRED`. Deferred, answered-later and open gaps are unresolved: CRITICAL/HIGH and material (compliance-sensitive) gaps keep blocking their requirements and stories; a MEDIUM/LOW gap stops blocking only once it is carried forward (below) or the human accepts the project-wide non-blocking policy.

## Carry Forward (MEDIUM/LOW gaps only)
- **Carry Forward** (`gap carry-forward`) lets the human continue to story generation without answering a MEDIUM/LOW, non-material gap. The gap **stays `OPEN`** — it is never resolved, no decision is created, and nothing the human or Claude assumes becomes a decision. The core records the person carrying it forward (name and role from the user, never invented), the decision owner, the IST timestamp and an optional reason, and writes an audit event.
- **All at once:** before the first MEDIUM/LOW gap the human is asked once whether to go through each or carry them all forward. `gaps carry-forward-all` applies exactly the same rules to every eligible gap — each gets its own record and `GAP_CARRIED_FORWARD` audit event, plus one `GAP_CARRIED_FORWARD_BULK` event — and needs the same named person; it skips every gap that may not be carried forward.
- **Not allowed** for CRITICAL/HIGH gaps or any `material` gap (security, compliance, scope, policy): the core refuses with `CARRY_FORWARD_NOT_ALLOWED` and the existing blocking rules apply — such a gap must be answered or deferred, and it keeps blocking the stories of the requirements it affects. If a carried gap later becomes ineligible, it blocks again and `validate` warns.
- Every story that depends on a carried-forward gap must name it (`unresolved_decisions`), describe the potential impact in its own words from the gap and the BRD, and is flagged `NEEDS_CLARIFICATION` or `NOT_READY`. Working assumptions are labelled "not approved"; they never go into `decision_ids`, `assumptions` of an approved decision or the baseline's decisions.
- Revisit later (`gaps next --carried` / `gap revisit`). Resolving it needs the usual validated, conflict-free answer; then linked stories are flagged, the baseline is refreshed and updated stories are reviewed again.

## Approvals
Stories and baselines are approved only by a human's explicit words, for an exact version. Pass them as `--statement`. Ask for approver name/role once; they are optional for stories (store `null` if the user declines) but never invented. After any edit, the new version needs a new approval; the previously approved version stays in force and in history until then.

## Applying a detected change
`changes apply` needs the name and role of a real person and their own words (`--statement`), like an approval, but it approves nothing: it authorises the core to mark artifacts stale / under review after the user has seen what changed and its impact. It never approves a changed story or an accepted BRD, never invalidates a confirmed decision (decisions are listed for re-validation; `gap reopen` is a separate human decision) and never removes an approval: an approved story keeps its recorded approval until a reviewer approves a newer version or edits it. A formatting-only difference needs no one's decision and is recorded automatically.

## Timestamps
IST (`Asia/Kolkata`, `+05:30`) from the host clock, written by the core. Response time (`received_at_ist`) and closure time (`closed_at_ist`) are separate. If the core reports `CLOCK_UNRELIABLE`, say so — never substitute a guessed time.
