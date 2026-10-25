# Cowork sync (file bridge)

Use this only when the tools to stage files in from the user's computer and commit files back are present (Cowork). On Claude Code with the local folder, skip it.

**Why:** the core runs in a temporary cloud workspace; the user's BRD and project folder are on their computer. A file the core wrote is not saved for the user until you commit it back, and the core cannot see the user's own edits unless fresh copies are staged.

## Session start
1. Stage into the workspace, at the same relative paths: the BRD, and for an existing project only what a session needs. Run `manifest --stage-list` on the user's folder (or, if the core cannot see it yet, take `state/` without `backups/` and `inbox/`, `snapshots/`, `source/`, `extracted/`, every `json/` folder and `stories/approved/md/`) and stage exactly its `stage` paths. Do not stage `md/` views, backups or `reports/`: the core rebuilds what it writes. With `bridge on`, `validate` does not report missing `md/` views; do not run `recover` to rebuild them, because the user already has those copies and they would be committed back for nothing. Always take fresh copies of `stories/approved/md/` and the BRD, and of any file the user says they edited.
2. `bridge on` once (it records the switch in `state/bridge.json`; for a new project that is right after `init --changed-files`, see `stages/brd-ingestion.md`), then `status`, then `manifest`. `in_sync: false` lists `drift` (`edited`, `added`, `missing`): files that differ from the last committed stage. Tell the user in one line; a hand-edited BRD or approved story is explained by `changes check`, which you run next (it changes nothing). A stale or missing copy is a staging problem: re-stage it, do not "repair" it.

## During the session
- With `bridge on` every command reports (otherwise add `--changed-files`). The result then lists `changed_files` (written or modified) and `removed_files` (for example a gap moving from `gaps/open` to `gaps/resolved` is one removed and one new path). `state/backups/` and `state/inbox/` are local only and never listed: do not commit them.
- After each command that records a decision or completes a stage (`req commit`, `gaps commit`, `gap resolve|defer|carry-forward`, `baseline confirm`, `story approve|defer|request-changes`, `changes apply`), commit back exactly its `changed_files` and delete its `removed_files` on the user's side. Presenting commands (`gaps next`, `story next`) only update status: commit their files with the next batch.
- Name a path to the user as saved only after the commit succeeded. If a commit fails, say so, keep the item pending and do not claim a save.

## Stage end (and before the session closes)
1. `manifest --write`, then commit its `state/manifest.json` together with any remaining `changed_files`.
2. Send `START-HERE.md` to the user (SendUserFile): it shows what is where. Mention folders once, not per file.
