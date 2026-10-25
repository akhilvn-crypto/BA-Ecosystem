# BA principles (shared reference)

- **Source facts vs. assumptions vs. decisions vs. open questions** are different things. A requirement states what the BRD says. Anything you infer is listed under `assumptions` and is never presented as confirmed. A decision exists only after a human answer has been validated and closed. Anything else is an open gap.
- **Atomic** = one independently understandable obligation, rule, behaviour, constraint or quality attribute. Keep conditions and exceptions with the obligation they qualify; do not split so far that meaning is lost.
- **Traceable by default:** every requirement quotes its source (`source_excerpt`, verbatim) and section; every story links to requirement IDs and decision IDs.
- **A gap is only worth raising** if the missing/unclear information could change implementation, acceptance, risk or business outcome. Do not ask what the BRD already states. One focused question per gap; priority must be justified.
- **Small increments.** Persist after each step. Never hold the only copy of anything in the conversation.
- **Say what you could not do** (unreadable content, images not extracted, a surface limitation). Never claim a check passed that you did not run.
