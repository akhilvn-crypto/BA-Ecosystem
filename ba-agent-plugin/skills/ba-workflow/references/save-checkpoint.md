# Save checkpoint (after every stage)

Files in the project folder are the deliverable. The user reviews and approves **files**, so each stage must leave them where the user can open them, without being asked.

Every artifact folder keeps readable copies in `md/` and machine records in `json/`:

| Stage ends with | Look in |
|---|---|
| `req commit` | `atomic-requirements/md/REQ-nnn.md` · `atomic-requirements/json/` |
| `gaps commit`, `gap resolve` | `gaps/md/GAP-nnn.md` · `decisions/md/DEC-nnn.md` |
| `baseline create` / `baseline confirm` | `baselines/md/baseline-vN.md` |
| `story add` | `stories/candidates/md/US-nnn.md` |
| `story approve` | `stories/approved/md/US-nnn.md` |
| `story request-changes` / `story defer` | `stories/unapproved/md/` / `stories/deferred/md/` |

Do this each time, in order:

1. Run the stage command. Read `files_saved` in its result (per folder: `md_folder`, `md_files`, `json_folder`, `json_files`). `ba.py files` shows the same for the whole project at any time.
2. Confirm with your **own file tools** (list the `md/` folder) that the new files exist. The command output alone is not proof for the folder the user is looking at.
3. Tell the user the full path of the folder (or the one file under review) and how many files it holds. Do this **before** asking them to review, confirm or approve.
4. If the files are missing, empty, or in a different folder than the user is looking at, say so plainly and stop. Do not claim they were saved, do not rebuild them by hand, and do not wait to be asked to "copy" them. If the project folder is not the folder the user is looking at, give them its full path, or ask whether to start a new project in the folder they meant. Never move or copy artifacts by hand: use the core.
