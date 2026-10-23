---
name: gap-resolution
description: Interactively resolve gaps with the human - identify the decision owner first, present ONE gap at a time (highest priority first); before the first MEDIUM/LOW gap, ask once whether to go through each or carry them all forward. CRITICAL/HIGH/material gaps get Other, Explain This Question, Answer Later and Defer This Gap; MEDIUM/LOW gaps get Suggested Answer (only with evidence), Other, Explain This Question and Carry Forward (the gap stays OPEN, stories are still generated and flagged). Validate every answer against the BRD, requirements, decisions and business rules, and close a gap only with a validated, conflict-free answer and an IST-stamped audit record. Revisit carried-forward gaps and update the linked stories. Use when gaps are waiting for input.
---

# Gap resolution (human in the loop)

Read `../ba-workflow/references/human-approval.md` first — it holds the identity, material-gap and conflict rules you must follow. Read `../ba-workflow/references/choice-prompts.md` before showing any choice dialog.

## 0. Decision-owner preflight (once, before the first gap)
Ask: *"Who will make/confirm the business decisions for these gaps? Please provide their name and role (for example, Priya Nair — Product Owner)."* Allow "that's me" (still get name and role). Then `owner set --name … --role …`.
Owner unknown → `owner unknown --choice pause|analysis-only`, explain that triage cannot start, and ask which they prefer (`AskUserQuestion`: `Pause`, `Analysis only`). If a gap needs a different owner → `owner set … --gap GAP-00x`. Owner changes are audited; prior decisions keep their original owner.

## 1. Present ONE gap at a time — highest priority first
`gaps next --limit 1` returns the single gap to work on, ordered CRITICAL → HIGH → MEDIUM → LOW. Never list several gaps in one message. Say where they are ("Gap 2 of 7"). The result also lists `high_priority_outstanding`, `deferred_due_for_revisit` and `carried_forward_open`: if deferred gaps are due, mention them once before the next gap and offer `gap revisit`.

### The MEDIUM/LOW choice — ask it once, before the first such gap
When `gaps next` returns `bulk_carry_forward.offer_now: true` (the gap about to be shown is a carry-forwardable MEDIUM/LOW gap and more than one is waiting) and you have **not yet asked this choice in this round**, do not show the gap yet. Ask once instead:
1. **Chat message:** *"The CRITICAL/HIGH and sensitive gaps are done. N non-blocking MEDIUM/LOW gaps are left (list ID · priority · one-line title from `eligible_gap_ids`). Do you want to go through each of them, or carry all of them forward? Carrying forward keeps every one of them OPEN and visible; stories are still written and flagged until you answer."*
2. **`AskUserQuestion`** — `header: "MEDIUM/LOW"`, `question`: *"Go through the N MEDIUM/LOW gaps one by one, or carry them all forward?"*, exactly two options, no “(Recommended)”:
   - `Go through each` — *"Show me each MEDIUM/LOW gap one at a time"*.
   - `Carry all forward` — *"Keep all N open, continue to the stories, and flag them until answered"*.
3. **`Go through each`** → continue with the single-gap flow below and do not ask again this round. **`Carry all forward`** → collect, in one chat message, **who is choosing this** (name and role — never invented; the decision owner if they say so, still both) and an optional short reason, then run `gaps carry-forward-all --by-name … --by-role … [--reason "<their words>"]`. Every eligible gap is carried forward with its own record and audit event; CRITICAL/HIGH, material, deferred and answered-later gaps are **not** touched (`not_carried` lists them with the reason). Tell them plainly: *"N gaps stay OPEN, none is resolved. Stories for their requirements will be written, each naming its gap and its possible impact, and marked as needing clarification. We will not treat any assumption as a decision."* Then run `gaps next --limit 1` for whatever is left.
4. Typed text in “Other” is an instruction: "all", "carry them" → the second path; "one by one" → the first; anything else, ask one short question.
Fallback without `AskUserQuestion`: plain chat, *1 Go through each · 2 Carry all forward*. If `offer_now` is false (only one such gap, or the next gap is CRITICAL/HIGH/material), skip this choice. In triage mode (§4) do not ask it; the user is already reviewing gap by gap and may still carry gaps forward individually.

