# Quality and traceability

**Deterministic (cheap — always run these first, no extra model call):**
- `validate` — schemas, key uniqueness, source file and snapshot integrity, enriched-copy sync (hand edits, missing or stale copies), closure metadata ↔ decision ↔ audit-event consistency, IST timestamps, criteria folder rules, approvals have audit events, interrupted work. Errors must be fixed or reported; warnings must be shown to the user.
- `trace --write` — requirement → gaps → criteria coverage with `file:line`, requirements without criteria, orphan criteria, dangling references → `reports/traceability-report.md`.
- `recover` — rolls interrupted gap closures/approvals forward idempotently, reopens a gap whose closure audit trail is incomplete, and re-renders stale enriched copies (`doc sync` does only the copies; `doc check` reports them read-only). A copy edited by hand is left alone until `changes check` has recorded the edit.

**Semantic (one independent pass, no duplicate reviewers):** unsupported assumptions in criteria, criteria that are not observable, wording that drifts from the requirement/decision or contradicts what the document already states, missing negative paths. Use the optional `ba-agent:quality-reviewer` agent for an isolated review of a batch; fix via `ac edit`, never by editing files.

**Requirements or approved criteria changed?** Follow `change-detection` (`changes check` → show impact → `changes apply` on the user's decision):
- Formatting-only (markup, whitespace, re-wrapping) → recorded, no review. Material → per-requirement diff with the old and new lines; criteria and gaps of changed or removed requirements are flagged only once applied; the accepted fingerprint moves only then.
- Reassess only the affected items: read the new text (`req show`), re-run gap analysis for changed and new requirements, then per record `ac edit` or `ac confirm-unaffected`, and `ac archive` for removed requirements. Previous decisions, versions and approvals are preserved.
- `trace --write` includes a *Change impact* section (unreviewed, open, stale artifacts); `validate` warns about unreviewed changes, drifted approved criteria, hand-edited enriched copies and damaged snapshots.

Before saying "complete": `complete` must return `complete: true`; otherwise report PARTIAL with its reasons.
