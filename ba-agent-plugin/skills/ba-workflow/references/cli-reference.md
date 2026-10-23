# Core CLI reference

`python "${CLAUDE_PLUGIN_ROOT}/scripts/ba.py" [--root DIR] [--args-file FILE.json] <group> <action> [options]`
`--args-file`: JSON object whose keys are option names (`-` or `_`), overriding CLI values. Use it for any text with quotes/newlines/unicode. Files for `--file` are JSON arrays/objects (see skills).

| Command | Purpose / key options |
|---|---|
| `init --brd FILE --root DIR [--name N] [--copy-source]` | Create project, fingerprint + outline the BRD (md/txt/docx). Stage → `BRD_PARSED`. `--copy-source` copies (never moves) the BRD into `source/`. |
| `status [--json]` | Stage, counts, blockers, validation, **NEXT** action. |
| `validate` · `trace [--write]` · `recover` · `complete` · `report` | Integrity checks · coverage/orphans/dupes · roll forward interrupted work · finish or report PARTIAL · write summary. |
| `source section SID [--allow-changed]` · `source outline` | Fetch ONE section's text (context-efficient) / list sections. |
| `source check` · `source accept --by-name --by-role --statement` | Detect BRD change → impact assessment · accept the reassessed change. |
| `req add --file reqs.json` | Items: `title, statement, type, source_section_ids[], source_excerpt (verbatim), actors, preconditions, outcomes, constraints, dependencies, acceptance_info, assumptions, flags`. Returns IDs + advisory duplicate/conflict flags. |
| `req commit` · `req pairs` · `req list [--status]` · `req show ID` | Validate decomposition → `REQUIREMENTS_READY` · cross-requirement candidates. |
| `req update ID --file changes.json --reason R --cause decision\|source-change\|correction [--decision-id D] [--by-name --by-role]` | New requirement version (old kept). |
| `req confirm ID --note N` · `req remove ID --reason R --by-name --by-role` | After BRD change: still valid / dropped (kept as `REMOVED`). |
| `owner set --name N --role R [--gap GAP-ID] [--statement S]` · `owner unknown --choice pause\|analysis-only` · `owner show` | Decision owner (default or per-gap). |
| `gaps add --file gaps.json` | Items: `category` (taxonomy), `title, description, evidence, why_it_matters, question, impacted_requirement_ids[], priority CRITICAL\|HIGH\|MEDIUM\|LOW, priority_rationale, material?` |
| `gaps commit --cross-pass-done` · `gaps next [--limit 3]` · `gaps list [--status S]` | Finish analysis · present gaps (**needs owner**) · list. |
| `gap show ID` · `gap answer ID --response T [--respondent-name --respondent-role]` | Store raw answer; returns `candidate_conflicts`. |
| `gap followup ID --answer-id A1 --note N` · `gap conflict ID --answer-id A1 --with REQ-003,DEC-001 --description D` | Incomplete answer · record a conflict. |
| `gap resolve ID --answer-id A1 --interpretation T --closed-by-name N --closed-by-role R --closure-summary S --confirmation "<closer's words>" [--conflict-status PASSED\|CHANGE_CONFIRMED] [--reviewed IDS] [--conflicting IDS --change-by-name --change-by-role --change-statement T --requirement-updates FILE] [--owner-confirmation T]` | The only way to `RESOLVED`. Writes decision + IST audit event atomically (journaled). |
| `gap defer ID --reason R [--owner O] [--revisit-by DATE] [--by-name --by-role]` · `gap skip ID` · `gap revisit ID` · `gap reopen ID --reason R --by-name --by-role` | Lifecycle. Deferred ≠ resolved. |
| `policy set --carry-forward true\|false --by-name --by-role --statement T` | Allow non-blocking unresolved gaps to be carried (explicit human acceptance). |
| `baseline create [--summary S]` · `baseline confirm --version N --by-name --by-role --statement T` · `baseline show [--version N]` | Versioned baseline (`PROPOSED` → human `APPROVED`). Unchanged inputs are reused. |
| `story add --file stories.json` | Items: `title, user_story{role,capability,value}, acceptance_criteria[] ("Given…when…then…"), requirement_ids[], dependencies, assumptions, notes{business_rules,validation_exceptions,non_functional}`. |
| `story next [--id US-001] [--deferred] [--batch N]` · `story show ID` | Present for review (marks `IN_REVIEW`). |
| `story approve ID --version N --statement "<human words>" [--approver-name --approver-role]` | Exact version; writes to `stories/approved/`. |
| `story edit ID --file changes.json --reason R` | New version → needs new approval. Editable: title, user_story, acceptance_criteria, requirement_ids, dependencies, assumptions, notes. |
| `story defer ID --version N [--reason R]` · `story revisit ID` · `story request-changes ID --version N --note N` · `story confirm-unaffected ID --statement S` | Review lifecycle / clear an impact flag. |

Common `error.code`s: `OWNER_REQUIRED`, `OWNER_UNKNOWN`, `IDENTITY_REQUIRED`, `MISSING_METADATA`, `NOT_PRESENTED`, `ANSWER_INCOMPLETE`, `CONFLICT_REVIEW_REQUIRED`, `CONFLICT_UNRESOLVED`, `AWAITING_OWNER_CONFIRMATION`, `REQUIREMENT_UPDATE_REQUIRED`, `CLOSURE_IN_PROGRESS` (run `recover`), `EXCERPT_NOT_FOUND`, `UNSUPPORTED_FORMAT`, `SOURCE_CHANGED`, `IMPACT_REVIEW_PENDING`, `BASELINE_STALE`, `REQUIREMENT_BLOCKED`, `ACCEPTANCE_FORMAT`, `VERSION_MISMATCH`, `APPROVAL_EVIDENCE_REQUIRED`, `OPEN_GAPS`, `WRITE_FAILED`, `CLOCK_UNRELIABLE`, `PATH_ESCAPE`, `SOURCE_PROTECTED`.
