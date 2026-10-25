# Save checkpoint (after every stage)

Files in the project folder are the deliverable; the user reviews **files**. Every artifact folder keeps readable copies in `md/` and machine records in `json/`.

| Stage ends with | Look in |
|---|---|
| `req commit` | `atomic-requirements/md/REQ-nnn.md` |
| `gaps commit`, `gap resolve` | `gaps/md/GAP-nnn.md` · `decisions/md/DEC-nnn.md` |
| `baseline create` / `baseline confirm` | `baselines/md/baseline-vN.md` |
| `story add` | `stories/candidates/md/US-nnn.md` |
| `story approve` | `stories/approved/md/US-nnn.md` |
| `story request-changes` / `story defer` | `stories/unapproved/md/` / `stories/deferred/md/` |

Each time, in order:
1. Run the stage command. Its `files_saved` lists **only the folders that command wrote**, each with `md_folder` and file counts. It is a fresh scan of the disk taken after the write, so it is the proof — do not list the folder again with your own tools just to repeat it. (`ba.py files` shows the whole project on demand.)
2. Tell the user the full `md_folder` path (or the one file under review) and how many files it holds, in one line, **before** asking them to review, confirm or approve.
3. If `files_saved` is absent for a command that should have written, the folder is empty, or it is not the folder the user is looking at, say so plainly and stop: do not claim anything was saved, do not rebuild files by hand, do not move or copy artifacts (use the core), and give the user the project folder's full path. Only then list the folder with your own tools to see what is there.
