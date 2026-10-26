# Save checkpoint (after every stage)

Files in the project folder are the deliverable; the user reviews **files**, and nothing needs to be copied or exported. Every artifact folder keeps readable copies in `md/` and machine records in `json/`. The enriched copies of the requirement files are in `enriched/`. The project root has `START-HERE.md` (what is where, counts, who the decision owner is) and `ADR-LOG.md` (who decided or approved what, and when, in IST). The core rebuilds both after every change - never write them yourself.

| What happens | It is saved to |
|---|---|
| `init` | `index/md/requirements-index.md`, `snapshots/source/`, `enriched/<same path as the source>` |
| `gaps add` / `gaps commit` | `gaps/open/md/GAP-nnn.md`; shown under its requirement in `enriched/` |
| `gap resolve` | moved to `gaps/resolved/md/GAP-nnn.md`, decision in `decisions/md/DEC-nnn.md` |
| `gap defer` / `gap revisit` | `gaps/deferred/md/` / back to `gaps/open/md/` (carried-forward, answered-later and reopened gaps stay in `gaps/open`) |
| `ac add` | `acceptance/candidates/md/` |
| `ac approve` | `acceptance/approved/md/` and the criteria block in `enriched/` |
| `ac request-changes` / `ac defer` | `acceptance/unapproved/md/` / `acceptance/deferred/md/` |

Each time:
1. Run the stage command. Its `files_saved` lists only the folders that command wrote (`folder`, `md_folder`, `md_files`, `json_files`; `enriched` carries the copies' folder) from a fresh scan of the disk, so it is the proof - do not list the folder again with your own tools. A `gap` command also returns `gap_file` (`folder` and `file`): where that gap lives now. (`ba.py files` shows the whole project on demand.)
2. Tell the user in **one line** and without waiting: e.g. "9 requirements indexed, copies in `enriched/`", "GAP-003 answered and moved to `gaps/resolved/`". Between gaps and between records say nothing about files; mention the folders once when a stage ends (requirements and gaps found; session or queue finished). Name the full path only where the user has to open a file to review or approve (a criteria record, an enriched copy).
3. If `files_saved` is absent for a command that should have written, the folder is empty, or it is not the folder the user is looking at, say so plainly and stop: do not claim anything was saved, do not rebuild files by hand, do not move or copy artifacts (use the core, `recover` if `validate` reports a missing copy, or `doc sync` for an enriched copy), and give the user the project folder's full path. Only then list the folder with your own tools to see what is there. If `enriched_copy_warnings` is returned, relay it: a copy that was edited by hand waits for `changes check`.
