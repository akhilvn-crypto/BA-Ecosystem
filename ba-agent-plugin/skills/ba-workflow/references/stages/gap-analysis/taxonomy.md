# Gap taxonomy and priority rubric

Categories (use these exact names): **Functional behavior** · **Business rules** · **Data requirements** · **Validation and exception handling** · **Integration** · **Security and access** · **Non-functional requirements** · **Workflow/state transitions** · **Acceptance criteria and testability** · **Contradiction, duplication, or dependency**

Prompts per category
- Functional behavior: triggers, actors, main/alternate flows, outputs, what "done" looks like.
- Business rules: thresholds, rates, eligibility, calculations, precedence, effective dates.
- Data requirements: fields, types, limits, formats, retention, ownership, reference data.
- Validation and exception handling: invalid input, limits exceeded, failures, retries, error messages.
- Integration: systems, protocols, direction, volumes, failure handling, SLAs.
- Security and access: roles, permissions, authentication, audit, privacy, encryption. (Default `material: true`.)
- Non-functional requirements: measurable targets (latency, availability, scale), devices, accessibility, compliance.
- Workflow/state transitions: states, transitions, who can trigger them, timeouts, reversibility.
- Acceptance criteria and testability: vague words ("quickly", "user-friendly", "etc."), no observable outcome.
- Contradiction, duplication, or dependency: two statements that cannot both hold; the same rule stated twice; hidden prerequisites.

Priority (justify in `priority_rationale`; never assign randomly)
- **CRITICAL** — prevents safe/correct interpretation or materially changes the business outcome.
- **HIGH** — likely changes design, security, compliance or acceptance.
- **MEDIUM** — meaningful uncertainty that can be settled before the affected work is finalised.
- **LOW** — minor clarification, limited impact.

Risk ≠ gap: a complete requirement can still be risky; an incomplete one is not automatically high-risk.

Under the strict default policy any unresolved gap blocks stories for the requirements it affects (CRITICAL/HIGH always). A MEDIUM/LOW gap stops blocking only when the human chooses **Carry Forward** for that gap (it stays OPEN, stories are written and flagged) or accepts the project-wide non-blocking policy via `policy set`; material (scope/policy/security/compliance, critical business-rule) gaps always block while unresolved and cannot be carried forward. Answer Later and Defer This Gap leave a gap unresolved — they never lift the block.

Rate honestly: set `material: true` and CRITICAL/HIGH when a wrong guess could breach a security control, a regulation or a financial/legal business rule. Use MEDIUM/LOW only when the work can safely proceed on a flagged, provisional basis.
