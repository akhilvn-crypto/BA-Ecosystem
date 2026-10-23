---
name: quality-traceability
description: Run deterministic and semantic quality checks - schema validation, source references, audit consistency, requirement-to-story traceability, orphan and duplicate detection, plus BRD change impact assessment. Use before presenting stories, before declaring completion, when status shows validation errors, or when the BRD file changed.
---

# Quality and traceability

**Deterministic (cheap — always run these first, no extra model call):**
- `validate` — schemas, ID uniqueness, source-section references, closure metadata ↔ decision ↔ audit-event consistency, IST timestamps, story folder rules, approvals have audit events, interrupted work. Errors must be fixed or reported; warnings must be shown to the user.
- `trace --write` — requirement→story coverage, uncovered requirements, orphan stories, dangling references, duplicate stories → `reports/traceability-report.md`.
- `recover` — rolls interrupted gap closures/approvals forward idempotently; reopens a gap whose closure audit trail is incomplete.

**Semantic (one independent pass, no duplicate reviewers):** unsupported assumptions in stories, acceptance criteria that are not observable, wording that drifts from the requirement/decision, missing negative paths. Use the optional `ba-agent:quality-reviewer` agent for an isolated review of a batch; fix via `story edit`, never by editing files.

**BRD changed?** `source check`:
- Cosmetic (text identical) → fingerprint refreshed, no impact.
- Content change → section-level diff by title path; requirements in changed/removed sections become `NEEDS_REVIEW`; stories, gaps and the baseline are flagged; the recorded fingerprint is **not** replaced yet.
- Reassess only the affected items: read the changed sections (`source section SID --allow-changed`), then per requirement `req update … --cause source-change`, `req confirm` (note why unaffected; `source_excerpt_still_present` is a hint, not proof) or `req remove`. Add requirements for new sections. Then ask the human to accept: `source accept --by-name … --by-role … --statement …`. Re-run `gaps commit --cross-pass-done`, `baseline create`/`confirm`, and handle flagged stories. Previous decisions and versions are preserved.

Before saying "complete": `complete` must return `complete: true`; otherwise report PARTIAL with its reasons.
