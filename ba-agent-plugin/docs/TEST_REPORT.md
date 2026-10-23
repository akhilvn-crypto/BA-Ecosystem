# Test report

**Date:** 2026-10-22 (IST) · **Environment:** Windows 10 Pro 10.0.19045, Python 3.14.8, Claude Code CLI 2.1.308, Git Bash + PowerShell.
**Not available / not run:** Claude Cowork, claude.ai chat, macOS/Linux, Python < 3.14 (see "Tests not run").

## 1. Automated tests — 84 run, 83 passed, 1 skipped, 0 failed

Command (from the plugin folder): `python -m unittest discover -s tests -t . -v` → `Ran 84 tests in ~47s — OK (skipped=1)`.

| Area (file) | Tests | Covers |
|---|---|---|
| `test_schemas` | 14 | validator, record schemas (requirement/gap/state), stable unique IDs, IST format + clock refusal, fingerprints |
| `test_state_transitions` | 19 | stage/gap/story transition rules, **decision-owner gate (spec test 16)**, **closure audit with exact IST (17)**, **closure blocked without identity (18)**, **conflicting answers (19)**, **confirmed change keeps prior versions + triggers impact review (20)**, material-gap owner confirmation, per-gap owner override |
| `test_traceability` | 18 | baseline policy (strict blocking, explicit carry-forward), baseline versioning/reuse, story validation, approve/edit/defer/revisit (spec 9, 10), **blocked requirement cannot become a story (14)**, **orphan/duplicate/dangling (15)**, coverage, completion only when all approved, tamper detection |
| `test_resume` | 15 | crash at 5 points of gap closure (journal → decision → event → register), write failure (12), approval crash repair, interrupted Q&A (11), defer + resume (5), skip, reopen (6), next-action at each stage, zero-gap BRD (1), source change: cosmetic / content / removed section (8) |
| `test_ingest_safety` | 15 (1 skipped) | DOCX/TXT/MD ingestion, PDF refused, corrupt/empty/missing, section-only fetch, **BRD with embedded instructions (13)**, hallucinated quote rejected, duplicate + conflicting requirements (7), path traversal/absolute/drive escapes, original BRD protection, `safe_write.py` scope |
| `test_e2e` | 3 | full CLI workflow (init → … → COMPLETED) via subprocesses with hostile free text + unicode through `--args-file`, error shapes/exit codes, wrapper scripts |

Spec §12.2 scenarios 1–20: all have an automated test **at the state-machine / artifact level** (numbers above). Skipped test: symlink-escape (`os.symlink` needs a privilege this Windows session lacks) — the `..`, absolute and drive-letter escapes are tested; symlink containment is implemented (`realpath` check) but **unexercised here**.

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
