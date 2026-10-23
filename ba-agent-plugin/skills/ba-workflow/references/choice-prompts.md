# Choice prompts (`AskUserQuestion`) — how to show them so they render correctly

Use this whenever the human must pick or type something (in Claude Code and in Claude Cowork alike; where the tool is unavailable, the numbered-list fallback below carries the same choices): gap answers, story review, folder confirmation, owner unknown (pause / analysis-only), conflict (clarify / confirm change / defer), baseline confirmation, policy acceptance.

## The pattern: details in chat, a short question in the dialog
The dialog is a small panel that shows plain text. Long, multi-line or markdown-heavy questions (tables, bullets, `**bold**`, Given/When/Then blocks) get cramped, truncated or show raw markup. So:

1. **First send a normal chat message** with everything the human needs to read (the gap packet or the full story, evidence, IDs, source references) formatted as ordinary markdown.
2. **Then call `AskUserQuestion` in the same turn** with a *short, plain-text* question (one or two sentences, no markdown, no tables) that points back at the message: e.g. `GAP-003 (HIGH): what is the refund window in days?`
3. Do not repeat the long text inside the dialog — except the priority, which always leads the dialog question for a gap (`[HIGH priority] …`) so the human sees how urgent it is while answering.
4. If the human picks an `Explain …` option, it is a request for help, not an answer: explain in chat in plain language with a real-world example, then show the same dialog again.
5. Gap dialogs depend on the gap (use the packet's `options`; the tool's own “Other” box always carries a free-text answer or instruction):
   - **MEDIUM/LOW, non-material gaps** (`carry_forward_allowed: true`): `Suggested Answer` (only when the packet has an evidence-backed `suggested_answer`; put the suggestion and its source in the description), `Explain This Question`, `Carry Forward`. `Carry Forward` needs the name and role of the person choosing it (and an optional reason), so collect those in chat before running the command.
   - **CRITICAL/HIGH/material gaps** (`carry_forward_allowed: false`): `Explain This Question`, `Answer Later`, `Defer This Gap`. `Answer Later` needs no reason; `Defer This Gap` needs a reason (and a revisit date when asked for). Never offer `Carry Forward` for these.
   - **Triage of a DEFERRED gap**: `Explain This Question`, `Keep Deferred` (the packet's `options`); an answer is typed in “Other”. **Carried-forward gap**: `Explain This Question`, `Keep Carried Forward`.
   - **Triage of a story** (`/ba-agent:triage-stories`): pending review `Approve vN`, `Defer story`, `Not approved`; not approved earlier `Approve vN`, `Defer story`, `Keep as is`; deferred `Approve vN`, `Not approved`, `Keep deferred`; a story that cannot be approved yet gets no `Approve` option. Typed text is always edit suggestions.
   - **A detected change** (`/ba-agent:changes`): `Apply impact` (*“Marks the affected requirements, gaps, stories and baseline for review; nothing is approved or discarded”*), `Review later` (*“Changes nothing; the change is raised again next time”*). For a deleted approved story: `Restore story`, `Confirm removal`, `Review later`. Name and role are collected in chat after a yes.
   A suggested answer is also stated in the chat message with its source; it is an answer only when the human picks it.

## Hard limits of the tool
- 1–4 questions per call. **Use exactly one question per call** here (one gap, one story, one decision).
- `header`: at most 12 characters (an ID such as `GAP-003` or `US-003` fits; a sentence does not).
- `options`: **2–4** items. Do not add an “Other” option — the tool adds a free-text “Other” box itself; that box is where the human types answers and suggestions.
- Every option needs a short `label` (1–5 words) **and** a `description` (one line saying what happens if it is picked).
- `multiSelect: false` for everything in this workflow.
- Never put “(Recommended)” on an option for a business answer, approval or closure; a suggestion from the BRD is labelled as a suggestion in its `description`. Never pre-select.

## Reading the result
- The human's picked option or their typed “Other” text is their answer. Quote their own words when recording it.
- If the dialog comes back empty, was dismissed, or the human typed something unrelated, treat it as **no answer**: say so in one line and ask again in plain chat. Never infer approval, closure or a decision from a dismissed dialog.
- If typed text mixes intents (e.g. “approve but change X”), do not guess: treat it as suggestions and confirm.

## When the tool cannot be used
If `AskUserQuestion` is not callable, try loading it once with ToolSearch `select:AskUserQuestion`. If the call fails (schema error, unavailable in this surface, or the dialog never appears), do **not** retry in a loop: fall back to plain chat — the same message, followed by the choices as a short numbered list, and wait for the reply.

## Free-text-only questions
Names, roles, folder paths and the decision owner are free text. Ask them in chat (or use a dialog whose first option is the proposed value, e.g. `Use this folder`, with the path in its description, and let the human type another path in “Other”).
