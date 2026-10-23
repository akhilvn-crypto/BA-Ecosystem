---
description: Show the BA workflow's current stage, progress, outstanding gaps, pending story reviews and output location
argument-hint: "[BA project folder]"
---

Show the status of a BA project. Input: `$ARGUMENTS`

1. Load the `ba-agent:ba-workflow` skill for the command syntax.
2. Find the project folder: use the input if given; otherwise look (inside the current working directory only) for `state/workflow.json` or `ba-output/*/state/workflow.json`. If none is found, ask the user for the folder. Do not search elsewhere.
3. Run the core `status` command and show its output as-is (it is already formatted for people), then add one line translating `NEXT:` into plain language.
4. If validation errors are listed, run `validate` and explain them; offer `recover` if it mentions an interrupted closure or approval. Never hide warnings.
5. Do not change anything. Offer `/ba-agent:review` (or `/ba-agent:start`) to continue.
