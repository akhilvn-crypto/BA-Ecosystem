# BA Agent v2 — Implementation Plan

**From:** ba-agent 1.10.2 (BRD → atomic requirements → gaps → baseline → approved user stories)
**To:** ba-agent 2.0.0 (requirements folder → gaps → human triage → an **enriched copy** of each requirement document with acceptance criteria and open gaps added; the supplied source is never modified)
**Revision 2 (2026-10-26):** the supplied requirements are kept as untouched source and the enriched output is a separate, generated copy. This replaces the earlier in-place write-back design and simplifies sections 4, 5.2, 6.1, 6.5, 8 and 9.
**Basis:** static audit of the plugin source (commands, skills, stage references, agents, schemas, templates, `scripts/ba_lib/*`, docs, tests). I did not run the test suite; test counts are from reading the files (README says 336).

---

## 1. Summary

The proposed redesign removes the two most expensive parts of the current pipeline: **decomposing a BRD into atomic requirements** and **generating user stories**. The upstream agent already delivers well-defined requirements as Markdown, so the plugin becomes a gap-finding, triage and enrichment layer on top of that document.

| Stays | Goes | New |
|---|---|---|
| Gap analysis, taxonomy, priority, `material` flag | BRD decomposition (`req add/commit/update/...`), `requirement-analyst` | Ask where requirements live (folder or file) |
| Decision owner, IST audit log, decision log, ADR log | DOCX/PDF/RTF/XLSX conversion (`convert.py`) | Requirement **index** (read-only locator over `.md` files) |
| Human triage: answer / explain / defer / carry forward | Baseline (`baseline.py`, `baseline-vN`) | **Enriched copy** of each requirement file: untouched source text plus generated AC / open-gap blocks |
| Conflict gate, crash-safe closure, carry-forward policy | User stories (`stories.py`, `storytrack.py`, `story-builder`, `triage-stories`) | **Acceptance-criteria (AC) records**, reviewed and approved by a human, rendered under each requirement |
| Change detection and impact analysis (reworked) | `US-nnn` / `REQ-nnn` ID allocation by the core | Requirement identity taken from the source document |
| Cowork file bridge (extended) | — | Source treated strictly as input (hash-checked); only our project folder and `enriched/` are committed back |

The single biggest design change is that **each supplied requirement file gets an enriched twin**: the source stays exactly as delivered and is the only input; a generated copy of it, with acceptance criteria and open gaps added under each requirement, is the output. Rule 6 ("the source is read-only") therefore **stays**; the source is never lost or altered.

---

## 2. Audit of the current plugin

### 2.1 Inventory

| Area | Contents | Size / count |
|---|---|---|
| Manifest | `.claude-plugin/plugin.json` v1.10.2, author is a placeholder ("BA Agent Plugin") | — |
| Commands (6) | `start`, `status`, `review`, `triage-gaps`, `triage-stories`, `changes` | ~7 KB |
| Skill (1) | `skills/ba-workflow/SKILL.md` router + 10 non-negotiables | 4.9 KB |
| Stage references | 11 stage files + 6 branch files under `references/stages/`, 7 shared references | ~60 KB |
| Agents (4) | `requirement-analyst`, `gap-analyst`, `story-builder`, `quality-reviewer` (all optional helpers) | ~8 KB |
| Schemas (6) | requirement, gap, story, decision, change, workflow_state | ~18 KB |
| Templates (4) | requirement, gap, decision, user_story | ~2.5 KB |
| Core (`scripts/ba_lib`, 22 modules) | deterministic CLI, state machines, audit, safe writes | ~430 KB |
| Tests | 16 test packages | ~335 test functions |
| Docs | README, USER_GUIDE, ARCHITECTURE, COMPATIBILITY, TEST_REPORT, CHANGELOG (32 KB) | — |

Largest modules: `changes.py` 62 KB, `gaps.py` 66 KB, `checks.py` 40 KB, `stories.py` 34 KB, `project.py` 31 KB, `cli.py` 26 KB, `reqs.py` 23 KB.

### 2.2 Current flow

`init` (BRD file) → stage `BRD_PARSED` → model decomposes into atomic `REQ-nnn` (`req add`, verbatim-quote check, `req commit`) → gap analysis (`gaps add`, `req pairs`, `gaps commit --cross-pass-done`) → owner set → gap resolution one at a time (`gaps next` → `gap answer` → `gap resolve`, or explain / answer-later / defer / carry-forward) → `baseline create/confirm` → `story add` (Given/When/Then, `unresolved_decisions` for open gaps) → `story next/approve/edit/defer` → `complete`.

State machine: `INITIALIZED → BRD_PARSED → REQUIREMENTS_READY → GAP_ANALYSIS ⇄ WAITING_FOR_GAP_INPUT → REQUIREMENTS_BASELINED → STORIES_READY → WAITING_FOR_STORY_REVIEW → COMPLETED` (`project.py`, `STAGE_TRANSITIONS`).

### 2.3 What is worth keeping (and is already generic)

1. **Gap engine** — taxonomy, priority rubric, `material`, suggested answers with a named source, answer validation, `precheck` conflict heuristics, journaled closure (`state/pending/*.closure.json`, `recover`), defer / answer-later / carry-forward / revisit / reopen, `policy set`. Nothing in it is specific to BRDs or stories except the *impact propagation* in `apply_closure`.
2. **Identity and audit** — decision owner first, named closers, IST clock, append-only `audit.jsonl`, `decision-log.jsonl`.
3. **Presentation in code** (`present.py`) — the core builds the `AskUserQuestion` dialog and card; the model relays. This is the main token-saving idea and it carries over to AC review unchanged.
4. **Approval machinery** — versioned records, exact-version approval with verbatim `--statement`, defer / request-changes / revisit, immutable approved snapshots with material/content/approval hashes (`storytrack.py`, `snapshots.py`). This generalises cleanly from "story" to "AC record for one requirement".
5. **Change detection** (`changes.py`) — material vs formatting-only normalisation (`ingest.material_normalize`), `DETECTED → APPLIED → RECONCILED` lifecycle, manual-edit detection on approved Markdown. The concept is exactly what is needed once the document is edited by both the upstream agent and us; the implementation needs rework (section 6.4).
6. **Safe writes** — temp + replace, rolling `.bak`, path containment, `safe_write.py` for helper files.
7. **Triage commands** — list is derived from files, so resume needs no extra state.