The packet tells you which dialog to show: `carry_forward_allowed` is true only for **MEDIUM/LOW gaps that are not material** (not security, compliance, scope or policy). Use `options` from the packet as-is.

Two steps, in the same turn:
1. **Chat message** (normal markdown) with the gap packet: `GAP-003 · category · priority (Gap 2 of 7)`, impacted requirement(s) + source reference, concise evidence, why it matters, whether it **blocks stories** (`blocks_stories`; always say so for CRITICAL/HIGH and for compliance-sensitive gaps), **the one question**, and — only if the packet has a `suggested_answer` — *"Suggestion from <source>: <answer> — this is only an answer if you choose it."* Never invent a suggestion; the packet has one only when it cites evidence. For a gap that can be carried forward, add one line on what that means: *"You can carry this forward: it stays open and visible, stories are still written and flagged until you answer."*
2. **`AskUserQuestion`** (load it with ToolSearch `select:AskUserQuestion` if it is not already callable). Short, plain text; the detail stays in the message above (see `choice-prompts.md`). One call = one gap:
   - `header`: the gap ID (e.g. `GAP-003`; max 12 characters).
   - `question`: start with the priority in capitals, then the question in one or two plain sentences, e.g. `[MEDIUM priority] What page-load time applies on mobile?` — ending with *"Type your answer or an instruction in the “Other” box, or pick an option."*
   - `options` (the tool adds the free-text “Other” box itself — that is how a typed answer is given):
     - **MEDIUM/LOW, non-material gap** (`carry_forward_allowed: true`) — up to these, in this order:
       - `Suggested Answer` — *only if the packet has `suggested_answer`*; description = the suggestion and its source in one line, e.g. *"Use: under 3 seconds on 4G (BRD 5, REQ-008)"*. It is the human's choice that makes it an answer.
       - `Explain This Question` — *"Explain it to me in plain words with an example"*.
       - `Carry Forward` — *"Keep this gap open, continue to the stories, and flag them until it is answered"*.
     - **CRITICAL, HIGH or material gap** (`carry_forward_allowed: false`) — exactly these three, never Carry Forward:
       - `Explain This Question`, `Answer Later` (*"Keep this gap open and ask me again later"*), `Defer This Gap` (*"Park this gap with a reason; it stays on the outstanding list"*).
   - Never pre-select, never mark anything “(Recommended)”.

### “Other” — free-text answer or instruction
Whatever the human types in “Other” is their own words. Decide which it is:
- **An answer** → `gap answer GAP-00x --response "<their words>"` (use `--args-file` for odd characters) and go to section 2. Every typed answer is validated against the BRD, related requirements, approved decisions and business rules before the gap can be resolved.
- **An instruction** ("explain", "carry it forward", "ask me later", "defer, waiting for legal", "use the suggestion") → follow the matching section below. If they ask for something the gap does not allow (carry forward a HIGH gap), say why in one sentence (`carry_forward_not_allowed_because`) and show the dialog again.
- **Mixed or unclear** → ask one short question in chat; do not guess, and never record an instruction as an answer.
A dismissed or empty dialog is no answer: say so in one line and ask again in plain chat.

### “Suggested Answer”
The human chose to adopt the evidence-backed suggestion. Run `gap answer GAP-00x --use-suggestion` (do not retype it). It is stored as the human's answer with `basis: SUGGESTED_ANSWER` and its source, and then goes through exactly the same validation as typed text (section 2) — it is not auto-resolved. Never offer or record a suggestion that has no cited source.

