# Architecture

## Principle: judgement in the model, guarantees in code

| Layer | Responsibility | Where |
|---|---|---|
| UX | `/ba-agent:start`, `status`, `review`; plain-language, one decision at a time | `commands/` |
| Workflow knowledge | Stage routing, decomposition, gap taxonomy, story rules, shared non-negotiables | `skills/` (+ `references/`), optional `agents/` |
| Deterministic core | State machines, identities, IST clock, audit log, schemas, IDs, fingerprints, conflict gate, safe writes, validation, traceability, recovery | `scripts/ba_lib/` via `scripts/ba.py` |
| Artifacts | Authoritative JSON + rendered Markdown + append-only JSONL | the project folder the user confirmed |

The model does semantic work (reading, decomposing, finding gaps, writing stories, judging whether an answer is complete or contradictory) and persists everything through the core. The core **refuses** actions that would violate a rule (e.g. resolving a gap with no identity, approving the wrong story version, presenting a gap with no decision owner), so correctness does not depend on the model remembering the rules. Hooks are not used: nothing depends on them. Subagents are optional helpers that only write JSON files; no core behaviour requires them.

Standard library only (Python ≥ 3.9; tested on 3.14): no installs, no network, no database, no MCP.

## Modules (`scripts/ba_lib/`)

`util` (IST clock, hashing, atomic writes, path containment, identity checks) · `schema` (JSON-Schema subset validator for `schemas/*.json`) · `ingest` (md/txt/docx → text, section outline, hashes, instruction-like-line scan) · `project` (store, state machines, audit, ID allocation, impact flagging) · `reqs` (requirements, versioning, source-change impact) · `gaps` (owner, gap lifecycle, answers, conflict gate, journaled closure) · `baseline` · `stories` · `checks` (validate, trace, recover, status, completion) · `conflicts` (advisory heuristics) · `render` (+ `templates/`) · `cli`.

## State machines

Workflow: `INITIALIZED → BRD_PARSED → REQUIREMENTS_READY → GAP_ANALYSIS ⇄ WAITING_FOR_GAP_INPUT → REQUIREMENTS_BASELINED → STORIES_READY → WAITING_FOR_STORY_REVIEW → COMPLETED`; `PAUSED`/`BLOCKED` resume only to the saved stage; later stages re-open to earlier ones when decisions, requirements or the BRD change (`COMPLETED` can be re-opened). Gaps: `OPEN → WAITING_FOR_ANSWER → RESOLVED → REOPENED …`, `DEFERRED` leaves only by explicit revisit; `RESOLVED` is reachable **only** from `WAITING_FOR_ANSWER` via `gap resolve`. Stories: `CANDIDATE → IN_REVIEW → APPROVED | DEFERRED | CHANGES_REQUESTED`; `APPROVED → SUPERSEDED` only through a newer approved version. Tables: `project.py`.

## Gap closure protocol (crash-safe)

1. Validate everything (owner, identities, answer complete, conflict gate, materiality, clock) — no writes yet.
2. Write `state/pending/<id>.closure.json` (the full plan: decision, audit event, new gap record, requirement versions).
3. Apply idempotently: append decision → append `GAP_RESOLVED` audit event (same `event_id`) → new requirement versions → **gap register (atomic replace)** → rendered files → impact propagation → delete journal.
4. A crash at any point leaves the journal; `validate` flags it, `recover` rolls forward without duplicating the decision or event; retries are refused with `CLOSURE_IN_PROGRESS`. `validate` also cross-checks every `RESOLVED` gap against its decision and audit event. Tested at five crash points.

## Conflict handling

Every answer is compared (advisory, deterministic) with requirements, active decisions and earlier accepted answers: numeric disagreement, negation polarity, opposing terms. Candidates must be explicitly reviewed (`--reviewed`) before `PASSED`; a recorded conflict blocks `PASSED`; a confirmed change (`CHANGE_CONFIRMED`) needs a named authoriser, a verbatim statement, and new content for every contradicted requirement; it versions the requirement, supersedes contradicted decisions, reopens gaps closed by them, flags stories, stales the baseline and re-opens analysis. The heuristic is a safety net, not a semantic judge — the model and human decide.

## Efficiency design

BRD read once; later steps fetch one section (`source section`). Gap packets carry only the gap, its requirements and prior answers. Baseline creation is skipped when its input hash is unchanged. BRD edits are diffed per section so only requirements in changed sections are reassessed. Deterministic checks replace extra model reviews. Measurements: `python -m tests.measure_efficiency`.

## Safety

BRD content is data: instruction-like lines are flagged (advisory) and no code path acts on BRD text. Writes go through `safe_join` (relative paths only, `..`/absolute/drive/symlink escapes rejected), the BRD path is write-protected, writes are temp+replace with a rolling `.bak`, `safe_write.py` only writes under `state/inbox/` and `reports/`. Identities must come from the user (placeholder names like "Claude"/"unknown" are rejected; the code cannot prove a person is real). The core does not claim to be a sandbox.
