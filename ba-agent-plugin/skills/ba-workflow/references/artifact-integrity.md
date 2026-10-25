# Artifact integrity (shared reference)

**Layout** (all under the confirmed project folder): every artifact folder holds two subfolders, `md/` (readable copies) and `json/` (machine records), and no loose files: `extracted/` (outline + source metadata) · `atomic-requirements/` (`md/REQ-*.md`, `json/REQ-*.json`) · `gaps/{open,deferred,resolved}/` (each with `md/GAP-nnn.md` and `json/GAP-nnn.json`; a gap moves folders as its status changes; the register is `state/gap-register.json`) · `decisions/` (`json/decision-log.jsonl`, `md/DEC-*.md`) · `baselines/` (`md/baseline-vN.md`, `json/baseline-vN.json` + `baseline-manifest.json`) · `changes/` (`md/CHG-nnn.md`, `json/CHG-nnn.json`) · `stories/{candidates,approved,unapproved,deferred,archive}/` (each with `md/US-nnn.md` and `json/US-nnn.json`) · `reports/` · `START-HERE.md` and `ADR-LOG.md` at the top (generated views, never edited) · `state/workflow.json`, `state/gap-register.json`, `state/audit.jsonl`, `state/pending/`, `state/backups/`, `state/inbox/`.

**Rules**
1. The JSON records are authoritative; the `.md` files are rendered views. Never hand-edit anything under `state/`, `atomic-requirements/`, `gaps/`, `decisions/`, `baselines/` or `stories/`. Change them only through the core so schemas, transitions and audit events stay consistent.
2. `state/audit.jsonl` and `decisions/decision-log.jsonl` are append-only.
3. Writes are atomic (temp file + replace); previous versions of overwritten derived files are kept in `state/backups/`. `safe_write.py` is the only way to create helper files, and only under `state/inbox/` or `reports/`.
4. Requirements, stories and baselines are **versioned**; older versions stay in history (`versions[]`, `stories/archive/`, earlier `baseline-vN`). Nothing is deleted.
5. An interrupted gap closure leaves a journal in `state/pending/`. `validate` flags it; `recover` rolls it forward idempotently. Run `recover` before continuing if `status` says so.
6. Run `validate` (and `trace`) before presenting candidate stories and before reporting completion. Report failures; do not paper over them.
7. If a write fails (permissions, disk), report it, keep the item pending, and do not tell the user it was saved.