### 2.4 What is wrong or risky for the new flow

| # | Finding | Evidence |
|---|---|---|
| A1 | Requirement identity is minted by the core (`REQ-001…`) and tied to BRD *sections* (`S1.2`). Upstream IDs will not match. | `reqs.add_requirements`, `requirement.schema.json` pattern `^REQ-\d{3,}$`, `source_section_ids` pattern `^S\d+(\.\d+)*$` |
| A2 | The same `REQ-\d{3,}` pattern is hard-coded in `gap.schema.json` (`impacted_requirement_ids`) and `story.schema.json`. Any upstream ID scheme such as `FR-2.1` or `AUTH-004` is rejected. | schemas |
| A3 | The current core already protects the source (`project.py:306`, `SOURCE_PROTECTED`; non-negotiable 6). That is now an asset: the new flow keeps it and writes only to a separate `enriched/` folder. | `project.py`, `SKILL.md` |
| A4 | One source file only: `init --brd <file>`, `state.source` is a single object, snapshots are keyed to one document, `source.fingerprint` is a single hash. | `Project.create`, `snapshots.py` |
| A5 | The source fingerprint covers the whole file. Once we write into the document, every enrichment would look like a "BRD change". | `ingest.extract`, `changes.detect_source` |
| A6 | Format handling is wider than needed: DOCX parser, `.txt` heading heuristics, `convert.py` (PDF/DOC/RTF/XLSX), cp1252 fallback. Requirements arrive as `.md` only. | `ingest.py`, `convert.py`, tests `test_convert` (7), parts of `test_ingest_safety` (15) |
| A7 | Stories and baselines are woven through the core: lines mentioning "stor" — `changes.py` 209, `stories.py` 162, `checks.py` 115, `gaps.py` 63, `project.py` 55; "baseline" — `baseline.py` 40, `checks.py` 33, `project.py` 22. Removal is a refactor, not a delete. | grep counts |
| A8 | Gap closure impact propagation marks stories and the baseline stale (`gaps.apply_closure`). The target of that propagation changes. | `gaps.py` |
| A9 | `next_action` and `completion` are written around baseline → stories. | `checks.py` |
| A10 | Docs and metadata drift: README hard-codes `D:\BA-Agents`; Cowork support "not verified"; no LICENSE; placeholder author; `TEST_REPORT.md` counts will be wrong after the change. | README, plugin.json |
| A11 | Very large modules (`gaps.py` 66 KB, `changes.py` 62 KB). Not a blocker, but touching them is risky without the tests; the plan keeps tests green per phase. | file sizes |

---

## 3. Target flow

```
 ask where requirements are  ──►  index requirements (read-only)  ──►  gap analysis
        (folder or file)            ids, titles, text, hashes           per requirement + cross pass
                                                                              │
                                                                              ▼
                                                              HUMAN TRIAGE of gaps (unchanged model)
                                                              answer · explain · defer · carry forward
                                                                              │
                                                                              ▼
                                                      draft AC per requirement (only where not blocked)
                                                                              │
                                                                              ▼
                                                      HUMAN REVIEW of AC (approve · edit · defer)
                                                                              │
                                                                              ▼
                          render enriched copy: source text + Acceptance Criteria + Open Gaps + Readiness
                                                                              │
                                                                              ▼
                                  change detection keeps source, AC records and gaps in sync
```

### 3.1 Step by step

1. **Locate.** First question, before anything else: *"Where are the requirements? (a folder, or a single `.md` file)"*. `find` looks first (attached folder, current folder) and the model asks only if it finds nothing or several candidates. The folder may contain one file or many.
2. **Index.** The core reads every `.md` file under the folder (non-recursive by default, recursive optional), detects requirement units with the locator (6.1), and records `req_key`, title, line extent, text hash. No decomposition, no rewriting, no verbatim-quote machinery.
3. **Gap analysis.** Same as today but the model reads the requirement unit directly (`req show <key>`), raises evidence-based gaps, then runs the cross-requirement pass (`req pairs`, now across files).
4. **Triage.** Same decision-owner-first flow, same actions, same strict blocking policy.
5. **Draft AC.** For each requirement that is `READY` or `READY_WITH_OPEN_GAPS` (carried forward only), the model drafts Given/When/Then criteria from the requirement text plus validated decisions.
6. **Review AC.** One requirement at a time, same card + dialog pattern as stories today: approve / edit / defer / not approved.
7. **Render.** On approval (and whenever a gap changes), the core regenerates the enriched copy of the affected file: source bytes unchanged, one BA block appended inside each requirement's extent. The source file is never opened for writing.
8. **Stay in sync.** `changes check` compares the source (text hash per requirement), the enriched copy (expected hash from the manifest) and gap state. Because the enriched file is a pure function of source + state, a changed source simply re-renders after impact review.

### 3.2 What the user sees in the enriched copy

Source (as delivered by the upstream agent, never touched):

```md
### REQ-012 Password reset
A registered user can request a password reset link by email. The link expires after a limited time.
```

