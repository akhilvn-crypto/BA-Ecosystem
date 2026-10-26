---
description: Start or resume the Interactive BA workflow (requirements folder → gaps → human triage → enriched copy with acceptance criteria)
argument-hint: "[requirements folder or .md file | existing BA project folder]"
---

Load the `ba-agent:ba-workflow` skill and follow it for the whole session. The user's input: `$ARGUMENTS`

The user is a Product Manager, not an engineer: ask as little as possible, never show commands, never make them copy or export anything.

1. **Where are the requirements?** Find out yourself first: run `find` on the input if it is a folder, else on the folder attached to the session or the current folder.
   - A project exists → resume per `references/stages/resume-workflow.md`: say where things stand in 3 lines, then continue from `NEXT:`.
   - The input is a folder or an `.md` file, or `find` returns one clear candidate → a new project. Several candidates → one `AskUserQuestion` (best match first).
   - Nothing found and no input → one sentence ("I'll look for gaps in your requirements, ask you about them, and add approved acceptance criteria to a copy; your files are never changed") and ask: **where are the requirements, a folder or a single .md file?**
2. **New project:** `references/stages/requirements-intake.md` — one confirmation of the project folder (propose `<parent of the requirements>/ba-output/<name>`; never inside the requirements folder) and, only if `init` warns that no IDs were found, the ID pattern. Then `init`. Report warnings and instruction-like lines in one sentence each.
3. **Without waiting for the user:** find gaps (`stages/gap-analysis.md`). Tell them in two lines how many requirements and gaps were found and that open gaps are in `gaps/open`, then ask for the decision owner.
4. **Run the rest** stage by stage as the router describes, stopping only at real decisions: a gap answer, each set of acceptance criteria.
