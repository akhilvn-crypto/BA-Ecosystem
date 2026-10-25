# Quality and traceability

**Deterministic (cheap — always run these first, no extra model call):**
- `validate` — schemas, ID uniqueness, source-section references, closure metadata ↔ decision ↔ audit-event consistency, IST timestamps, story folder rules, approvals have audit events, interrupted work. Errors must be fixed or reported; warnings must be shown to the user.
- `trace --write` — requirement→story coverage, uncovered requirements, orphan stories, dangling references, duplicate stories → `reports/traceability-report.md`.
- `recover` — rolls interrupted gap closures/approvals forward idempotently; reopens a gap whose closure audit trail is incomplete.

**Semantic (one independent pass, no duplicate reviewers):** unsupported assumptions in stories, acceptance criteria that are not observable, wording that drifts from the requirement/decision, missing negative paths. Use the optional `ba-agent:quality-reviewer` agent for an isolated review of a batch; fix via `story edit`, never by editing files.

**BRD or an approved story changed?** Follow `change-detection` (`changes check` → show impact → `changes apply` on the user's decision):
- Formatting-only (markup, whitespace, re-wrapping) → recorded, no review. Material → section-level diff by title path with the old and new lines; requirements in changed/removed sections become `NEEDS_REVIEW` only once applied; stories, gaps and the baseline are flagged; the recorded fingerprint is **not** replaced until `source accept`.
- Reassess only the affected items: read the changed sections (`source section SID --allow-changed`), then per requirement `req update … --cause source-change`, `req confirm` (note why unaffected; `quoted_text_still_present` is a hint, not proof) or `req remove`. Add requirements for new sections. Then ask the human to accept: `source accept --by-name … --by-role … --statement …`. Re-run `gaps commit --cross-pass-done`, `baseline create`/`confirm`, and handle flagged stories. Previous decisions, versions and approvals are preserved.
- `trace --write` includes a *Change impact* section (unreviewed, open, stale artifacts); `validate` warns about unreviewed changes, drifted approved stories and damaged snapshots.

Before saying "complete": `complete` must return `complete: true`; otherwise report PARTIAL with its reasons.