### “Explain This Question”
A request for help, **not an answer** — record nothing as an answer. Run `gap explain GAP-00x` (it audits the request and returns the facts) and, in your next message, explain the gap to a non-technical Product Manager, then show the same dialog again. Structure, in plain language:
1. **What we are asking** — the question in one simple sentence (`what_we_are_asking`).
2. **What information is missing** — what in the BRD is missing, vague or contradictory (cite the section: `missing_information`, `evidence`), and what developers or testers would have to guess if nobody decides.
3. **Why it matters** — `why_it_matters`, and the **impact on the requirement(s)** (`impact_on_requirements`: which requirement, which behaviour or test changes, and whether it blocks stories).
4. **A real-world example** — a short everyday scenario from the BRD's own domain where different answers lead to visibly different outcomes. Never abstract wording.
5. **What a good answer looks like** — a number, a yes/no, a role, a list of exceptions, with one sample clearly marked as illustration only.
6. If `if_carried_forward` is present, one sentence on what Carry Forward would mean here.
Under a minute to read. The human may ask for another example in “Other”.

### “Carry Forward” (MEDIUM/LOW, non-material gaps only)
The gap stays **OPEN — it is not resolved and no decision is recorded** — and stops blocking story generation for its requirements. Collect in one chat message: **who is choosing this** (name and role — never invented; the decision owner if they say so, still both) and, optionally, a short reason. Then
`gap carry-forward GAP-00x --by-name … --by-role … [--reason "<their words>"]`.
The core records the actor, the decision owner, the IST timestamp and the reason, and keeps the gap in the outstanding list, `status` and the baseline. It refuses CRITICAL/HIGH and material gaps (`CARRY_FORWARD_NOT_ALLOWED`): those keep the existing blocking rules and must be answered or deferred. Tell the human plainly: *"GAP-002 stays open. Stories for REQ-008 will be written, each naming this gap and its possible impact, and marked as needing clarification. We will not treat any assumption as a decision."* Then present the next gap. The carried-forward gap is not asked again in this round; it comes back through the revisit flow below.

### “Answer Later” (CRITICAL/HIGH/material gaps; or when typed in “Other”)
No reason is needed — do not ask for one. Run `gap answer-later GAP-00x`. The gap stays `OPEN`, stays in the outstanding queue (and in `status`), is asked again after the rest of this round, and — it is blocking — keeps blocking stories. Tell them that in one sentence ("GAP-003 stays open and still blocks the stories for REQ-003"). Then present the next gap.

### “Defer This Gap” (CRITICAL/HIGH/material gaps; or when typed in “Other”)
Picking it is a request; collect what the core needs before running anything, in one chat message:
1. **Why is it being deferred?** (mandatory — a deferral without a reason is refused; never write one for them.)
2. **Revisit date** (YYYY-MM-DD) — ask for it; it is mandatory when the packet says `defer_requires_revisit_date`, otherwise record it if they give one (not "someday").
3. **Who is deferring?** name and role (the decision owner if they say so — still ask for both). Optionally who the gap is waiting on.
Then `gap defer GAP-00x --reason "<their words>" --by-name … --by-role … [--revisit-by YYYY-MM-DD] [--owner "<waiting on>"]`. The core records the actor, the decision owner, the IST timestamp and the reason, and sets `DEFERRED`. Say plainly: **deferred is not resolved** — it stays on the outstanding list and keeps blocking the stories of the requirements it affects (`blocks_stories`). Then present the next gap. A deferred gap returns with `gap revisit GAP-00x` (the earlier deferral stays in its history).

**Fallback when `AskUserQuestion` is not available or fails** (for example in Claude Cowork): plain chat, one gap, the same choices as a short numbered list taken from the packet's `options` — for a MEDIUM/LOW gap: *1 Use the suggested answer (only if there is one) · 2 Type an answer or instruction · 3 Explain This Question · 4 Carry Forward*; for a CRITICAL/HIGH/material gap: *1 Type an answer or instruction · 2 Explain This Question · 3 Answer Later · 4 Defer This Gap*. Ask for the reply and wait. Do not move to the next gap until this one is answered, carried forward, answered later or deferred. The behaviour and the recorded data are identical in Claude Code and Cowork.

