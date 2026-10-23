# Changelog

## 1.0.0 — 2026-10-22
Initial release implementing `ba_agent_plugin_implementation_v2.md`.

- Plugin: manifest, 3 commands (`start`, `status`, `review`), 10 skills, 5 optional agents.
- Deterministic stdlib-only core (`scripts/ba.py`): stage/gap/story state machines, schemas, stable IDs, source fingerprints and section outline (md/txt/docx), verbatim-quote verification, safe/atomic writes with path containment, append-only IST audit log.
- Decision-owner preflight; closure record (closer, role, owner, respondent, decision link, `closed_at_ist` `+05:30`, validation) written through a recoverable journal; `recover` rolls interrupted closures/approvals forward.
- Answer conflict detection (advisory pre-check + mandatory explicit review), confirmed-change flow with requirement versioning, decision supersession and impact flags.
- Versioned baselines with strict blocking policy; stories only from an approved baseline; approve/edit/defer/revisit with exact-version approvals.
- Source-change detection with section-level impact assessment.
- 80+ automated tests (unit, workflow, crash-recovery, safety, CLI end-to-end).
