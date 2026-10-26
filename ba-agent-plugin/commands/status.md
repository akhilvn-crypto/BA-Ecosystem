---
description: Show the BA workflow's current stage, progress, outstanding gaps, pending criteria reviews and output location
argument-hint: "[BA project folder]"
---

Show the status of a BA project. Input: `$ARGUMENTS`

1. Load the `ba-agent:ba-workflow` skill for the command syntax.
2. Find the project folder: use the input if given; otherwise look (inside the current working directory only) for `state/workflow.json` or `ba-output/*/state/workflow.json`. If none is found, ask the user for the folder. Do not search elsewhere.
3. Run the core `status` command and show its output as-is (it is already formatted for people), then add one line translating `NEXT:` into plain language. A project from ba-agent 1.x answers `LEGACY_PROJECT`: say it needs ba-agent 1.10.2 or a new project on the requirements folder.
4. If validation errors are listed, run `validate` and explain them; offer `recover` if it mentions an interrupted closure or approval. Never hide warnings. If the `Changes:` line shows a change awaiting a decision, one not yet checked, or open actions, or `Enriched copies` is not in step, say so in one line and offer `/ba-agent:changes` (status itself records and changes nothing).
5. Do not change anything. Offer the matching next step: `/ba-agent:triage-gaps` when OPEN or DEFERRED gaps remain, `/ba-agent:triage-ac` when acceptance criteria are unapproved, `/ba-agent:review` to simply continue (or `/ba-agent:start`).
