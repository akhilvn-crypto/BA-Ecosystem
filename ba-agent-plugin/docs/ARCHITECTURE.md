# Architecture

## Principle: judgement in the model, guarantees in code

| Layer | Responsibility | Where |
|---|---|---|
| UX | `/ba-agent:start`, `status`, `review`, `triage-gaps`, `triage-stories`, `changes`; plain-language, one decision at a time | `commands/` |
| Workflow knowledge | Stage routing, decomposition, gap taxonomy, story rules, shared non-negotiables | `skills/` (+ `references/`), optional `agents/` |
| Deterministic core | State machines, identities, IST clock, audit log, schemas, IDs, fingerprints, conflict gate, safe writes, validation, traceability, recovery | `scripts/ba_lib/` via `scripts/ba.py` |
| Artifacts | Authoritative JSON + rendered Markdown + append-only JSONL | the project folder the user confirmed |

The model does semantic work (reading, decomposing, finding gaps, writing stories, judging whether an answer is complete or contradictory) and persists everything through the core. The core **refuses** actions that would violate a rule (e.g. resolving a gap with no identity, approving the wrong story version, presenting a gap with no decision owner), so correctness does not depend on the model remembering the rules. Hooks are not used: nothing depends on them. Subagents are optional helpers that only write JSON files; no core behaviour requires them.

Standard library only (Python ≥ 3.9; tested on 3.14): no installs, no network, no database, no MCP.

## Modules (`scripts/ba_lib/`)

`util` (IST clock, hashing, atomic writes, path containment, identity checks) · `schema` (JSON-Schema subset validator for `schemas/*.json`) · `ingest` (md/txt/docx → text, section outline, hashes, instruction-like-line scan) · `project` (store, state machines, audit, ID allocation, impact flagging) · `reqs` (requirements, versioning, source-change impact) · `gaps` (owner, gap lifecycle, answers, conflict gate, journaled closure) · `baseline` · `stories` · `checks` (validate, trace, recover, status, completion) · `conflicts` (advisory heuristics) · `render` (+ `templates/`) · `adr` (ADR-LOG.md views) · `overview` (START-HERE.md) · `discover` (`find`) · `cli`.

## State machines

Workflow: `INITIALIZED → BRD_PARSED → REQUIREMENTS_READY → GAP_ANALYSIS ⇄ WAITING_FOR_GAP_INPUT → REQUIREMENTS_BASELINED → STORIES_READY → WAITING_FOR_STORY_REVIEW → COMPLETED`; `PAUSED`/`BLOCKED` resume only to the saved stage; later stages re-open to earlier ones when decisions, requirements or the BRD change (`COMPLETED` can be re-opened). Gaps: `OPEN → WAITING_FOR_ANSWER → RESOLVED → REOPENED …`, `DEFERRED` leaves only by explicit revisit; **Carry Forward** is not a status — the gap stays `OPEN` with a `carry_forward` record (actor, decision owner, IST time, reason) that is archived to `carry_forward_history` on revisit, deferral or resolution; `RESOLVED` is reachable **only** from `WAITING_FOR_ANSWER` via `gap resolve`. Stories: `CANDIDATE → IN_REVIEW → APPROVED | DEFERRED | CHANGES_REQUESTED`; `APPROVED → SUPERSEDED` only through a newer approved version. Tables: `project.py`.

## Carry-forward model (MEDIUM/LOW gaps)

- **Eligibility** (`gaps.carry_forward_blocker`): priority MEDIUM/LOW, not in the policy's blocking priorities, and not `material` (security, compliance, scope/policy, critical business rules). Otherwise `CARRY_FORWARD_NOT_ALLOWED` and the existing rules apply. One shared rule, `blocks_requirements`, is used by baseline, status and story approval: a *currently eligible* carried gap does not block; it blocks again if it becomes ineligible.
- **Baseline:** a requirement whose only unresolved gaps are carried is `READY` with `open_gap_ids`; the outstanding-gaps list marks carried gaps; the input hash includes the carried state, so carry/revisit/resolve produce a new version (the baseline is marked stale).
- **Stories:** `unresolved_decisions[]` (gap, potential impact written by the author from the gap/BRD, readiness effect, optional interim assumption) must name **every** open gap that touches the story's requirements (checked on add, edit, approve and confirm-unaffected). `readiness` is derived (`READY_FOR_DEVELOPMENT` / `NEEDS_CLARIFICATION` / `NOT_READY`); interim assumptions are rendered as *not approved* and never become decisions.
- **Resolution loop:** `gap revisit`/`gaps next --carried` → normal validated resolution → `apply_closure` flags every story listing the gap (any folder) and stales the baseline → refresh the baseline → `story edit` (new version, re-review). `confirm-unaffected` is refused while a story still names a resolved gap; a gap opened later flags existing stories the same way.
- **Visibility:** `status`, `trace` (open-gap → story table, stories missing a link), baseline, gap/story Markdown, `complete` (a `NOT_READY` story keeps the workflow PARTIAL; carried gaps are always listed).

## Triage commands (1.4.0)

