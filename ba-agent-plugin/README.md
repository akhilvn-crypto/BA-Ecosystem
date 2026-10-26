# ba-agent — Interactive Business Analyst plugin for Claude

Point it at the **requirements your upstream agent already wrote** (a folder of Markdown files, or one file). It finds the **gaps**, resolves them **with a named decision owner**, and writes **acceptance criteria that a human explicitly approved** — into a generated **enriched copy** of each requirement file. The files you supplied are never modified. Works in Claude Code (CLI) and, via upload, Claude Cowork (see [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md) for what is and is not verified).

## What you get

| Stage | What happens | Human control |
|---|---|---|
| Intake | Asks where the requirements are (folder or one `.md`), indexes every requirement read-only: a heading that carries an ID (`### REQ-012 Title`), or a table row with an ID. The exact bytes of each file are kept; nothing is decomposed or rewritten | You point at the requirements and confirm the project folder |
| Gap analysis | Evidence-based gaps (10-category taxonomy, justified priority, one question each, a verbatim quote from the requirement) + a cross-requirement pass across all files | — |
| Gap resolution | Asks **who the decision owner is (name + role) before the first gap**; presents gaps highest priority first — Critical/High/material gaps with Other / Explain This Question / Answer Later / Defer This Gap, Medium/Low gaps with Suggested Answer (evidence only) / Other / Explain This Question / Carry Forward; validates every answer against the requirements, decisions and business rules; closes a gap only with a validated, conflict-free answer and an IST audit record; a carried-forward gap stays OPEN until resolved | You answer, confirm, carry forward, answer later, defer (with a reason), reopen |
| Acceptance criteria | Given/When/Then criteria per requirement, only where its gaps do not block it, linked to decisions and to any open gaps (with the potential impact and a readiness flag: ready / needs clarification / not ready) | You approve / edit / defer each |
| Enriched copy | `enriched/<same path as the source>`: the source text byte for byte plus, under each requirement, its approved criteria, an open-gaps table and readiness. Regenerated after every approval and gap change; never an input | — |
| Change control | Accepted requirement files and approved criteria are snapshotted and hashed. Upstream edits (added / modified / removed requirements) are detected, explained with their impact **before** anything is updated, and classified material vs formatting-only; a hand edit of an enriched copy is kept, never lost; approval history is preserved | You decide whether to apply a change |

Everything is saved the moment it exists — gaps into `gaps/open/`, answered gaps moved to `gaps/resolved/`, criteria into the folder for their status — and `START-HERE.md` at the top of the project folder always shows what is where. Everything is resumable: state, decisions and the audit trail live in the project folder, not in the chat.

What the enriched copy adds under a requirement:

```md
### REQ-012 Password reset
A registered user can request a password reset link by email.

<!-- ba:begin key="auth.md::REQ-012" ac="v2" rendered="sha256:…" -->
#### Acceptance Criteria
*Approved by Priya Nair (Product Owner), 2026-10-28 14:05 IST · AC v2*
1. Given a registered user, when they request a reset, then an email with a single-use link is sent.

#### Open Gaps
| Gap | Priority | Status | Question | Potential impact |
|---|---|---|---|---|
| GAP-004 | MEDIUM | OPEN (carried forward) | What is the link expiry? | Criterion 3 cannot state a number |

**Readiness:** NEEDS_CLARIFICATION
<!-- ba:end key="auth.md::REQ-012" -->
```

## Built to stay cheap in tokens

Each stage reads only what it needs. One skill (`ba-workflow`) is advertised; stage guides are short loops loaded on demand from `references/stages/`; the core builds the choice dialog and card for each gap or criteria record so the model relays them instead of writing them (a gap whose dialog says everything is sent as the dialog alone, queues as one summary line); CLI results are compact and omit empty fields; `req show` returns one requirement; large requirement sets are batched out to optional helper agents. Details: [Architecture → Context engineering](docs/ARCHITECTURE.md).

## Install and run

**Claude Code CLI**: `claude --plugin-dir /path/to/ba-agent-plugin` (one session), then `/ba-agent:start path/to/requirements`.
Persistent (changes your Claude config): from the folder that contains `.claude-plugin/marketplace.json` run `claude plugin marketplace add ./` then `claude plugin install ba-agent@ba-agents`.

**Claude Cowork** (not verified in this environment): build the upload file with `python scripts/package_plugin.py` → `dist/ba-agent-2.0.0.zip`, then *Customize → Plugins → Add → Upload plugin*. Commands appear as `/ba-agent:start` etc. Cowork's local-folder and script-execution behaviour is not documented well enough to claim support — see the compatibility report.

Commands: `/ba-agent:start [requirements folder or project folder]` · `/ba-agent:status` · `/ba-agent:review` · `/ba-agent:triage-gaps` · `/ba-agent:triage-ac` · `/ba-agent:changes`. You normally only need `start`.

## Upgrading from 1.x

2.0 is a breaking redesign: the BRD → atomic requirements → baseline → user stories pipeline is gone. A 1.x project opens as `LEGACY_PROJECT` (read the message from `status`); it is not migrated. Keep using ba-agent 1.10.2 for it, or start a new project on the requirements folder. See [CHANGELOG.md](CHANGELOG.md).

## Requirements

Python 3.9+ on the machine that runs the plugin (standard library only — **nothing is installed**; developed and tested on Python 3.14). No database, no network access, no MCP server, no external services.

## Develop / test

```
python -m unittest discover -s tests -t . -v      # run from this folder (about three minutes)
claude plugin validate .                            # manifest, skills, agents, commands
```

More: [User guide](docs/USER_GUIDE.md) · [Architecture](docs/ARCHITECTURE.md) · [Compatibility](docs/COMPATIBILITY.md) · [Test report](docs/TEST_REPORT.md) · [Changelog](CHANGELOG.md)

## Limits you should know

Markdown requirements only (no DOCX, PDF, RTF, XLSX or TXT; ask the upstream agent for a Markdown export). A renamed requirement ID shows as one removed and one added requirement; there is no old-key → new-key mapping. A decision that contradicts a requirement is recorded, but the document is never edited: the upstream owner must change it. The conflict pre-check is a keyword heuristic that *prompts* review; the semantic check is performed by Claude and the human. The plugin does not choose a software licence for you — add a `LICENSE` before publishing.
