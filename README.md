# BA Agents — Interactive Business Analyst plugin for Claude

This repo is a Claude Code **plugin marketplace** (`ba-agents`). It ships one plugin, **`ba-agent`**, which turns a Business Requirements Document (BRD) into traceable requirements, resolved gaps and **human-approved user stories**.

## Purpose

Business analysts and project managers lose time turning BRDs into clean requirements and chasing open questions. `ba-agent` does the legwork and keeps you in control of every decision:

1. **Reads the BRD** (`.docx`, `.md`, `.txt`). The original is never modified.
2. **Extracts atomic requirements** (`REQ-###`) with verbatim source quotes.
3. **Finds gaps** (10-category taxonomy, prioritised, one question each).
4. **Resolves gaps with a named decision owner**. Every closure is recorded with who decided, their role and an IST timestamp.
5. **Freezes a versioned baseline** (`baseline-vN`) once you confirm it.
6. **Drafts user stories** with Given/When/Then criteria. Only the ones you approve are saved to `stories/approved/`.

Nothing is approved, resolved or deferred without your explicit decision. All state lives as plain files in a folder you choose, so work is fully resumable.

## Install

Prerequisites: [Claude Code](https://claude.com/claude-code) and Python 3.9+ (standard library only; nothing else is installed).

```
/plugin marketplace add akhilvn-crypto/BA-Ecosystem
/plugin install ba-agent@ba-agents
```

Or from a terminal:

```
claude plugin marketplace add akhilvn-crypto/BA-Ecosystem
claude plugin install ba-agent@ba-agents
```

Try it for one session without installing: `claude --plugin-dir ./ba-agent-plugin`

## Update

```
/plugin marketplace update ba-agents
/plugin update ba-agent@ba-agents
```

Restart Claude Code (or run `/reload-plugins`) afterwards. Updates are detected by the `version` in `ba-agent-plugin/.claude-plugin/plugin.json`, so maintainers must bump it (and add a `CHANGELOG.md` entry) with every release.

## How to use it (BA / Project Manager)

| Command | Use it to |
|---|---|
| `/ba-agent:start [BRD path or project folder]` | Start a new project from a BRD, or resume an existing one |
| `/ba-agent:status [folder]` | See the stage, progress, blockers and next step |
| `/ba-agent:review [folder]` | Continue answering gaps, reviewing stories, revisiting deferred items |

Typical flow:

1. Run `/ba-agent:start D:\docs\MyProject-BRD.docx` and confirm the output folder.
2. Give the **decision owner's name and role** (for example, "Priya Nair — Product Owner"). This is asked before the first gap.
3. Answer gaps (up to three at a time), or defer them. Confirm each closure.
4. Confirm the **baseline**.
5. Review stories one by one: **approve**, **edit** (creates a new version that needs re-approval) or **defer**.
6. Take the approved stories from `<project>/stories/approved/`.

If the BRD changes, run `/ba-agent:start <project folder>` again. Only the affected requirements are flagged for review, and earlier decisions stay on record.

**For project managers:** use `/ba-agent:status` for a quick read on open gaps and pending reviews. `reports/workflow-summary.md` and `reports/traceability-report.md` give a shareable view of progress and requirement-to-story coverage.

## Limits

- PDF input is not supported. Export the BRD to `.docx`, `.md` or `.txt`.
- Images inside DOCX files are not read.
- Claude Cowork use is unverified (see the compatibility doc).

## Documentation

All in [`ba-agent-plugin/`](ba-agent-plugin/): [User guide](ba-agent-plugin/docs/USER_GUIDE.md) · [Architecture](ba-agent-plugin/docs/ARCHITECTURE.md) · [Compatibility](ba-agent-plugin/docs/COMPATIBILITY.md) · [Test report](ba-agent-plugin/docs/TEST_REPORT.md) · [Changelog](ba-agent-plugin/CHANGELOG.md)

## Develop

```
cd ba-agent-plugin
python -m unittest discover -s tests -t . -v
claude plugin validate .
```
