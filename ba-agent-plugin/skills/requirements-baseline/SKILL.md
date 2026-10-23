---
name: requirements-baseline
description: Consolidate atomic requirements, confirmed decisions and outstanding gaps into a versioned requirements baseline and obtain explicit human confirmation. Use when gap resolution is finished or paused, or when a baseline is stale after changes.
---

# Requirements baseline

1. `baseline create [--summary "<what changed>"]`.
   - Needs: gap analysis committed, no pending BRD-change review, no interrupted closure. Otherwise follow the error.
   - Unchanged inputs ⇒ `reused: true` (nothing regenerated). Otherwise a new `baseline-vN` (`PROPOSED`) with: requirements (each `READY` or `BLOCKED by GAP-…`), confirmed decisions, outstanding gaps with status and impact, source fingerprint, change summary vs. the previous version. Previous versions are kept.
2. **Strict default policy:** any unresolved gap (deferred, open, waiting, reopened) blocks stories for the requirements it affects; CRITICAL/HIGH always. Say clearly which requirements are blocked and why. Offer: resolve/revisit those gaps, or — only for MEDIUM/LOW — let the human accept carrying them forward (`policy set --carry-forward true --by-name … --by-role … --statement "<their words>"`, then `baseline create` again).
3. Show the user a short summary (ready vs. blocked, new decisions, changes since previous). Open `baselines/baseline-vN.md` if they want detail.
4. Ask for explicit confirmation with an `AskUserQuestion` (`Confirm vN`, `Not yet`; short plain-text question, summary stays in chat — see `../ba-workflow/references/choice-prompts.md`), then get name and role in chat: *"Confirm baseline vN as the working baseline? (name and role please)"*. Then `baseline confirm --version N --by-name … --by-role … --statement "<their words>"`. Do not confirm on their behalf.
5. Confirming flags existing stories whose requirements changed or became blocked for impact review.

Next: `user-story-generation`.