`triage-gaps` and `triage-stories` are orchestration only; they add no business rules and no agents (the gap-analyst / story-builder agents generate content and are not involved in reviewing it).
- **List** (read-only, derived from the files on every call, so resume needs no extra state): `gaps triage` = unresolved gaps that are `OPEN` (incl. `WAITING_FOR_ANSWER`, reopened, answer-later, carried forward) or `DEFERRED`; `story triage` = stories in `candidates/`, `unapproved/`, `deferred/`, each with `eligible` / `blockers` computed from the same rules `story approve` enforces (impact review, changed requirements, blocking gaps, stale baseline).
- **Preflight (1.8.0):** `story triage` reports problems that block every story once (`preflight`); the command clears them in one dialog before presenting stories, and a story flagged by a resolved gap offers `Update story` with the decision to apply.
- **Present one:** `gaps next --id` (OPEN → `WAITING_FOR_ANSWER`; DEFERRED and carried-forward gaps are returned unchanged, because looking must not change state), `story next --id` (any pending folder).
- **Act:** only existing actions — `gap answer / explain / defer / carry-forward / revisit / resolve`, `story approve / edit / defer / request-changes / revisit` — plus two audit-only "keep" actions (`gap keep`, `story keep`). A deferred or carried gap needs `gap revisit` before it can be answered; a deferred or not-approved story needs `story revisit` before it can be approved (same version, audited). `story edit` always creates a new CANDIDATE version.
- **Transitions added:** story `CHANGES_REQUESTED → DEFERRED`; `story revisit` accepts `CHANGES_REQUESTED → CANDIDATE` (already in the transition table).

## Change detection and impact analysis (1.5.0)

**Baselines for comparison.** `init` stores the extracted BRD text as `snapshots/brd/brd-v1.txt` (index with fingerprint, text fingerprint and a *material* fingerprint = hash of section levels, title paths and normalised bodies). Accepting a reassessed BRD (`source accept`) or a formatting-only change appends the next version. Each `story approve` records, in `snapshots/stories/index.json`, the material hash (title, user story, acceptance criteria, linked requirements, dependencies, assumptions, declared unresolved decisions, notes), the content hash, an approval/readiness hash and an immutable copy `US-nnn.vN.json` (+ `.md`) of exactly what was approved; the audit event `STORY_APPROVED` carries the hashes.

**Material vs formatting-only.** `ingest.material_normalize` drops Markdown emphasis/heading/list/table markup, line wrapping, repeated whitespace and typographic quotes; words, numbers, case and punctuation inside a sentence stay. Equal normalised text => `FORMATTING_ONLY` (BRD: also bytes-only changes such as CRLF; stories: whitespace/Markdown decoration, a regenerated `.md`). Formatting-only differences are accepted by the core itself (fingerprints, requirement `source_fingerprint`/reference, tracking hashes refreshed), recorded as `RECORDED` change records and audited; nothing is flagged and no review is asked for.

**Detection (`changes.detect`, read-only).** BRD: section diff by title path (`added` / `removed` / `changed` / `formatting_only`) with line-level hunks between the snapshot and the new text. Stories: for every file in `stories/approved/` and every tracked story, compare with the tracked hashes; classes `MANUAL_EDIT` (JSON or Markdown copy, field-level diff), `APPROVAL_ALTERED` (status/version/approval/readiness hand-edited), `DELETED`, `UNREADABLE`, `UNTRACKED_APPROVED` (in `approved/` with no approval on record), `FORMATTING_ONLY`. Approved stories that predate tracking are adopted (`STORY_TRACKING_ADOPTED`) if their approval is in the audit log.

**Impact (`_source_impact`, `_story_impact`).** BRD: affected requirements (section deleted / quoted text removed or reworded / section changed with quoted text intact), open gaps, confirmed decisions to re-validate (listed, never invalidated), linked stories (approved ones named), baseline, workflow stage, new sections no requirement covers, modified sections nobody references. Story: changed fields, requirements added/removed, open gaps not declared, requirement-version drift, stories sharing requirements, the approval on record.

