# Artifact integrity (shared reference)

**Layout** (all under the confirmed project folder): `extracted/` (outline + source metadata) · `requirements/REQ-*.json|md` · `gaps/gap-register.json` + `GAP-*.md` · `decisions/decision-log.jsonl` + `DEC-*.md` · `baselines/baseline-vN.json|md` + `baseline-manifest.json` · `stories/{candidates,approved,deferred,archive}/` · `reports/` · `state/workflow.json`, `state/audit.jsonl`, `state/pending/`, `state/backups/`, `state/inbox/`.

**Rules**
1. The JSON records are authoritative; the `.md` files are rendered views. Never hand-edit anything under `state/`, `requirements/`, `gaps/`, `decisions/`, `baselines/` or `stories/`. Change them only through the core so schemas, transitions and audit events stay consistent.
2. `state/audit.jsonl` and `decisions/decision-log.jsonl` are append-only.
3. Writes are atomic (temp file + replace); previous versions of overwritten derived files are kept in `state/backups/`. `safe_write.py` is the only way to create helper files, and only under `state/inbox/` or `reports/`.
4. Requirements, stories and baselines are **versioned**; older versions stay in history (`versions[]`, `stories/archive/`, earlier `baseline-vN`). Nothing is deleted.
5. An interrupted gap closure leaves a journal in `state/pending/`. `validate` flags it; `recover` rolls it forward idempotently. Run `recover` before continuing if `status` says so.
6. Run `validate` (and `trace`) before presenting candidate stories and before reporting completion. Report failures; do not paper over them.
7. If a write fails (permissions, disk), report it, keep the item pending, and do not tell the user it was saved.