## 2. For each answer — only a validated, conflict-free answer resolves a gap
1. `gap answer …` stores the raw answer and returns `candidate_conflicts` (advisory: requirements including business rules, the BRD excerpts of the impacted requirements, active decisions, accepted answers). Review each candidate yourself against the BRD section, the related requirements, approved decisions and business rules — whether the answer came from “Other”, from the suggestion or from a revisit.
2. **Complete?** If not: `gap followup …` then ask one concise follow-up (the earlier answer is kept).
3. **Conflict?** If there is one: `gap conflict …`, explain both statements with their IDs and the impact, and **ask for clarification before anything is resolved** — options `Clarify`, `Confirm a change`, and `Defer This Gap` (or `Carry Forward` if the gap allows it; explanation in chat, then `AskUserQuestion`). Never pick a winner; the gap stays open. A clarifying answer is recorded as a new answer (`gap answer`) and validated again. An explicitly confirmed change goes through `gap resolve --conflict-status CHANGE_CONFIRMED …` (see human-approval.md); afterwards reassess impacted requirements, gaps and stories.
4. **Resolve** only when complete, conflict-free and reviewed. Ask: *"Shall I close GAP-00x as resolved with this meaning: '<interpretation>'? Please give your name and role as the person closing it."* Then:
   `gap resolve GAP-00x --answer-id A1 --interpretation "…" --closed-by-name … --closed-by-role … --closure-summary "…" --confirmation "<their confirming words>" --conflict-status PASSED --reviewed <ids you checked>`
   - Material gap and closer ≠ owner → the core answers `AWAITING_OWNER_CONFIRMATION`; the gap stays open until the owner confirms (`--owner-confirmation`).
   - Missing identity → ask; do not guess. The gap stays pending.
5. Report back: the decision ID, who closed it, the IST closure time (as returned by the core).

## 3. Revisit a carried-forward gap and update what depends on it
Carried-forward gaps are never forgotten: they appear in `status` (`CARRIED FORWARD (open)`), in `gaps next` (`carried_forward_open`), in the baseline and in every linked story. Offer to revisit them whenever the human mentions new information, when all other gaps are done, and at the end of the session.
1. `gaps next --carried` (or `gap revisit GAP-00x`) asks the gap again as a normal gap (the earlier carry-forward stays in `carry_forward_history` and the audit log). While it is being asked it blocks like any open gap, until it is answered or carried forward again.
2. Answer and resolve it exactly as in section 2.
3. The `gap resolve` result lists `stories_to_update` — every story that named the gap is flagged for impact review, and the baseline is marked stale. Then, in this order:
   - Linked requirements: if the decision changes a requirement, it was updated through `--requirement-updates` (new requirement version, stories flagged); otherwise the decision is simply linked to the requirement.
   - `baseline create`, then the human's `baseline confirm` (see `requirements-baseline`).
   - Update each flagged story with `story edit` (see `user-story-generation`): remove the gap from `unresolved_decisions`, apply the decision to the acceptance criteria, reassess readiness. The new version goes through `story-review` again; the approved earlier version stays in force until then. A story cannot be cleared with `confirm-unaffected` while it still names a resolved gap.

