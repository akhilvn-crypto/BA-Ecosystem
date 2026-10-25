# Compatibility report

**Checked:** 2026-10-22 · **Claude Code CLI:** 2.1.308 (Windows 10, Python 3.14.8)

## Sources (official, fetched 2026-10-22)

- https://code.claude.com/docs/en/plugins-reference — manifest, layout, `${CLAUDE_PLUGIN_ROOT}`, caching
- https://code.claude.com/docs/en/plugins — creating plugins, `--plugin-dir`, marketplaces
- https://claude.com/docs/plugins/overview — plugins in claude.ai / Cowork (upload, components)
- https://claude.com/docs/plugins/platform-support — what loads where
- plus the linked pages `…/plugins/loading`, `…/install`, `…/components`, `…/build`

## What the documentation says (relied on)

- Manifest `.claude-plugin/plugin.json` (`name` required); `commands/*.md`, `skills/<name>/SKILL.md`, `agents/*.md` at the plugin root; `scripts/` is a convention only; a root `CLAUDE.md` is not loaded (none shipped).
- Commands `/<plugin>:<file>`; skills `/<plugin>:<dir>`; agents `<plugin>:<name>`.
- `${CLAUDE_PLUGIN_ROOT}` is substituted in skill/command/agent bodies; marketplace plugins are copied to a cache (so the plugin must be self-contained — it is); `--plugin-dir` plugins load in place. `${CLAUDE_PLUGIN_ROOT}` is **not** in the Bash tool's environment, which is why instructions embed it in the command text.
- Cowork: *Customize → Plugins → Add → Upload plugin* (`.zip`/`.plugin`, `plugin.json` at root or one top-level dir, ≤ 5,000 files / 200 MB). Skills, commands, agents and hooks load; top-level `bin/` blocks installation (not used).
- **Not documented** (so not claimed): Cowork local-folder access semantics; whether `${CLAUDE_PLUGIN_ROOT}`/`${CLAUDE_SKILL_DIR}` are substituted or scripts can run in a Cowork session; Cowork plugin caching; `.plugin` vs `.zip` differences; full SKILL.md frontmatter list.

## Matrix

| Capability | Claude Code CLI | Claude Cowork | Status / action |
|---|---|---|---|
| Plugin manifest | **Verified**: `claude plugin validate` passes (also `--strict`); plugin loads via `--plugin-dir` (name `ba-agent`) | Format per docs; **not tested** (no Cowork access here) | Zip built by `scripts/package_plugin.py`; upload route documented |
| Skills | **Verified**: the 10 skills that existed at 1.4.0 were listed by a live session; `change-detection` (11th, 1.5.0) is validated by `claude plugin validate` but **not yet seen in a live session** | Documented to load; **not tested** | Skills are surface-neutral |
| Slash commands | **Verified**: `/ba-agent:start`, `:status`, `:review` registered and invoked in a live headless session (`:triage-gaps` and `:triage-stories` added in 1.4.0 — files validated, core commands tested; **not yet invoked in a live session**) | Documented `/plugin:command`; **not tested** | — |
| Subagents | Load (validated); optional — workflow does not depend on them | Documented to load; not tested | Treated as optimisation only |
| Hooks | None shipped | None shipped | Correctness never depends on hooks |
| Local folder access | Verified (reads BRD, writes project folder) | **Unverified** | `start` asks the model to check access and offer CLI/desktop-folder fallback instead of pretending |
| Script execution | Verified (Python via Bash/PowerShell tool; user permission prompt) | **Unverified** (sandbox/desktop constraints undocumented) | If scripts cannot run, the core cannot run: the plugin says so and offers CLI. No silent degraded mode |
| Persistent state | Verified (files in the project folder) | Unverified | State is shared across sessions/devices only if both can open the same folder |
| Installation / import | `--plugin-dir` verified; local marketplace manifest validated (`claude plugin validate .`); persistent `plugin install` **not run** (would change the user's Claude config) | Upload documented; **not tested** | Exact steps in README |

## Surface-specific notes

- Gap and story dialogs use `AskUserQuestion` where it exists (Claude Code). Where it is unavailable (for example Cowork) the skills fall back to the same choices as a short numbered list in chat — for Medium/Low gaps Suggested Answer / Other / Explain This Question / Carry Forward, for Critical/High/material gaps Other / Explain This Question / Answer Later / Defer This Gap. The core commands, the recorded data and the blocking rules are identical on both surfaces; only the presentation differs. The Cowork path is **not verified** here.

- Claude Code on Windows may expose both a Bash and a PowerShell tool; the plugin's commands are plain `python …` and work in either. Headless (`-p`) runs need `--allowedTools` to include the shell tool actually used (observed: PowerShell on this machine).
- **Change detection (1.5.0)** is part of the core script plus the `change-detection` skill and `/ba-agent:changes`, so it needs nothing the rest of the plugin does not already need. It deliberately does **not** rely on hooks (none are shipped): the checks run inside the core before guarded commands and are called explicitly by `/ba-agent:start`, `/ba-agent:review` and `/ba-agent:changes`, which is the same in Claude Code and Cowork. Snapshots and change records are ordinary files in the project folder. Verified: the core behaviour (automated tests, real CLI subprocess). Not verified: a live Claude session invoking `/ba-agent:changes`, and anything in Cowork.
- **Cowork readiness review (1.9.0, by inspection, not by running Cowork):** everything the plugin needs is a plain file write inside the folder the user attached plus `python` from the standard library, so the open risks are Cowork's, not the plugin's: whether `${CLAUDE_PLUGIN_ROOT}` is substituted (the workflow skill falls back to locating `scripts/ba.py` two folders above the skill), whether `python` or `python3` is the name in the sandbox (the skill tries both), and whether `AskUserQuestion` exists (the numbered-list fallback is built into every dialog). The source was checked to parse as Python 3.9 and to use no 3.10+ runtime features; it was run only on 3.14. A PDF BRD no longer needs a library: Claude converts it to Markdown itself. **Still unverified:** an actual Cowork session end to end.
- Not tested at all: macOS/Linux, Python < 3.14 (syntax-checked for 3.9 only), claude.ai chat (no local file access), Cowork.
