# BA Agents — Interactive Business Analyst plugin for Claude

This repo is a Claude Code **plugin marketplace** (`ba-agents`). It ships one plugin, **`ba-agent`**, which takes the requirements your upstream agent already wrote (a folder of Markdown files), finds the gaps, resolves them with a named decision owner and adds **human-approved acceptance criteria** to a generated, enriched copy of each requirement file.

## Purpose

Business analysts and project managers lose time chasing open questions in requirements that look finished. `ba-agent` does the legwork and keeps you in control of every decision:

1. **Reads the requirements** (`.md` files, or one file) read-only. **Your files are never modified**; each requirement is located by the ID it already carries.
2. **Finds gaps** (10-category taxonomy, prioritised, one question each, quoting the requirement).
3. **Resolves gaps with a named decision owner**. Every closure is recorded with who decided, their role and an IST timestamp.
4. **Drafts acceptance criteria** (Given/When/Then) for each requirement whose gaps do not block it. Only the ones you approve are saved to `acceptance/approved/`.
5. **Writes an enriched copy** of every requirement file under `enriched/`: the same text, plus under each requirement its approved criteria, its open gaps and a readiness flag.
6. **Detects changes** to the requirements and to approved criteria, explains their impact before anything is updated, and never discards a manual edit.

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

Upgrading from 1.x: 2.0 replaces the BRD → baseline → user-story pipeline; 1.x projects open as `LEGACY_PROJECT` and are not migrated (see the changelog).

## How to use it (BA / Project Manager)

| Command | Use it to |
|---|---|
| `/ba-agent:start [requirements folder or file, or project folder]` | Start a new project from a folder of requirement `.md` files, or resume an existing one |
| `/ba-agent:status [folder]` | See the stage, progress, blockers and next step |
| `/ba-agent:review [folder]` | Carry on where you left off (answers gaps, reviews criteria, handles an impact review) |
| `/ba-agent:triage-gaps [folder]` | List every OPEN and DEFERRED gap, then answer, explain, defer, carry forward or keep each one |
| `/ba-agent:triage-ac [folder]` | List every unapproved acceptance-criteria record (pending, not approved, deferred), then approve, edit or defer each one |
| `/ba-agent:changes [folder]` | Check for requirement, criteria or enriched-copy changes, see what is affected, and decide whether to apply them |

Typical flow:

1. Run `/ba-agent:start D:\docs\my-requirements` and confirm the project folder (proposed next to the requirements, in `ba-output/`).
2. Give the **decision owner's name and role** (for example, "Priya Nair — Product Owner"). This is asked before the first gap.
3. Answer gaps one at a time (highest priority first). Every gap lets you type an answer or instruction in **Other** or pick **Explain This Question**. For **Critical/High/security-or-compliance** gaps you can also pick **Answer Later** or **Defer This Gap** (a reason is required). Before the first **Medium/Low** gap you are asked once whether to go through each or **carry them all forward** in one step. For **Medium/Low** gaps you can instead use the **Suggested Answer** (only offered when the document supports one) or **Carry Forward**: the gap stays open and visible, criteria are still written, and each affected requirement's criteria name the gap, its possible impact and are marked as needing clarification until you answer.
4. Review acceptance criteria one requirement at a time: **approve**, **edit** (creates a new version that needs re-approval) or **defer**.
5. Open the enriched copies in `<project>/enriched/` (the machine-readable criteria records are in `acceptance/approved/json/`).

Every stage saves its files into the project folder as it finishes (readable copies in each folder's `md/` subfolder, records in `json/`), and Claude tells you where before it asks for your review. Nothing has to be copied.

If the upstream agent changes the requirements, run `/ba-agent:changes <project folder>` (or `/ba-agent:start <project folder>` again). The change is explained first: what changed, and which gaps, decisions and criteria it touches. Nothing moves until you apply it, and only the affected items are flagged for review. Formatting-only edits are accepted automatically; a changed word or number is always treated as material. Earlier decisions stay on record.

**For project managers:** use `/ba-agent:status` for a quick read on open gaps and pending reviews. `reports/workflow-summary.md` and `reports/traceability-report.md` give a shareable view of progress and requirement-to-criteria coverage.

## Limits

- Markdown requirements only; ask the upstream agent for a Markdown export of anything else.
- A decision that contradicts a requirement is recorded, but your files are never edited: the requirement's owner must change it upstream.
- Claude Cowork use is unverified (see the compatibility doc).

## Documentation

All in [`ba-agent-plugin/`](ba-agent-plugin/): [User guide](ba-agent-plugin/docs/USER_GUIDE.md) · [Architecture](ba-agent-plugin/docs/ARCHITECTURE.md) · [Compatibility](ba-agent-plugin/docs/COMPATIBILITY.md) · [Test report](ba-agent-plugin/docs/TEST_REPORT.md) · [Changelog](ba-agent-plugin/CHANGELOG.md)

## Develop

```
cd ba-agent-plugin
python -m unittest discover -s tests -t . -v
claude plugin validate .
```