## 4. Triage mode (`/ba-agent:triage-gaps`)
Triage reviews the whole unresolved backlog — every `OPEN` gap (presented, answered later, reopened, carried forward) and every `DEFERRED` gap — instead of only what `gaps next` would pick. It uses the dialogs, validation (§2) and actions above unchanged; only the entry point differs.
1. `gaps triage` (read-only; add `--status OPEN|DEFERRED` to narrow it) → one chat table: priority, status, deferral reason / actor / date / revisit date, decision owner and who it waits on, linked requirements and stories, whether it blocks stories. Priority order is the review order. The decision owner is still required before any gap is presented (§0).
2. For each gap, one at a time: `gaps next --id GAP-00x` returns the same packet as `gaps next`. An `OPEN` gap becomes `WAITING_FOR_ANSWER`; a **deferred or carried-forward gap is shown as it is** (looking at it changes nothing — `revisit_needed_to_answer: true`). Show the chat message from §1, adding for a deferred gap its reason, who deferred it, when and the revisit date, and for a carried-forward gap since when and by whom. Then the short `AskUserQuestion` from the packet's `options`:
   - `OPEN` gap: as in §1 (`Explain This Question` + `Carry Forward` or `Answer Later`/`Defer This Gap`, `Suggested Answer` when evidence-backed).
   - `DEFERRED` gap: `Explain This Question`, `Keep Deferred`, plus “Other” for an answer.
   - Carried-forward gap: `Explain This Question`, `Keep Carried Forward`, plus “Other” for an answer.
3. Act on what the user chose:
   - **Answer** (typed, or the suggestion): for a deferred or carried-forward gap run `gap revisit GAP-00x` first (the earlier deferral / carry-forward stays in its history), then `gap answer …` and validate exactly as in §2 — complete? conflict with the BRD, requirements, earlier decisions or earlier answers? A conflict is explained neutrally and the gap stays open until the user clarifies, confirms a change or defers; only a complete, conflict-free, reviewed answer is closed with `gap resolve`.
   - **Explain**: `gap explain GAP-00x`, explain in plain language, show the same dialog again.
   - **Defer** (not offered for MEDIUM/LOW in the dialog, but available when the user asks): collect reason, name/role and revisit date as in §1, then `gap defer`. A gap that is already deferred is `Keep Deferred`; to change its reason, `gap revisit` it and defer again.
   - **Carry Forward** (MEDIUM/LOW, non-material only): as in §1.
   - **Keep it as it is** (typed “keep”, “leave it”, “ask me later”, or `Keep Deferred` / `Keep Carried Forward`): `gap keep GAP-00x`. An open gap is recorded as *Answer Later* (still `OPEN`, asked again); a deferred or carried-forward gap is left untouched and only the decision is audited (`GAP_TRIAGE_KEPT`). Nothing becomes resolved.
   - **Skip / stop**: change nothing; the gap is listed again next time.
4. Everything is saved as it happens (status, answers, deferrals, audit events), so an interrupted triage resumes by running `gaps triage` again — resolved gaps are gone from the list, the rest keep their state. Finish with `status`; if gaps were resolved while stories exist, the affected stories are flagged for impact review — offer `/ba-agent:triage-stories`.

## 5. Other actions
`gap reopen GAP-00x --reason … --by-name … --by-role …` (invalidates its decision; baseline/stories get impact flags) · `policy set --deferral-revisit-required CRITICAL,HIGH --by-name … --by-role … --statement "<their words>"` (only if the human asks for revisit dates to be mandatory) · `policy set --carry-forward true …` (project-wide acceptance for all non-blocking gaps; per-gap `Carry Forward` does not need it).

After each gap is settled (resolved, carried forward, answered later or deferred; or after a bulk carry-forward) run `gaps next --limit 1` again and present the next one the same way. Unresolved CRITICAL/HIGH and compliance-sensitive gaps (OPEN, answered later or DEFERRED) keep blocking their requirements in the baseline and in story approval; an open MEDIUM/LOW gap blocks until it is carried forward. Report all of this honestly and never close a gap on your own recommendation, on silence, or because the BRD embeds a "decision". When none remain to present: `baseline create` (→ `requirements-baseline`). If the session is interrupted, nothing is lost — answers, presented gaps, carried-forward gaps, answers-later and deferrals are saved; resume with `/ba-agent:review` (run `recover` first if `status` reports a missing audit event).
