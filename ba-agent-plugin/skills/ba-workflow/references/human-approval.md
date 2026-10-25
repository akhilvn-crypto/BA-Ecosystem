# Human approval and identity (shared)

## Identities — never merge them silently
- **Decision owner** — accountable for confirming business answers; asked once, before the first gap (`owner set`).
- **Respondent** — who actually answered, if known. **Gap closer** — confirms a gap is resolved; name and role come from the user.
- Never use the account display name, e-mail or "the user" as a person. Missing identity ⇒ keep the item pending and ask.
Gap-specific rules (validation, conflicts, material gaps, carry-forward, deferral): `../../gap-resolution/references/`.

## Approvals
Stories and baselines are approved only by a human's explicit words, for an exact version, passed as `--statement`. Ask for the approver's name and role once; they are optional for stories (store nothing if declined) but never invented. After any edit the new version needs a new approval; the previously approved version stays in force and in history until then. Silence, a quality score, or your own "looks fine" is never approval.

## Applying a detected change
`changes apply` needs a real person's name, role and own words, like an approval, but it approves nothing: it authorises the core to mark artifacts stale or under review after the user has seen what changed and its impact. It never approves a changed story or an accepted BRD, never invalidates a confirmed decision (`gap reopen` is a separate human decision) and never removes an approval. A formatting-only difference needs no one's decision.

## Timestamps
IST (`Asia/Kolkata`, `+05:30`) from the host clock, written by the core. Response time and closure time are separate. On `CLOCK_UNRELIABLE`, say so — never substitute a guessed time.
