---
description: Start or resume the Interactive BA workflow (BRD → atomic requirements → gaps → baseline → approved user stories)
argument-hint: "[BRD file path | existing BA project folder]"
---

Load the `ba-agent:ba-workflow` skill and follow it for the whole session. The user's input: `$ARGUMENTS`

The user is a Product Manager, not an engineer: ask as little as possible, never show commands, never make them copy or export anything.

1. **Find things yourself first.** Run `find` on the input if it is a folder, else on the folder attached to the session or the current folder.
   - A project exists → resume per `references/stages/resume-workflow.md`: say where things stand in 3 lines, then continue from `NEXT:`.
   - The input is a file, or `find` returns one clear BRD → a new project. Several candidates → one `AskUserQuestion` (best match first).
   - Nothing found and no input → one sentence ("I'll turn your BRD into traceable requirements, ask you about the gaps, and keep only the stories you approve") and ask them to attach the folder with the BRD or give its path.
2. **New project:** `references/stages/brd-ingestion.md` — one folder confirmation (propose the BRD's own folder; never write anywhere unconfirmed), `init`, `convert` for a PDF/DOC/RTF/XLSX. Report extraction warnings and instruction-like lines in one sentence each.
3. **Without waiting for the user:** decompose into requirements (`stages/atomic-requirements.md`) and find gaps (`stages/gap-analysis.md`). Tell them in two lines how many were saved and that open gaps are in `gaps/open`, then ask for the decision owner.
4. **Run the rest** stage by stage as the router describes, stopping only at real decisions: a gap answer, the baseline, each story.
