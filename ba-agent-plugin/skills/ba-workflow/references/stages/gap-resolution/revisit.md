# Revisit a carried-forward or deferred gap and update what depends on it

Carried-forward gaps stay visible in `status`, `gaps next` (`carried_forward_open`), the baseline and every linked story. Offer a revisit when the human mentions new information, when all other gaps are done, and at the end of the session.

1. `gaps next --carried` (or `gap revisit GAP-00x`) asks the gap again as a normal gap; the earlier carry-forward/deferral stays in its history and the audit log. While asked it blocks like any open gap until answered or carried forward again.
2. Answer and resolve it as in `validate-and-resolve.md`.
3. `gap resolve` lists `stories_to_update` (every story that named the gap, approved ones included, is flagged) and marks the baseline stale. Then, in this order:
   - Requirements: if the decision changes one, it went through `--requirement-updates` (new version, stories flagged); otherwise the decision is just linked.
   - `baseline create`, then the human's `baseline confirm` (`requirements-baseline`). `/ba-agent:triage-stories` does this as its first step.
   - Each flagged story: `story edit` (see `user-story-generation` → *When a carried-forward gap is resolved*). The new version goes through `story-review`; the approved earlier version stays in force until then. A story that still names a resolved gap cannot be cleared with `confirm-unaffected`.
