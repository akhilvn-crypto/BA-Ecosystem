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

Under the strict default policy any unresolved gap blocks stories for the requirements it affects (CRITICAL/HIGH always). Non-blocking (MEDIUM/LOW) gaps can be carried forward only after the human accepts that via `policy set`.