**Records and lifecycle.** `changes/json/CHG-nnn.json` + `changes/md/CHG-nnn.md` + `changes/json/change-register.json` (schema `change`). `DETECTED` (explained, nothing downstream touched) → `APPLIED` (`changes apply`: named person + their words; requirements `NEEDS_REVIEW`, gaps `needs_impact_review`, stories `impact_review` with `change_id`, baseline stale, workflow reopened; for an edited approved story the approved version is restored from its snapshot, the verbatim edit is kept in `snapshots/stories/US-nnn.observed-CHG-nnn.*` and becomes the next `CANDIDATE` that needs re-approval) → `RECONCILED` when every item (`requirement`, `source_acceptance`, `new_section`, `gap`, `baseline`, `story`, `reapproval`, `story_removal`) is done — closed by the real work, evaluated from live artifacts (`reconcile`, run after every command) or dismissed with a reason (`new_section`, `story_removal` only). Also `RECORDED` (formatting-only; BA Agent's own `story edit`), `SUPERSEDED` (changed again before applying), `REVERTED` (file put back). Re-detection is idempotent (`dedupe_key`).

**Where it runs.** `status` shows it read-only; `changes check` records. `guard` runs inside the CLI before `req add|commit`, `gaps add|commit|next`, `gap resolve`, `baseline create|confirm`, `story add|next|approve|edit` and refuses with `CHANGES_UNREVIEWED` while a material change is unreviewed; `gaps triage`, `story triage`, `complete`, `report` run the check and attach `change_check`; the start/review/changes commands and the resume skill call it first. `complete` stays PARTIAL while any record is unreviewed or has open items. `validate` warns about unreviewed changes, drifted approved stories and damaged snapshots; `trace --write` adds a *Change impact* section.

**Guarantees.** The BRD is only read (existing `SOURCE_PROTECTED` guard); the accepted fingerprint changes only at `source accept`; a manual edit is preserved byte for byte before anything else is written; approved versions, approvals and decisions are never deleted or invalidated without a recorded reason (`story history` lists every approved version, approver, wording and snapshot); story ids of deleted stories are never reused.

## Gap closure protocol (crash-safe)

1. Validate everything (owner, identities, answer complete, conflict gate, materiality, clock) — no writes yet.
2. Write `state/pending/<id>.closure.json` (the full plan: decision, audit event, new gap record, requirement versions).
3. Apply idempotently: append decision → append `GAP_RESOLVED` audit event (same `event_id`) → new requirement versions → **gap register (atomic replace)** → rendered files → impact propagation → delete journal.
4. A crash at any point leaves the journal; `validate` flags it, `recover` rolls forward without duplicating the decision or event; retries are refused with `CLOSURE_IN_PROGRESS`. `validate` also cross-checks every `RESOLVED` gap against its decision and audit event. Tested at five crash points.

## Conflict handling

Every answer is compared (advisory, deterministic) with requirements, active decisions and earlier accepted answers: numeric disagreement, negation polarity, opposing terms. Candidates must be explicitly reviewed (`--reviewed`) before `PASSED`; a recorded conflict blocks `PASSED`; a confirmed change (`CHANGE_CONFIRMED`) needs a named authoriser, a verbatim statement, and new content for every contradicted requirement; it versions the requirement, supersedes contradicted decisions, reopens gaps closed by them, flags stories, stales the baseline and re-opens analysis. The heuristic is a safety net, not a semantic judge — the model and human decide.

## Context engineering (1.8.0)

Principle: the model receives *the right information, only when it is needed*, and the core does everything that does not need judgement.
- **Load on demand.** `ba-workflow` is the router and the non-negotiables; each stage skill is a short loop. Branch detail lives in `references/` of that skill and is opened only at the branch (`gap-resolution/references/{actions,validate-and-resolve,revisit,triage}.md`, `story-review/references/triage.md`). `change-detection` loads only when `changes check` reports a change. `tests/test_context` fails if a skill grows past its budget or a reference pointer dangles.
- **Presentation in code (`ba_lib/present.py`).** One gap or story at a time. The core builds the `AskUserQuestion` payload (`dialog`), the compact markdown (`card`) and a numbered `fallback`; the model relays. A gap whose dialog holds everything is `inline` - sent as the dialog alone; a story always sends its card first because its acceptance criteria must be read to be approved. Queues are a one-line `summary`.
- **Right fields, right time.** The gap packet keeps what the model *acts on*; `gap explain` carries the plain-language facts (fetched only if the human asks), `story next` carries a *Decision to apply* only for a story flagged by a resolved gap, `story show` / `--full` the raw record only when an edit file is needed.
- **Hoisted blockers.** `story triage.preflight` states what blocks every story once (stale baseline, pending BRD review), so the user is never walked through stories that cannot be approved when one step unblocks them all.
- **Compact output.** One-line JSON, empty values omitted, `files_saved` limited to the folders a command changed (a fresh disk scan taken after the write, which is also the save check).
- **Delegation.** `source outline` returns sizes, `batches` (<= 12k characters) and `delegate` (BRD > 24k characters). Helper agents read the sections and return <= 8 lines plus a file under `state/inbox/`; only the main session runs mutating commands.
- Measure with `python -m tests.measure_efficiency` (prompt surface per stage, CLI result sizes).

## Efficiency design

BRD read once; later steps fetch one section (`source section`). Gap packets carry only the gap, its requirements and prior answers. Baseline creation is skipped when its input hash is unchanged. BRD edits are diffed per section so only requirements in changed sections are reassessed. Deterministic checks replace extra model reviews. Measurements: `python -m tests.measure_efficiency`.

## Safety

BRD content is data: instruction-like lines are flagged (advisory) and no code path acts on BRD text. Writes go through `safe_join` (relative paths only, `..`/absolute/drive/symlink escapes rejected), the BRD path is write-protected, writes are temp+replace with a rolling `.bak`, `safe_write.py` only writes under `state/inbox/` and `reports/`. Identities must come from the user (placeholder names like "Claude"/"unknown" are rejected; the code cannot prove a person is real). The core does not claim to be a sandbox.
