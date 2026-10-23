# Changelog

## 1.1.0
- Atomic requirements are now saved in a clearly named `atomic-requirements/` folder (was `requirements/`). Existing projects are migrated automatically the next time they are opened. `req commit` now returns `saved_to` and `files_on_disk`, and the skill tells the user the folder path and verifies it exists.
- Default project folder is the folder that contains the BRD (the attached folder in Cowork) instead of a nested `ba-output/<name>`.
- Gap resolution presents one gap at a time, as an `AskUserQuestion` prompt with a text box (plain-chat fallback). `gaps next` now defaults to one gap.
- Stories marked *not approved* are now saved in their own `stories/unapproved/` folder (previously they stayed in `candidates/` with a status flag). Old projects are migrated on open; `story next --unapproved` revisits them; `status` and `validate` count them.
- Choice dialogs: the full gap/story is shown as a normal chat message and the `AskUserQuestion` dialog carries only a short plain-text question with described options (new `references/choice-prompts.md`: limits, result handling, fallback).
- Story review works the same way: one story per `AskUserQuestion` prompt with a text box. The user types *approve*, their suggestions (→ new story version, re-presented), or *not approved* (→ `story request-changes`, kept out of `approved/`).

## 1.0.0 — 2026-10-22
Initial release implementing `ba_agent_plugin_implementation_v2.md`.

- Plugin: manifest, 3 commands (`start`, `status`, `review`), 10 skills, 5 optional agents.
- Deterministic stdlib-only core (`scripts/ba.py`): stage/gap/story state machines, schemas, stable IDs, source fingerprints and section outline (md/txt/docx), verbatim-quote verification, safe/atomic writes with path containment, append-only IST audit log.
- Decision-owner preflight; closure record (closer, role, owner, respondent, decision link, `closed_at_ist` `+05:30`, validation) written through a recoverable journal; `recover` rolls interrupted closures/approvals forward.
- Answer conflict detection (advisory pre-check + mandatory explicit review), confirmed-change flow with requirement versioning, decision supersession and impact flags.
- Versioned baselines with strict blocking policy; stories only from an approved baseline; approve/edit/defer/revisit with exact-version approvals.
- Source-change detection with section-level impact assessment.
- 80+ automated tests (unit, workflow, crash-recovery, safety, CLI end-to-end).
