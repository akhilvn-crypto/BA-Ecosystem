# ba-agent — Interactive Business Analyst plugin for Claude

Turn a Business Requirements Document (BRD) into **traceable atomic requirements**, resolve the gaps **with a named decision owner**, freeze a **versioned baseline**, and store **only the user stories a human explicitly approved** — all as plain files in a project folder you choose. Works in Claude Code (CLI) and, via upload, Claude Cowork (see [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md) for what is and is not verified).

## What you get

| Stage | What happens | Human control |
|---|---|---|
| Intake | BRD (`.md`, `.txt`, `.docx`) is fingerprinted and outlined; the original is never modified | You pick the BRD and the output folder |
| Requirements | Atomic `REQ-###` records with verbatim source quotes and section references | — |
| Gap analysis | Evidence-based gaps (10-category taxonomy, justified priority, one question each) + cross-requirement pass | — |
| Gap resolution | Asks **who the decision owner is (name + role) before the first gap**; checks every answer for completeness and conflicts; closes gaps with an IST audit record | You answer, confirm, defer, reopen |
| Baseline | Versioned `baseline-vN` with decisions and outstanding gaps | You confirm it |
| Stories | Candidate stories with Given/When/Then criteria, linked to requirements and decisions | You approve / edit / defer each |
| Output | `stories/approved/` contains only explicitly approved versions; edits create new versions that need re-approval | — |

Everything is resumable: state, decisions and the audit trail live in the project folder, not in the chat.

## Install and run

**Claude Code CLI** (tested): `claude --plugin-dir /path/to/ba-agent-plugin` (one session), then `/ba-agent:start path/to/BRD.docx`.
Persistent (not run here, as it changes your Claude config): from the folder that contains `.claude-plugin/marketplace.json` (`D:\BA-Agents`) run `claude plugin marketplace add ./` then `claude plugin install ba-agent@ba-agents`.

**Claude Cowork** (not verified in this environment): build the upload file with `python scripts/package_plugin.py` → `dist/ba-agent-1.1.0.zip`, then *Customize → Plugins → Add → Upload plugin*. Commands appear as `/ba-agent:start` etc. Cowork's local-folder and script-execution behaviour is not documented well enough to claim support — see the compatibility report.

Commands: `/ba-agent:start [BRD or project folder]` · `/ba-agent:status` · `/ba-agent:review`. You normally only need `start`.

## Requirements

Python 3.9+ on the machine that runs the plugin (standard library only — **nothing is installed**; developed and tested on Python 3.14). No database, no network access, no MCP server, no external services.

## Develop / test

```
python -m unittest discover -s tests -t . -v      # run from this folder; 80+ tests
claude plugin validate .                            # manifest, skills, agents, commands
```

More: [User guide](docs/USER_GUIDE.md) · [Architecture](docs/ARCHITECTURE.md) · [Compatibility](docs/COMPATIBILITY.md) · [Test report](docs/TEST_REPORT.md) · [Changelog](CHANGELOG.md)

## Limits you should know

PDF input is not supported without installing a library (export to `.docx`/`.md`/`.txt`). Images inside DOCX are not read. The conflict pre-check is a keyword heuristic that *prompts* review; the semantic check is performed by Claude and the human. The plugin does not choose a software licence for you — add a `LICENSE` before publishing.