Enriched copy, e.g. `enriched/requirements/auth.md` (requirement text copied byte-for-byte, BA block appended inside its extent):

```md
### REQ-012 Password reset
A registered user can request a password reset link by email. The link expires after a limited time.

<!-- ba:begin key="REQ-012" ac="v2" rendered="sha256:9f1c…" -->
#### Acceptance Criteria
*Approved by Priya Nair (Product Owner), 2026-10-28 14:05 IST · AC v2*
1. Given a registered user, when they request a reset, then an email with a single-use link is sent.
2. Given an unregistered email, when a reset is requested, then the response is identical to the registered case (no account disclosure).
3. Given a link older than the expiry set by GAP-004, when it is opened, then the user sees an "expired" message and can request a new one. *(expiry value not yet decided)*

#### Open Gaps
| Gap | Priority | Status | Question | Potential impact |
|---|---|---|---|---|
| GAP-004 | MEDIUM | OPEN (carried forward) | What is the link expiry? | Criterion 3 cannot state a number |

**Readiness:** NEEDS_CLARIFICATION
<!-- ba:end key="REQ-012" -->
```

Rules for the block: it is fenced by markers; it contains nothing unapproved except the open-gaps table (a factual projection of gap state, not a decision). The enriched file is generated output: it is never read as input and is not meant to be hand-edited (see D2).

---

## 4. Design decisions

Each decision has a recommendation. Items marked **(confirm)** are repeated in section 11.

### D1. Requirement identity without atomic conversion (confirm)
- A **requirement unit** is whatever the upstream document already treats as one requirement: by default a heading whose text carries an ID token (`REQ-012`, `FR-2.1`, `AUTH-004`, matched by a configurable regex), or, if the upstream uses tables, a table row with an ID cell.
- `req_key` = `<relative file path>::<ID>`; a short alias (just `<ID>`) is displayed when unique across the folder. This avoids collisions when two files both contain `REQ-001`.
- If a document has no IDs, fall back to *heading path + ordinal* (the same `key` that `ingest.parse_sections` already computes) and **warn once**. We do not insert IDs into the upstream document.
- The locator settings are stored in `state/workflow.json` (`source.locator`) after one confirmation, so they never change silently.
- A sample file from the upstream agent is the single most useful input for this; see open question Q1.

### D2. Authority, source and enriched copy (confirm)
- **Source** (the supplied folder or file): read-only input. No code path opens it for writing; the existing `SOURCE_PROTECTED` guard stays and is extended so the project and `enriched/` folders can never be inside the source folder.
- **Authoritative state:** `acceptance/json/<key>.json` (AC records) and `state/gap-register.json` (gaps). Unchanged principle from today: JSON is authoritative, Markdown is a rendered view.
- **Enriched copy:** `enriched/<same relative path as the source>`. It is a pure function of *(source text, AC records, gap register)*: the renderer copies the source text unchanged and appends one block per requirement unit. Mirroring the relative path keeps relative links working and makes the pairing obvious (alternative: `name.enriched.md` next to the original; see Q3).
- **Manifest:** `state/enriched-manifest.json` stores, per enriched file, the source text hash and the expected output hash. This is what change detection compares against.
- **Upstream regenerates or edits the source:** handled by ordinary source change detection (nothing in the source was ever ours). After impact review the enriched copy is simply re-rendered. The old "blocks vanished" case does not exist.
- **Someone edits the enriched copy by hand:** the file no longer matches its expected hash. Edits inside a block follow the existing `MANUAL_EDIT` path (explain, apply, becomes the next AC version needing approval, or discard). Edits outside blocks are reported as `ENRICHED_TEXT_EDITED` ("change the source instead; the next render overwrites this"); the previous file is saved to `state/backups/` before any overwrite, so nothing is lost silently.
- **Rejected alternative:** editing the source in place. It risks corrupting the upstream file, creates write races with the upstream agent (worst in Cowork) and makes every enrichment look like a source change.

### D3. Source protection and render safety
- **Source is never written.** Source file hashes are recorded at index time; any later difference is a *source change* and goes through change detection, never a silent overwrite.
- **Enriched files are not valid input.** If a file containing `ba:begin` markers is supplied as source (someone fed an enriched copy back), index refuses it with `ENRICHED_FILE_AS_SOURCE` and says which file.
- **Render invariant, checked after every render:** removing the BA blocks from the generated file yields the source text byte-for-byte (line endings and trailing newline preserved). On failure the previous enriched file is kept and `RENDER_INVARIANT_FAILED` is raised.
- **Atomic and recoverable:** temp file + replace, rolling `.bak`. Because the enriched copy can always be regenerated from source + state, `recover` simply re-renders; no write journal is needed (the gap-closure journal pattern still applies to gap closures).
- **Render guard:** refuse to render while a source change is unreviewed (`CHANGES_UNREVIEWED`), so AC are never rendered against requirement text they were not written for.

### D4. State and output location
Default the project (state) folder to a **sibling** of the requirements folder (`<parent>/ba-output/<name>/`) with `enriched/` inside it, so the upstream agent that scans its own folder never ingests our output and nobody mistakes the enriched copy for the source. `--enriched-dir` can point it elsewhere (e.g. a shared docs folder), but never inside the source folder. One confirmation, as today.

