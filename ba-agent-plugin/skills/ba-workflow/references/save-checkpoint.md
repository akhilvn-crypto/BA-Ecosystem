# Save checkpoint (after every stage)

Files in the project folder are the deliverable; the user reviews **files**, and nothing needs to be copied or exported. Every artifact folder keeps readable copies in `md/` and machine records in `json/`. The project root has `START-HERE.md` (what is where, counts, who the decision owner is) and `ADR-LOG.md` (who decided or approved what, and when, in IST). The core rebuilds both after every change - never write them yourself.

| What happens | It is saved to |
|---|---|
| `req add` / `req commit` | `atomic-requirements/md/REQ-nnn.md` (each requirement is written as it is added) |
| `gaps add` / `gaps commit` | `gaps/open/md/GAP-nnn.md` |
| `gap resolve` | moved to `gaps/resolved/md/GAP-nnn.md`, decision in `decisions/md/DEC-nnn.md` |
| `gap defer` / `gap revisit` | `gaps/deferred/md/` / back to `gaps/open/md/` (carried-forward, answered-later and reopened gaps stay in `gaps/open`) |
| `baseline create` / `baseline confirm` | `baselines/md/baseline-vN.md` |
| `story add` | `stories/candidates/md/US-nnn.md` |
| `story approve` | `stories/approved/md/US-nnn.md` |
| `story request-changes` / `story defer` | `stories/unapproved/md/` / `stories/deferred/md/` |

Each time:
1. Run the stage command. Its `files_saved` lists only the folders that command wrote (`folder`, `md_folder`, `md_files`, `json_files`) from a fresh scan of the disk, so it is the proof - do not list the folder again with your own tools. A `gap` command also returns `gap_file` (`folder` and `file`): where that gap lives now. (`ba.py files` shows the whole project on demand.)
2. Tell the user in **one line** and without waiting: e.g. "12 requirements saved in `atomic-requirements/`", "GAP-003 answered and moved to `gaps/resolved/`". Between gaps and between stories say nothing about files; mention the folders once when a stage ends (requirements and gaps found; session or queue finished). Name the full path only where the user has to open a file to review or approve (a baseline, a story).
3. If `files_saved` is absent for a command that should have written, the folder is empty, or it is not the folder the user is looking at, say so plainly and stop: do not claim anything was saved, do not rebuild files by hand, do not move or copy artifacts (use the core, or `recover` if `validate` reports a missing copy), and give the user the project folder's full path. Only then list the folder with your own tools to see what is there.
