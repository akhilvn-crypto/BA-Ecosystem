# Revisit a carried-forward or deferred gap and update what depends on it

Carried-forward gaps stay visible in `status`, `gaps next` (`carried_forward_open`), the enriched copy and every linked criteria record. Offer a revisit when the human mentions new information, when all other gaps are done, and at the end of the session.

1. `gaps next --carried` (or `gap revisit GAP-00x`) asks the gap again as a normal gap; the earlier carry-forward/deferral stays in its history and the audit log. While asked it blocks like any open gap until answered or carried forward again.
2. Answer and resolve it as in `validate-and-resolve.md`.
3. `gap resolve` lists `acs_to_update` (every criteria record that named the gap, approved ones included, is flagged). A confirmed change that contradicts a requirement is recorded as `upstream_change_required`: the document is never edited here, so tell the user the owner of the requirement must change it. Then, for each flagged record:
   - `ac edit` (see `acceptance-criteria` → *When a carried-forward gap is resolved*). The new version goes through `ac-review`; the approved earlier version stays in force, and stays in the enriched copy, until then. Criteria that still name a resolved gap cannot be cleared with `confirm-unaffected`.
   - `/ba-agent:triage-ac` walks them one by one.