### D5. AC review replaces story approval (confirm granularity)
- Reuse the story approval machinery 1:1: versions, exact-version approval, `--statement`, defer, request-changes, revisit, immutable approved snapshots.
- Granularity: **one requirement at a time** (matches today's story-at-a-time and gives the same audit precision). Optional later: "approve all for this section" for requirements with no open gaps.
- Only **approved** AC are rendered into the enriched copy. Drafts live in `acceptance/candidates/`.
- Open gaps are shown in the block as soon as they exist (they are facts); their *wording* comes from the gap record.

### D6. Blocking policy and readiness replace the baseline
- Keep `gaps.blocks_requirements` unchanged: CRITICAL/HIGH/material gaps always block; MEDIUM/LOW non-material can be carried forward.
- Replace baseline `READY`/`BLOCKED` with a **live** requirement status computed from gaps: `READY`, `READY_WITH_OPEN_GAPS`, `BLOCKED`. No stale-baseline state, no "refresh and confirm baseline" dialog — this removes today's most awkward triage step (`preflight: baseline_stale`).
- `BLOCKED` requirements get no AC; their block shows only the open gaps and "Acceptance criteria withheld until blocking gaps are resolved". (Q4 asks whether to relax this.)
- Per-AC `readiness` (`READY_FOR_DEVELOPMENT` / `NEEDS_CLARIFICATION` / `NOT_READY`) stays, derived from `open_gap_refs` exactly as `stories.readiness_of` does today.

### D7. Legacy 1.x projects (confirm)
Bump `schema_version` to `2.0`. A 1.x project opened by 2.x returns `LEGACY_PROJECT` with a plain-language message; `status` stays read-only. No automatic migration (stories and baselines have no home). Optional later: `import-legacy` that carries over gaps, decisions and audit log only.

### D8. Cowork file bridge
The source is staged in as input and hash-checked (`manifest` drift as today); it is never committed back. What is committed back is the project folder and `enriched/`, using the existing `changed_files` / `removed_files` mechanism, so there is no race with the upstream agent editing its own files. `sync.py` changes are small: add the source files to the staged set and the manifest, and add `enriched/` to the committed set.

### D9. Delegation
With no decomposition step, the heaviest token consumer disappears. Keep `delegate`/`batches` only for gap analysis (batches of requirement units instead of BRD sections) and AC drafting. Remove `requirement-analyst`.

---

## 5. Target architecture

### 5.1 State machine

```
INITIALIZED → SOURCE_INDEXED → GAP_ANALYSIS ⇄ WAITING_FOR_GAP_INPUT
            → AC_DRAFTING → WAITING_FOR_AC_REVIEW → COMPLETED
PAUSED / BLOCKED unchanged (resume only to the saved stage)
Re-open edges as today: later stages return to GAP_ANALYSIS / WAITING_FOR_GAP_INPUT when decisions or requirements change.
```
Removed stages: `BRD_PARSED`, `REQUIREMENTS_READY`, `REQUIREMENTS_BASELINED`, `STORIES_READY`, `WAITING_FOR_STORY_REVIEW`. Gap and AC transition tables are the existing gap and story tables (renamed).

### 5.2 Folder layout

```
<project>/                            (sibling of the requirements folder)
  START-HERE.md  ADR-LOG.md           (generated views, unchanged idea)
  index/        md/requirements-index.md   json/requirements-index.json
  enriched/     <same relative paths as the source>.md      (generated: source text + BA blocks)
  gaps/         open/ deferred/ resolved/  (each md/ + json/)         ← unchanged
  decisions/    md/DEC-*.md  json/decision-log.jsonl                  ← unchanged
  acceptance/   candidates/ approved/ unapproved/ deferred/ archive/  (md/ + json/, keyed by req_key)
  changes/      md/CHG-*.md  json/CHG-*.json  change-register.json    ← kept, reworked
  snapshots/    source/<file>@v<n>.md   ac/<key>.v<n>.json   (the supplied source is also never lost here)
  reports/      workflow-summary.md  traceability-report.md
  state/        workflow.json  gap-register.json  enriched-manifest.json  audit.jsonl  pending/  backups/  inbox/
```
Removed: `extracted/`, `atomic-requirements/`, `baselines/`, `stories/`, `snapshots/brd`, `snapshots/stories`.

### 5.3 Data model changes

**Requirement index entry** (replaces `requirement.schema.json`; derived, rebuilt on every index, never hand-edited)
```json
{"req_key": "requirements/auth.md::REQ-012", "id": "REQ-012", "file": "requirements/auth.md",
 "title": "Password reset", "line_start": 41, "line_end": 52,
 "text_hash": "sha256:…", "material_hash": "sha256:…", "status": "ACTIVE|CHANGED|REMOVED",
 "existing_ac_hint": ["…"], "flags": [{"kind": "POSSIBLE_DUPLICATE", "note": "…"}]}
```
`existing_ac_hint` captures acceptance statements the upstream already wrote, so AC drafting does not contradict them (Q5).

**Gap** (`gap.schema.json`): `impacted_requirement_ids` → `impacted_req_keys` (pattern relaxed to a generic key, minItems 1). Add `evidence_quote` that the core checks against the requirement text (cheap guard replacing the old verbatim-excerpt check on requirements).

**AC record** (replaces `story.schema.json`)
```json
{"req_key": "…::REQ-012", "version": 2,
 "status": "CANDIDATE|IN_REVIEW|CHANGES_REQUESTED|APPROVED|DEFERRED|SUPERSEDED",
 "acceptance_criteria": ["Given … when … then …"],
 "decision_ids": ["DEC-003"],
 "open_gap_refs": [{"gap_id":"GAP-004","potential_impact":"…","readiness_effect":"NEEDS_CLARIFICATION","interim_assumption":null}],
 "readiness": "NEEDS_CLARIFICATION",
 "requirement_hash": "sha256:…", "rendered_hash": "sha256:…",
 "impact_review": null, "approval": {…}, "deferral": null, "review_history": […]}
```
Dropped from the story: `user_story {role, capability, value}`, `title`, `dependencies`, `baseline_version`, `source_references`. Kept: approval, review history, impact review, readiness, assumptions.

**workflow_state.schema.json**: `source` becomes `{root, files[], locator, fingerprints{}}`; drop `baseline_version`, `pending_story_ids`; add `pending_ac_keys`.
**change.schema.json**: subject kinds `source_file`, `requirement`, `ac`, `ac_block`; drop `baseline`, `story`, `story_removal`, `reapproval` item kinds (replaced by `ac`, `ac_removal`, `ac_reapproval`).
**decision.schema.json**: requirement references re-keyed the same way.

---

## 6. Change plan by component

### 6.1 New module — `docmodel.py`
- `locate(text, locator)` → requirement units with extents (reuse `ingest.parse_sections` for heading structure and code-fence handling).
- `render_block(ac, gaps)`; `render_enriched(source_text, units, blocks)` → generated text (source bytes + blocks appended at each unit's end, before the next unit of the same or higher level).
- `strip_blocks(text)` → used only for the render invariant and for manual-edit detection on the enriched copy, never on the source.
- `write_enriched(path, text)` — atomic write, `.bak`, invariant check, manifest update.
- Table-based requirement lists: the block goes directly after the table, one block per row key (Q1 decides whether this is needed).

### 6.2 Source ingest (`ingest.py`, `project.py`, `snapshots.py`, `discover.py`)
| File | Change |
|---|---|
| `ingest.py` | Markdown only. Delete `_docx_text`, `.txt` numbered-heading heuristic, cp1252 fallback. Keep `normalize_text`, `material_normalize`, `parse_sections`, `scan_injection`. Add multi-file `extract_all(dir)` and `pristine()` variants of fingerprints. Remove `outline_batches` by sections; add batches by requirement units. |
| `convert.py` | Delete. |
| `project.py` | `create(root, requirements_path, ...)`: accept a folder or a file; build `state.source`; DIRS/SPLIT_DIRS per 5.2; STAGES + `STAGE_TRANSITIONS` per 5.1; `SOURCE_PROTECTED` kept and extended (output never inside source); drop baseline manifest accessors; add `index()` / `requirements()` reading `index/json`; `_migrate_*` helpers removed (legacy → `LEGACY_PROJECT`). |
| `snapshots.py` | Per-file source text snapshots (`source/<file>@vN`) with text and material hashes; AC snapshots handled by `actrack.py`. |
| `discover.py` / `find` | Look for a requirements folder (≥1 `.md` with ID-bearing headings) as well as existing projects. |

### 6.3 Requirements and gaps (`reqs.py` → `reqindex.py`, `gaps.py`, `conflicts.py`)
| Item | Change |
|---|---|
| `reqs.py` | Replace with `reqindex.py`: `index()`, `list`, `show`, `pairs` (keep `pairs_report`, now cross-file). Delete `add_requirements`, `update_requirement`, `confirm_requirement`, `remove_requirement`, `commit_requirements`, `source_accept`'s per-requirement reassessment. |
| `gaps.py` | Validate `impacted_req_keys` against the index; `apply_closure` flags **AC records** (impact_review) and re-renders affected blocks instead of flagging stories and staling a baseline; `blocks_requirements`, carry-forward, defer, reopen, policy, owner, followup, conflict gate unchanged. Add `evidence_quote` check. |
| `conflicts.py` | Unchanged (works on text). |
| Gap sync hook | After every gap-mutating command (`gap answer/resolve/defer/carry-forward/revisit/reopen`, `gaps add`) run `docs.render_affected(keys)` → re-render only the enriched files whose open-gap table changed. Gated by the render guard (D3). |

### 6.4 AC records (`stories.py`, `storytrack.py`, `present.py`, `render.py`)
| Old | New | Notes |
|---|---|---|
| `stories.py` (34 KB) | `acs.py` | Same functions renamed: `add_acs`, `next_ac`, `approve`, `edit`, `defer`, `revisit`, `keep`, `confirm_unaffected`, `request_changes`, `triage`, `preflight`. Remove role/capability/value checks and the `READY` baseline map; replace with live requirement status. Keep Given/When/Then check, duplicate check, `unresolved_decisions` completeness check (now `open_gap_refs`). |
| `storytrack.py` | `actrack.py` | Material hash = criteria + open-gap refs + assumptions; approval/readiness hash unchanged; immutable `…v<n>.json` copies. |
| `present.story_view/story_card/story_options` | `ac_view` etc. | Card shows the requirement text, criteria, open gaps, readiness, decision-to-apply. |
| `render.story_md`, `templates/user_story.md` | `render.ac_block`, `templates/ac_block.md` | Plus `templates/requirement.md` deleted. |
| Preflight | Simplified | `baseline_stale` / `no_baseline` / `baseline_unconfirmed` removed; keep `source_impact_review`. |

### 6.5 Change detection (`changes.py`, 62 KB — the riskiest change)
| Current | New |
|---|---|
| `detect_source`: one BRD, section diff by title path | Per file: file added / removed; per requirement (by `req_key`): added / removed / changed (compare `material_hash`); formatting-only auto-accepted as today. The source is the only input, so no block-stripping is involved. |
| `_source_impact`: requirements via `source_section_ids`, stories via requirement links, baseline | Impact = AC record (approved or not), open gaps and confirmed decisions on that `req_key`. No baseline. |
| `detect_stories` / `_assess_story`: approved story `.md` vs snapshot | `detect_enriched`: each enriched file vs the manifest's expected hash; classes `MANUAL_EDIT` (inside a block), `ENRICHED_TEXT_EDITED` (outside blocks, backed up and re-rendered, no review), `FILE_DELETED` (silent re-render), `UNTRACKED_BLOCK`, `FORMATTING_ONLY`. |
| `apply` | Requirement changed → AC marked `impact_review`, related gaps `needs_impact_review`, stage reopened to gap analysis if a gap-bearing requirement changed. Manual block edit → approved version restored from snapshot, enriched file re-rendered, the edit kept as `observed-CHG-nnn`, next version is a `CANDIDATE` (unchanged behaviour). |
| `guard` | Same refusal list with `ac add|next|approve|edit` replacing `story …` and `baseline …`; plus **every render**. |
| Reconciliation items | `requirement`, `gap`, `ac`, `ac_reapproval`, `new_requirement`, `ac_removal`. |

Plan this module's rework as its own phase with its own tests (the 52 existing change-detection tests are the main safety net).

### 6.6 Checks, status, overview (`checks.py`, `overview.py`, `adr.py`, `sync.py`)
- `next_action`: new ladder — `recover` → unreviewed changes → `SOURCE_INDEXED` ("run gap analysis") → gaps waiting → AC drafting needed → AC awaiting review → complete.
- `completion`: complete when every non-blocked requirement has an approved AC (or an explicit deferral), no unreviewed changes, and blocks in documents match state. `PARTIAL` reasons: blocked requirements, deferred/unapproved AC, `NOT_READY` AC, carried-forward gaps (listed), requirements without AC.
- `validate`: add document checks — source hash drift, enriched file vs manifest, orphan markers, render invariant, duplicate keys, ID collisions; remove baseline checks.
- `trace`: requirement → gaps → decisions → AC → document location (file + line).
- `overview.py` (`START-HERE.md`): new folder table and counts.
- `adr.py`: only the wording of requirement references.
- `sync.py`: include the source files (input only) in `manifest` and `stage_list`; add `enriched/` to the committed set (D8).

### 6.7 CLI (`cli.py`, `helptext.py`)

| Before | After |
|---|---|
| `init --brd <file> [--copy-source]` | `init --requirements <folder\|file> [--recursive] [--locator …] [--name]` |
| `source check|accept|section|outline|history` | `source index` (rebuild index, no write), `source history`; `source check` kept as alias of `changes check` |
| `req add|commit|pairs|list|show|update|confirm|remove` | `req list|show|pairs` only |
| `baseline create|confirm|show` | removed |
| `story add|next|show|approve|edit|defer|revisit|keep|history|restore|revert-edit|request-changes|confirm-unaffected|triage|gaps` | `ac add|next|show|approve|edit|defer|revisit|keep|history|restore|revert-edit|request-changes|confirm-unaffected|triage|gaps` |
| — | `doc sync [--key K]` (re-render blocks), `doc check` (read-only drift report) |
| `convert` | removed |
| `gaps`, `gap`, `owner`, `policy`, `changes`, `status`, `validate`, `trace`, `recover`, `complete`, `report`, `manifest`, `bridge`, `find`, `files`, `fingerprint`, `help` | unchanged names |

`helptext.py` is generated from argparse, so most of it follows automatically.

### 6.8 Plugin surface

**Commands** (`commands/`)
| File | Change |
|---|---|
| `start.md` | Step 1 becomes explicit: **ask where the requirements are** (folder or file) unless the argument or `find` already answers it. Then confirm the state folder (sibling default, D4) and the locator once. Then index and go straight to gap analysis, ask for the decision owner. Remove all BRD / conversion wording. |
| `status.md`, `review.md`, `changes.md` | Wording; router targets. |
| `triage-gaps.md` | Unchanged except the closing line (no stale-baseline mention). |
| `triage-stories.md` | Rename to `triage-ac.md` (`/ba-agent:triage-ac`); remove preflight baseline step; same loop. |

**Skill** — `skills/ba-workflow/SKILL.md`
- Router table: replace rows for `BRD_PARSED`, `REQUIREMENTS_READY`, baseline, stories with `SOURCE_INDEXED` → gap analysis, `AC_DRAFTING` → AC drafting, `WAITING_FOR_AC_REVIEW` → AC review.
- Non-negotiable 6 stays in spirit and is widened: *"The supplied requirements are read-only source. Write only inside the confirmed project folder; the enriched copy under `enriched/` is generated by the core and never hand-edited."* Rule 4 mentions AC instead of stories/baseline. Rule 5 (untrusted text) stays and covers every requirement file.
- Context discipline item 3: `req show` replaces `source section`.

**Stage references** (`references/stages/`)
| File | Action |
|---|---|
| `brd-ingestion.md` | Rewrite → `requirements-intake.md` (ask where, locate, confirm state folder and locator, index, report warnings). |
| `atomic-requirements.md` | Delete. |
| `gap-analysis.md`, `gap-analysis/taxonomy.md` | Edit: input is the indexed unit; `impacted_req_keys`; `evidence_quote` rule. Taxonomy unchanged. |
| `gap-resolution.md` + 4 branch files | Minimal edits (wording, no baseline in §4 "next gap"). |
| `requirements-baseline.md` | Delete. |
| `user-story-generation.md` | Rewrite → `acceptance-criteria.md` (inputs, rules, open-gap handling, `open_gap_refs`, the carried-forward-resolved loop). |
| `story-review.md`, `story-review/triage.md` | Rewrite → `ac-review.md`, `ac-review/triage.md`. |
| `change-detection.md` | Rewrite for 6.5. |
| `quality-traceability.md`, `resume-workflow.md` | Edit wording and ladder. |
| `../cowork-sync.md`, `../artifact-integrity.md`, `../human-approval.md`, `../ba-principles.md`, `../save-checkpoint.md`, `../choice-prompts.md` | Edit wording; `artifact-integrity.md` gets the new layout and the managed-block rules. |

**Agents** (`agents/`)
| Agent | Action |
|---|---|
| `requirement-analyst` | Delete. |
| `gap-analyst` | Edit: input is requirement keys; reads `req show`; output unchanged. |
| `story-builder` | Replace → `ac-writer`: drafts AC from approved decisions and requirement text only, `open_gap_refs` mandatory, never invents values. |
| `quality-reviewer` | Edit: review AC and change impact; add check "does any AC contradict the requirement's own `existing_ac_hint`?" |

**Schemas / templates** — section 5.3. Templates: delete `requirement.md`, `user_story.md`; add `ac_block.md`; edit `gap.md`, `decision.md`.

**Metadata and docs** — `plugin.json` → `2.0.0` (and fix the author), `package_plugin.py` zip name, README, USER_GUIDE, ARCHITECTURE (state machines, carry-forward, change detection, context engineering), COMPATIBILITY, CHANGELOG (breaking-change entry), TEST_REPORT regenerated after the last phase. Add a `LICENSE` decision (README already says none is chosen).

---

## 7. Implementation phases

Each phase ends with the suite green for everything still in scope, so regressions surface where they were introduced. Sizes are relative effort (S ≈ 1–2 days, M ≈ 3–5, L ≈ 6–10) and are estimates, not commitments.

| Phase | Scope | Exit criteria | Size |
|---|---|---|---|
| **P0 — Inputs and decisions** | Get 2–3 real upstream requirement files (single and multi-file); answer Q1–Q8; freeze locator rules; create fixtures under `tests/fixtures/requirements/` | Fixtures committed; locator spec agreed | S |
| **P1 — Source model** | `docmodel.locate`; multi-file `ingest`; `reqindex.py`; `Project.create/open` for folder input; new stages and DIRS; delete `convert.py`/DOCX; `snapshots` per file; `find`; `init`, `source index`, `req list/show/pairs` | Index of fixtures is stable across CRLF/LF, re-indexing is idempotent, ID collisions across files handled; `test_convert` removed, `test_ingest_safety` rewritten | M |
| **P2 — Gaps on the index** | Re-key gaps and decisions; schema patterns; `evidence_quote`; remove `req add/commit/update/...`; adapt `checks.next_action` up to gap resolution; temporarily no-op the baseline/story parts | `test_gap_actions`, `test_gap_folders`, `test_carry_forward` (gap-only parts), `test_triage` (gap part) green | M |
| **P3 — Render engine** | `render_block`, `render_enriched`, `write_enriched` with the D3 invariant, `.bak`, line endings, manifest; `doc sync/check`; gap render hook | Property tests: stripping blocks from any enriched file gives the source bytes; source files never modified (hash check); render is idempotent; a crash at each write point leaves the previous enriched file intact | S–M |
| **P4 — AC records and review** | `acs.py`, `actrack.py`, `ac_view`, `ac_block` template, `ac` CLI group, live requirement status, `triage-ac`, `ac-writer`; approval renders the block into the enriched copy; gap resolution flags AC and re-renders | End-to-end on fixtures: gap → resolve → AC → approve → block in file; carried-forward → resolved → AC updated → re-approved | L |
| **P5 — Change detection and checks** | Rework `changes.py` (6.5), `checks.validate/trace/completion/status_text`, `overview`, `sync`/Cowork guard; remove baseline everywhere | `test_change_detection`, `test_traceability`, `test_state_transitions`, `test_resume`, `test_cowork` migrated and green; upstream-regenerates-file and human-edits-block scenarios covered | L |
| **P6 — Plugin surface and docs** | Commands, SKILL router, stage references, agents, templates, README/USER_GUIDE/ARCHITECTURE/COMPATIBILITY/CHANGELOG, `plugin.json` 2.0.0, `package_plugin.py` | `claude plugin validate .` passes; `test_context` budgets and dangling-reference checks pass | M |
| **P7 — Hardening and pilot** | Run on a real upstream folder in Claude Code and, separately, in Cowork; update `measure_efficiency.py`; regenerate `TEST_REPORT.md`; tag release | Pilot of ≥ 1 real requirements folder end-to-end; no `SOURCE_TEXT_ALTERED`; token numbers recorded | M |

Rough total: about 4–7 engineering weeks for one person who knows the codebase, dominated by P4 and P5. P1–P3 can ship internally as a "gap-only" 2.0-alpha that already proves the render safety.

---

## 8. Test plan

### 8.1 Existing suites (static count of `def test_`)

| Suite | Tests | Fate |
|---|---|---|
| `test_convert` | 7 | Delete |
| `test_ingest_safety` | 15 | Rewrite for multi-file Markdown; keep injection-scan and path-containment cases |
| `test_schemas` | 14 | Update for new schemas |
| `test_state_transitions` | 22 | Update stage table; gap and AC tables carry over |
| `test_gap_actions` / `test_gap_folders` / `test_adr` | 19 / 10 / 4 | Mostly unchanged (re-keyed IDs) |
| `test_carry_forward` | 44 | Gap parts unchanged; story parts → AC |
| `test_triage` | 36 | Gap triage unchanged; story triage → AC triage |
| `test_change_detection` | 52 (39 + 13 in the edge-case file) | Heavy rewrite (6.5) |
| `test_traceability` | 18 | Rewrite targets (no baseline/story) |
| `test_resume` | 17 | New `next_action` ladder |
| `test_cowork` | 37 | Add requirement files to staging and commit-back guard |
| `test_context` | 28 | Skill/stage budgets and dangling reference checks re-baselined |
| `test_layout` | 9 | New folder tree |
| `test_e2e` | 3 | Rewrite on fixtures |

### 8.2 New suites
- `test_docmodel`: locator on ID headings, nested headings, tables, code fences containing `#`, no-ID fallback, duplicate IDs, CRLF/LF, BOM.
- `test_render`: stripping blocks from the enriched file equals the source bytes (property-style over random AC/gap states), idempotence, marker tampering in the enriched file (nested, unbalanced, duplicated), crash points, read-only output folder, Windows paths, output-inside-source refused, source with markers refused.
- `test_multifile`: key collisions, file added/removed/renamed, non-requirement `.md` (README, index files) ignored or flagged.
- `test_source_change`: upstream rewrites or edits a source file → impact review, then re-render; an approved AC on a changed requirement is flagged.
- `test_ac_flow`: draft → review → approve → block; edit → new version → old stays in force; gap resolved → AC flagged → updated → re-approved.
- `test_cowork_enriched`: source is staged read-only and hash-checked; only the project folder and `enriched/` appear in `changed_files`.

---

## 9. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Enriched copy is mistaken for the source, or drifts from it | Medium | Separate `enriched/` folder (never inside the source folder); generated header comment in every enriched file; render invariant; `ENRICHED_FILE_AS_SOURCE` refusal; `ENRICHED_TEXT_EDITED` warning |
| Upstream agent regenerates or edits the source | Low | Ordinary source change detection and impact review, then re-render; nothing of ours lives in the source |
| Source and output get out of step in Cowork | Low | Source is input-only and hash-checked; only `enriched/` and state are committed back, so there is no write race |
| Locator misses requirements in the real upstream format | High | P0 sample files; locator config confirmed once; `validate` reports headings without IDs and files with zero units |
| ID scheme changes between upstream runs | Medium | Rename shows as REMOVED + ADDED in the change record; offer "map old key → new key" in `changes apply` (decision recorded in audit) |
| `changes.py` rework regresses a guarantee | High | Own phase; keep the 52 change-detection tests as the oracle; port scenario by scenario |
| Context budget grows (documents are large) | Medium | `req show` returns one unit; AC cards include only the requirement text and gaps; `measure_efficiency` rerun in P7 |
| Loss of the baseline gate removes a human checkpoint | Medium | AC approval and gap triage are the gates; optional `complete` snapshot of the enriched files into `snapshots/source/` gives a point-in-time record |
| Breaking change for existing 1.x projects | Low–Medium | D7: explicit `LEGACY_PROJECT` message; 1.10.2 remains installable for old projects |

---

## 10. Out of scope (as requested)
- Any input format other than Markdown (no DOCX/PDF/RTF/XLSX/TXT).
- Generating or maintaining the upstream requirements.
- Converting requirements into atomic requirements.
- User stories (replaced by AC under each requirement).

---

## 11. Open questions (answers needed before P1)

| # | Question | Why it matters | My default if unanswered |
|---|---|---|---|
| Q1 | What does an upstream requirement file look like — heading per requirement with an ID, bullet list, or table? Can you share one sample (single file and a multi-file folder)? | Defines the locator and the block insertion point | Heading with an ID token; table rows supported later |
| Q2 | ~~Edit upstream files in place?~~ **Decided:** no. The source stays untouched and a generated enriched copy is produced. | D2/D3 | Done |
| Q3 | Where should the enriched copies go and how are they named: `enriched/<same relative path>` inside the project folder (recommended), or `<name>.enriched.md` beside each source file? | Beside-the-source naming is handier to find but puts generated files in the folder the upstream agent scans | `enriched/` in the project folder, same relative paths |
| Q4 | May AC be drafted for a `BLOCKED` requirement (marked provisional), or should AC be withheld until blocking gaps are resolved? | Today's strict rule is "no stories for blocked requirements" | Withhold |
| Q5 | Does the upstream agent already write acceptance criteria or "open questions"? If so, should we treat them as input, keep them, or replace them? | Avoid duplicated or contradictory AC | Read as `existing_ac_hint`, never edit |
| Q6 | AC review granularity: per requirement (default) or batch approve per section when no gaps are open? | Review effort vs audit precision | Per requirement; batch later |
| Q7 | Legacy 1.x projects: leave unsupported, or build `import-legacy` for gaps, decisions and audit log? | Scope of P2 | Unsupported, revisit after pilot |
| Q8 | Is a `complete`-time freeze/stamp of the enriched documents wanted as the replacement for baseline confirmation? | Keeps a visible "approved set" | Snapshot only, no extra approval step |
| Q9 | Should the first-run question also ask for the decision owner, or keep asking right before the first gap as today? | Fewer interruptions vs rule 1 | Keep as today |

---

## Appendix A — Reuse map (old → new)

| Old concept | New concept | Code reuse |
|---|---|---|
| Atomic requirement `REQ-nnn` | Indexed requirement unit `req_key` | New (`reqindex`, `docmodel`) |
| BRD section `S1.2` | Requirement extent in a file | `ingest.parse_sections` |
| BRD fingerprint / snapshot | Per-file source text hash / snapshot | `ingest`, `snapshots` |
| Story `US-nnn` | AC record per `req_key` | `stories`/`storytrack` renamed and trimmed |
| `unresolved_decisions` | `open_gap_refs` | `stories._unresolved`, `readiness_of` |
| Baseline `READY/BLOCKED` | Live requirement status | `gaps.blocks_requirements` |
| `story approve/edit/defer/...` | `ac approve/edit/defer/...` | Same state table, same snapshots |
| Story `.md` manual edit detection | Enriched-copy manual edit detection (expected-hash comparison) | `storytrack.parse_md/md_diff` pattern |
| BRD change record | Requirement / file change record | `changes.py` lifecycle |
| `triage-stories` | `triage-ac` | `present.py`, `story-review/triage.md` pattern |
| Gap closure journal | (unchanged; no write-back journal needed because the enriched copy is regenerable) | `state/pending/` + `recover` |
