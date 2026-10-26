# Human approval and identity (shared)

## Identities — never merge them silently
- **Decision owner** — accountable for confirming business answers; asked once, before the first gap (`owner set`).
- **Respondent** — who actually answered, if known. **Gap closer** — confirms a gap is resolved; name and role come from the user.
- Never use the account display name, e-mail or "the user" as a person. Missing identity ⇒ keep the item pending and ask.
Gap-specific rules (validation, conflicts, material gaps, carry-forward, deferral): `stages/gap-resolution/`.

## Approvals
Rule 4 of `../SKILL.md` applies: explicit words, exact version, `--statement`. Specifics: ask for the approver's name and role once; they are optional for acceptance criteria (store nothing if declined). After any edit the new version needs a new approval; the previously approved version stays in force and in history until then.

## Applying a detected change
`changes apply` needs a real person's name, role and own words, like an approval, but it approves nothing: it authorises the core to accept the new requirement text and mark artifacts under review after the user has seen what changed and its impact. It never approves changed criteria or the content of a changed requirement, never invalidates a confirmed decision (`gap reopen` is a separate human decision) and never removes an approval. A formatting-only difference needs no one's decision.

## Timestamps
Written by the core (IST, rule 2 of `../SKILL.md`). Response time and closure time are separate.
