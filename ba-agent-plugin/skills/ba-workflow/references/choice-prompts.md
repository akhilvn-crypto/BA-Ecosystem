# Choice prompts (`AskUserQuestion`)

## Gaps and stories: the core builds the prompt — relay it
`gaps next` and `story next` return `present`: `dialog` (a ready `AskUserQuestion` payload) and, when needed, `card` (compact markdown). Call `AskUserQuestion` directly and pass `dialog` unchanged. Never rewrite its options, add a “Recommended” mark, or pre-select. The numbered `fallback` is not sent by default: if the dialog tool fails, re-run the same command with `--fallback`.
- **Gap, `inline: true`:** the dialog alone — no chat message and no card. It already holds priority, question, why, source and affected requirements. Without the tool, re-run with `--fallback` and send `present.fallback` (it contains the question).
- **Gap, `inline: false`:** `card` once, then the dialog. The card carries history the dialog cannot (earlier answers, conflicts, deferral).
- **Story:** `card` once (the story and its acceptance criteria must be readable), then the dialog.
- **Queue lists** (`gaps triage`, `story triage`): post the one-line `summary`, not a table.

## Prompts you build yourself (keep them this small)
One question per call (the tool accepts 1–4, but each gap, story and decision is audited on its own); the tool adds the free-text “Other” box itself. Put long detail in chat first, then keep the dialog to one or two plain sentences.
| Moment | `header` | Options (label — description) |
|---|---|---|
| Output folder | `FOLDER` | `Use this folder` — path in the description; another path goes in “Other” |
| Owner unknown | `OWNER` | `Pause` · `Analysis only` |
| Baseline | `BASELINE` | `Confirm vN` · `Not now` |
| MEDIUM/LOW round | `MEDIUM/LOW` | `Go through each` · `Carry all forward` |
| Detected change | `CHG-nnn` | `Apply impact` — *marks the affected requirements, gaps, stories and baseline for review; nothing is approved or discarded* · `Review later` — *changes nothing; raised again next time*. Deleted approved story: `Restore story` · `Confirm removal` · `Review later` |
| Conflict | `GAP-00x` | `Clarify` · `Confirm a change` · `Defer This Gap` (or `Carry Forward` when allowed) |
Name and role are collected in chat after a yes, never invented.

## Hard limits of the tool
- One question per call, by our choice (see above). `header` ≤ 12 characters. 2–4 `options`, each with a short `label` and a one-line `description`. No “Other” option of your own. `multiSelect: false`.

## Reading the result
- The picked option or the typed “Other” text is the human's answer; quote their own words when recording it.
- Empty, dismissed or unrelated → **no answer**: say so in one line and ask again in plain chat. Never infer approval, closure or a decision from it.
- Mixed intent (“approve but change X”) → treat as suggestions and confirm.

## Without the tool
Call `AskUserQuestion` directly; use ToolSearch (`select:AskUserQuestion`) only if it is not in the tool list. If it is not callable or the call fails, do **not** retry in a loop: re-run the `next` command with `--fallback`, send `card` plus `present.fallback` (or the choices as a short numbered list) and wait for the reply. Names, roles, paths and dates are free text — ask in chat.
