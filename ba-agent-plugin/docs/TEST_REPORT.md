# Test report

**Date:** 2026-10-22 (IST) · **Environment:** Windows 10 Pro 10.0.19045, Python 3.14.8, Claude Code CLI 2.1.308, Git Bash + PowerShell.
**Not available / not run:** Claude Cowork, claude.ai chat, macOS/Linux, Python < 3.14 (see "Tests not run").

## 0. 1.10.0 prompt-surface before/after (2026-10-25)

Measured with `python -m tests.measure_efficiency` on the 1.9.0 tree (`git archive HEAD`) and on the working tree. Bytes; tokens are about bytes / 4.

| Surface | 1.9.0 | 1.10.0 |
|---|---|---|
| Skill descriptions always loaded (chars, plugin's own metric) | 1,928 | 200 |
| Gap session (router + gap loop) | 11,583 | 11,323 |
| Gap session with validate and actions | 19,469 | 19,216 |
| Story session | 11,446 | 11,196 |
| `/triage-gaps` (command + router + loop + triage) | 15,520 | 14,374 |
| `/triage-stories` | 18,311 | 16,920 |
| `gaps next --id` packet, mean of 3 gaps (CLI output) | 1,539 | 1,076 (-30%) |
| `story next --id US-003` (CLI output) | 2,097 | 2,002 |
| `references/cli-reference.md` | 12,336 | deleted; `help gap resolve` is about 0.9 KB |
| Delegation thresholds (`DELEGATE_ABOVE` / `BATCH_CHARS`) | 24,000 / 12,000 | 60,000 / 30,000 |

Reading these honestly: the per-stage prompt surface fell by only 1 to 8 %, because the router gained the optional-helpers and file-bridge lines while the stage files shrank. The larger gains are elsewhere: ten skill listings no longer advertised in every session, about 3 K tokens saved per CLI lookup, one third less per gap packet, no transcription of PDF/DOC/RTF/XLSX BRDs (about twice the document size) when a converter exists, and no helper agents spawned by default. The work order's estimate of 1 to 2 K tokens per entry command from slimmer commands was not reached (about 0.3 K measured).

Not run: Claude Cowork (so the file-bridge protocol is inferred, not observed), `convert` with LibreOffice or `pypdf` (only `pdftotext` and a hand-built XLSX were exercised), a live session with the one-skill layout.

## 1. Automated tests — 300 run, 299 passed, 1 skipped, 0 failed (re-run 2026-10-25 for 1.9.0; adds `test_gap_folders` (10: gap folders by status, migration of a 1.8 layout, one-step close), `test_cowork` (10: `find`, START-HERE.md, PDF hint, default baseline approver) and `test_adr`)

Command (from the plugin folder): `python -m unittest discover -s tests -t . -v` → `Ran 276 tests in ~155s — OK (skipped=1)` (Python 3.14.8, Windows 10). 1.7.0 added 5 tests in `test_carry_forward/` (bulk carry-forward: offer hint, per-gap records and audit, identity/owner/nothing-to-carry refusals, material gaps skipped, CLI). The 235 tests that existed before 1.6.0 all still pass (only their file paths were updated for the `md/` + `json/` layout); the 9 new tests are in `test_layout/` (path mapping, populated `md/` and `json/` folders at every stage, saved-files receipt, migration of a flat project, no overwrite of a conflicting loose file, CLI `files_saved` and `files`). Per-file counts of older areas were last measured at the version noted in their row.

| Area (file) | Tests | Covers |
|---|---|---|
| `test_context` | 27 | 1.8.0: presentation built by the core (inline vs card, dialog limits, suggestion shown with its source, eligibility-aware story options), the triage-stories dead end (shared `preflight`, `Update story` with the decision to apply, no `Approve` while blocked), lean packets, compact JSON and pruning, per-command `files_saved`, BRD batching for helper agents, YAML-safe frontmatter, dangling-reference detection, byte budgets for prompt files and CLI results |
| `test_schemas` | 14 | validator, record schemas (requirement/gap/state), stable unique IDs, IST format + clock refusal, fingerprints |
| `test_state_transitions` | 22 | stage/gap/story transition rules, **decision-owner gate (spec test 16)**, **closure audit with exact IST (17)**, **closure blocked without identity (18)**, **conflicting answers (19)**, **confirmed change keeps prior versions + triggers impact review (20)**, material-gap owner confirmation, per-gap owner override |
| `test_traceability` | 18 | baseline policy (strict blocking, explicit non-blocking policy), baseline versioning/reuse, story validation, approve/edit/defer/revisit (spec 9, 10), **blocked requirement cannot become a story (14)**, **orphan/duplicate/dangling (15)**, coverage, completion only when all approved, tamper detection |
| `test_resume` | 17 | crash at 5 points of gap closure (journal → decision → event → register), write failure (12), approval crash repair, interrupted Q&A (11), defer + resume (5), skip, reopen (6), next-action at each stage, zero-gap BRD (1), source change: cosmetic / content / removed section (8) |
| `test_gap_actions` | 19 | **1.2.0 gap dialog** (updated for 1.3.0: packet options now depend on whether the gap may be carried forward), evidence-only suggested answer, Answer Later (open, no reason, queue order, status), Defer This Gap (reason/actor/decision-owner mandatory, IST record, audit event, revisit-date format/past/policy, revisit history, due deferrals), validated-only resolution (BRD-excerpt conflict, clarification, incomplete answer), material gaps always block, **interruption between status save and audit append + `recover`**, reopen in a new session, CLI behaviour |
| `test_carry_forward` | 39 | **1.3.0 medium/low workflow:** eligibility (MEDIUM/LOW only; HIGH, CRITICAL, material and security gaps refused with `CARRY_FORWARD_NOT_ALLOWED`; owner and named actor required), gap stays `OPEN` with IST/actor/owner record and audit event, carried gap stops blocking while an uncarried MEDIUM gap and HIGH gaps still do, ineligible-again gap blocks again, not re-asked until revisited, revisit keeps `carry_forward_history`, defer ends a carry, crash between status save and audit + `recover`; **Suggested Answer** (option only when evidence-backed, recorded as the human's answer, validated, cannot be combined with typed text), **Other** validated against requirements/BRD for a LOW gap with conflict gate, **Explain This Question** (facts, no state change, audit); baseline (`READY` with `open_gap_ids`, strict default retained, stale/new version after carry); stories (open gaps must be declared with impact, invented/resolved gap links and bad readiness refused, derived readiness, working assumption labelled *not approved* and kept out of decisions, blocked requirements still refused, approval records open gaps and readiness, new gap flags/blocks existing stories); **revisit and update** (resolve flags only the stories that named the gap and reports them, baseline stale, flagged story cannot be cleared unchanged, edit → new version, re-reviewed, re-approved, v1 archived, readiness reassessed with two gaps); visibility in `status`, `trace`, summary, `complete`; CLI commands via subprocess |
| `test_triage` | 36 | **1.4.0 `/ba-agent:triage-gaps` and `/ba-agent:triage-stories`:** gap queue (OPEN + DEFERRED only, resolved excluded, priority order, status filter, deferral reason / actor / IST time / revisit date, decision owner, linked requirements and stories, carried-forward and due-for-revisit flags, read-only, works before the owner is known, empty queue); presenting one gap (OPEN → waiting; DEFERRED and carried gaps unchanged and offering Keep Deferred / Keep Carried Forward; owner required; unknown / resolved refused); keep (answer-later vs audit-only `GAP_TRIAGE_KEPT`); defer needs reason and actor and is never resolution; answering a deferred gap needs a revisit, keeps `deferral_history` and the full audit trail; a conflicting answer cannot resolve the gap; carry-forward rules unchanged; resume in a new session; story queue (pending / not approved / deferred listed, approved excluded, IN_REVIEW first, `eligible` / `blockers` for gap block, impact flag and stale baseline, read-only); present from any folder without moving it; approve a pending story; deferred and not-approved stories reconsidered via revisit then approved (audit + review history); **edit = new version back for approval, earlier version archived, approved version stays in force**; defer a pending or not-approved story and it stays available; `story keep`; audit and review-history sequence; traceability and resume; CLI for both queues |
| `test_ingest_safety` | 15 (1 skipped) | DOCX/TXT/MD ingestion, PDF refused, corrupt/empty/missing, section-only fetch, **BRD with embedded instructions (13)**, hallucinated quote rejected, duplicate + conflicting requirements (7), path traversal/absolute/drive escapes, original BRD protection, `safe_write.py` scope |
| `test_change_detection` (`test_change_detection.py`, `test_change_edge_cases.py`) | 52 | **1.5.0 change detection and impact analysis.** *BRD:* snapshot + hashes recorded at init; unchanged BRD records nothing; an edit is detected with old/new lines and explained (affected requirements by kind, decisions to re-validate, approved stories at risk, baseline, workflow) while requirements, baseline, stories, fingerprint and the BRD file stay untouched; idempotent check; `apply` refused without a real person and their words, then marks requirements / gaps / stories / baseline and reopens the workflow; deleted and added sections; formatting-only edits (markup, bullets, re-wrapping, whitespace, CRLF, a Word re-save) accepted and recorded with no review while a changed number stays material; superseded and reverted detections; stale record refused; full reconciliation to `RECONCILED`; project without a snapshot degrades honestly; DOCX BRDs. *Stories:* approval records hashes + immutable copy; manual JSON edit and manual Markdown edit detected and explained before anything moves; applying keeps the approved version and its approval record intact, keeps the edit byte for byte and turns it into the next version needing re-approval; re-approval reconciles and keeps version history (`story history`); formatting-only story edits do not touch approval; altered approval fields restored and flagged; deleted approved story kept, restorable, id never reused, removal confirmable; revert of an edit; agent edits tracked (material → new version, formatting-only → approval kept); untracked story in `approved/` not trusted; legacy approved stories adopted; destroyed Markdown, corrupt JSON, tampered snapshot, unknown requirement link; completed workflow reopened. *Workflow:* CLI guard (`CHANGES_UNREVIEWED`) on write commands, formatting-only never blocks, triage attaches the check without blocking, `status` read-only, resume across sessions (detected → applied → worked), `complete` stays PARTIAL, traceability *Change impact*, audit events with IST, BRD reverted/changed again at `source accept`, real subprocess run of the CLI |
| `test_e2e` | 3 | full CLI workflow (init → … → COMPLETED) via subprocesses with hostile free text + unicode through `--args-file`, error shapes/exit codes, wrapper scripts |

Spec §12.2 scenarios 1–20: all have an automated test **at the state-machine / artifact level** (numbers above). Skipped test: symlink-escape (`os.symlink` needs a privilege this Windows session lacks) — the `..`, absolute and drive-letter escapes are tested; symlink containment is implemented (`realpath` check) but **unexercised here**.

### 1.3.0 verification beyond the unit tests

- `claude plugin validate . --strict` (CLI 2.1.309) and `claude plugin validate .` on the marketplace manifest → **Validation passed** (same two directory-profile notices as before: no icon, no LICENSE).
- A scripted end-to-end run through `scripts/ba.py` on the library fixture: a MEDIUM gap with an evidence-backed suggestion offered *Suggested Answer / Other / Explain / Carry Forward*; a MEDIUM **security** gap was refused for carry-forward and offered the four original options; after carry-forward the baseline marked the requirement `READY` with `open_gap_ids`; the story was refused without its unresolved decision and, once declared, rendered `NEEDS CLARIFICATION`, the open gap, its potential impact and the working assumption labelled *NOT approved*; `status`/`trace` listed the carried gap; revisit → `--use-suggestion` → resolve returned `stories_to_update`; after a baseline refresh the story edit produced v2 `READY_FOR_DEVELOPMENT`; `validate` reported no errors throughout.
- **Not verified:** a live Claude session driving the new dialog (the `AskUserQuestion` option lists and the numbered-list fallback are specified in the skills and in the packet's `options`, but model behaviour with them was not observed), Claude Cowork, and macOS/Linux.

### 1.5.0 verification beyond the unit tests

- `claude plugin validate .` (CLI 2.1.309) → **Validation passed** with the new skill, command and schema (only the pre-existing no-icon / no-LICENSE directory notices).
- `scripts/package_plugin.py` builds `ba-agent-1.5.0.zip` (67 files) and includes `changes.py`, `snapshots.py`, `storytrack.py`, `change.schema.json`, `skills/change-detection/`, `commands/changes.md`.
- A scripted run through `scripts/ba.py` subprocesses (detect → blocked write → refused apply without identity → apply → status → list/show → validate) is part of the suite (`RealCliTests`).
- **Not verified:** a live Claude session driving `/ba-agent:changes` and the change-decision dialog (specified in the skill and `choice-prompts.md`, the model's behaviour is not tested); Claude Cowork; Python < 3.14; macOS/Linux; large BRDs (each guarded command and `status` re-reads and re-hashes the BRD; its cost on large documents was not measured).
- Known limits: line-level diffs need the stored BRD text (projects created before 1.5.0 get it on the first check while the BRD still matches); a DOCX is compared on its extracted text (images, headers and footers are not extracted, as before); a story edit that only reorders acceptance criteria is treated as material; there is no automatic merge of two simultaneous edits — a newer BRD edit supersedes an unapplied record.

## 2. Plugin validation

`claude plugin validate . --strict` → **Validation passed** (manifest, 3 commands, 10 skills, 5 agents). `claude plugin validate .` on the marketplace manifest (`D:\BA-Agents\.claude-plugin\marketplace.json`) → passed. The directory profile additionally reports a **missing LICENSE** (deliberately not chosen for you) and no icon (optional).

## 3. Live acceptance runs in a real Claude Code session (model-in-the-loop)

Plugin loaded with `claude -p … --plugin-dir D:/BA-Agents/ba-agent-plugin` (headless; the "human" answers were scripted in the prompt because `-p` cannot converse). Session init listed `ba-agent` with all 10 skills and `/ba-agent:start|status|review`.

| Run | Scenario | Result (verified from files, not from the model's report) |
|---|---|---|
| A | Happy path, 2-requirement BRD, 1 gap | `COMPLETED`, `validate` OK (0 errors/warnings). GAP-001 `RESOLVED`: owner/closer/respondent Priya Nair — Product Owner, `closed_at_ist=2026-10-22T19:34:47+05:30`, answer received 19:34:30, `DEC-001`, validation PASSED; 23 audit events all `+05:30`; 2 stories approved with verbatim statements; `candidates/` empty; BRD unchanged. |
| B | Human answer *contradicts* REQ ("30 min **before**" vs "**at** the due time"; the keyword pre-check cannot see this) and no change was authorised | Model caught it: GAP-001 stays `WAITING_FOR_ANSWER`, answer A1 kept verbatim and marked `CONFLICT`, conflict `C1 OPEN` vs REQ-003, **0** decisions, **0** `GAP_RESOLVED` events, REQ-003 still v1, no baseline/stories; model stopped and stated the clarification/confirmation/defer questions. |
| C | Decision owner not provided | Model stopped before showing any gap: owner `NOT YET IDENTIFIED`, all 7 gaps still `OPEN`, asked the exact owner question. |

Defects found by these runs and fixed: (1) helper JSON written outside the project folder → skill now requires `state/inbox/`; (2) story "I want create…" phrasing → skill asks for "to …"; (3) `NEXT:` text after completion → fixed. The first attempt of run A was blocked only because my `--allowedTools` omitted the PowerShell tool the model chose (harness setting, not a plugin defect).

**Limits of this evidence:** one model, three short runs on a 2–3-requirement synthetic BRD; the model was *told* the human answers; real interactive back-and-forth, longer BRDs, ambiguous answers, and non-Claude-Code surfaces are untested. The deterministic gates (§1) do not depend on model behaviour; the semantic checks (§3 B) do, and B is a single sample.

## 4. Efficiency measurements (method + results)

Core makes **0 model calls**. `python -m tests.measure_efficiency` (fixture `brd_library.md`, 1,064 bytes — tiny, so ratios illustrate the mechanism, not production savings):
section fetch = largest section 170 B (16 % of the BRD, mean 105 B); a gap packet averages 886 B (12 % of all requirement JSON); re-running `baseline create` on unchanged inputs → `reused: true` in ~10 ms; after editing one sentence, 2 of 8 requirements (25 %) need reassessment (1 changed section, 9 unchanged) in ~49 ms.

Host-reported usage for the live runs (real numbers from `claude -p … --output-format stream-json`):

| Run | Turns | Wall time | Cost | Output tokens | Cache-read tokens | Uncached input |
|---|---|---|---|---|---|---|
| A (full, 2 req / 1 gap / 2 stories) | 41 | 231 s | $0.40 | 8,887 | 1,284,108 | 58 |
| B (conflict stop) | 22 | — | $0.27 | 6,024 | — | — |
| C (owner stop) | 17 | — | $0.21 | 4,993 | — | — |

No before/after optimisation comparison was run, so **no reduction percentage is claimed**. Observation: cost is dominated by per-turn context re-reads (turn count), so fewer, larger batched commands (e.g. `req add` with whole sections) are the main lever.

## 5. Known limitations / honest gaps

- Cowork: package built, **not installed or tested**; script/folder access in Cowork is undocumented (see COMPATIBILITY.md).
- Persistent `claude plugin install` not run (would modify your Claude configuration); `--plugin-dir` is the tested route.
- Conflict pre-check is heuristic (numbers, negation, a small antonym list); it can miss subtle contradictions (run B shows the model, not the heuristic, caught one) and can flag benign overlaps (cleared via `--reviewed`).
- PDF/DOC/RTF/XLSX unsupported without installing libraries (not installed). DOCX images, headers/footers not extracted.
- Section-level (not sentence-level) change diff: all requirements in a changed section are flagged; `source_excerpt_still_present` helps triage.
- Identity cannot be verified: the core rejects empty/placeholder names but trusts what the user states.
- Timestamps use the host clock; a tampered clock is not detected beyond a plausibility check (year ≥ 2025).
- Single-writer assumption: no cross-process file locking.
- No LICENSE chosen; no icon.

## 6. Tests not run

Cowork; claude.ai chat; macOS/Linux; Python 3.9–3.13; symlink-escape on a privileged Windows session or POSIX; very large BRDs (>100 sections) and performance at scale; multi-user concurrent access; real human-in-the-loop sessions.


## 1.8.0 efficiency measurements (reproduce with `python -m tests.measure_efficiency`)

Bytes only (the core makes no model calls); roughly 4 bytes per token. Before = 1.7.0 (`git show HEAD`), after = 1.8.0, same scenario.

| Instructions loaded | Before | After |
|---|---|---|
| Gap session (router + gap skill + the references it needed) | 40.3 KB | 10.6 KB (17.9 KB when the validate and action references are also opened) |
| Story session | 29.5 KB | 10.7 KB |
| `/ba-agent:triage-gaps` | 43.1 KB | 14.6 KB |
| `/ba-agent:triage-stories` | 32.1 KB | 17.5 KB |
| Skill descriptions (always in context) | 3,702 chars | 1,927 chars |

| CLI result a model reads | Before | After |
|---|---|---|
| `owner set` | 1,150 B | 307 B |
| `gaps triage` (3 gaps) | 2,208 B | 848 B |
| `gaps next --id` (card + dialog included) | 2,563 B | 1,821 B |
| `gap answer` | 1,232 B | 436 B |
| `gap resolve` | 1,625 B | 709 B |
| `story triage` (5 stories) | 3,417 B | 1,795 B |
| `story next --id` (card + dialog included) | 2,753 B | 2,051 B |
| `baseline create` | 2,169 B | 397 B |

Not measured here: real token counts and the text the model writes in chat (the host reports those); the saving from not composing a card or table by hand is on top of the above. `claude plugin validate .` passes (the LICENSE and icon notes of the directory profile are unchanged).
