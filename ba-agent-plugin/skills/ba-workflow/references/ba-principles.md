# BA principles (shared reference)

- **Source facts vs. assumptions vs. decisions vs. open questions** are different things. A requirement states what the document says. Anything you infer is listed under `assumptions` and is never presented as confirmed. A decision exists only after a human answer has been validated and closed. Anything else is an open gap.
- **The upstream requirements are the source of truth and stay untouched.** You do not split, merge, reword or renumber them; you ask about what is missing and write acceptance criteria that follow from the text and the decisions. If a decision contradicts the text, the document must change upstream; say so.
- **Traceable by default:** every gap names its requirement keys (and quotes the text it is about); every criteria record links to one requirement and its decision IDs; the enriched copy shows both next to the requirement.
- **A gap is only worth raising** if the missing/unclear information could change implementation, acceptance, risk or business outcome. Do not ask what the document already states, including acceptance statements it already makes. One focused question per gap; priority must be justified.
- **Small increments.** Persist after each step. Never hold the only copy of anything in the conversation.
- **Say what you could not do** (unreadable file, no requirement IDs found, a surface limitation). Never claim a check passed that you did not run.
