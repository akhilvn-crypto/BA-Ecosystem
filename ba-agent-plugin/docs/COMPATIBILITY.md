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
| Skills | **Verified**: all 10 listed by a live session | Documented to load; **not tested** | Skills are surface-neutral |
| Slash commands | **Verified**: `/ba-agent:start`, `:status`, `:review` registered and invoked in a live headless session | Documented `/plugin:command`; **not tested** | — |
| Subagents | Load (validated); optional — workflow does not depend on them | Documented to load; not tested | Treated as optimisation only |
| Hooks | None shipped | None shipped | Correctness never depends on hooks |
| Local folder access | Verified (reads BRD, writes project folder) | **Unverified** | `start` asks the model to check access and offer CLI/desktop-folder fallback instead of pretending |
| Script execution | Verified (Python via Bash/PowerShell tool; user permission prompt) | **Unverified** (sandbox/desktop constraints undocumented) | If scripts cannot run, the core cannot run: the plugin says so and offers CLI. No silent degraded mode |
| Persistent state | Verified (files in the project folder) | Unverified | State is shared across sessions/devices only if both can open the same folder |
| Installation / import | `--plugin-dir` verified; local marketplace manifest validated (`claude plugin validate .`); persistent `plugin install` **not run** (would change the user's Claude config) | Upload documented; **not tested** | Exact steps in README |

## Surface-specific notes

- Claude Code on Windows may expose both a Bash and a PowerShell tool; the plugin's commands are plain `python …` and work in either. Headless (`-p`) runs need `--allowedTools` to include the shell tool actually used (observed: PowerShell on this machine).
- Not tested at all: macOS/Linux, Python < 3.14, claude.ai chat (no local file access), Cowork.
