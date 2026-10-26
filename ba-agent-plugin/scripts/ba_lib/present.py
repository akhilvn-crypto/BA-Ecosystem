"""Presentation layer: the chat card and the choice dialog for ONE gap or acceptance-criteria record, built by the core.

The model relays what is built here instead of composing it, so every presentation is the same size and shape, costs
no composition tokens and cannot drift from the rules (which options exist, when `Approve` is withheld, ...).

`dialog` is a ready `AskUserQuestion` payload (one question; the tool adds the free-text “Other” box itself).
`inline: true` means the dialog already carries everything the human needs, so **no separate chat message is sent**.
`card` is the compact markdown for when it does not (long history, a requirement's acceptance criteria). Without the dialog
tool, `card` (when present) + `fallback` replace it; for an inline gap `fallback` is the whole message.
`fallback` is built only on request (`--fallback`, after the dialog tool failed): it repeats the dialog text, so by default
it is not sent.
"""
from __future__ import annotations

import re

DIALOG_QUESTION_MAX = 480  # characters of plain text a dialog can carry before it is cramped
OTHER_HINT = "Type an answer or instruction in “Other”, or pick an option."


def short(text, n: int) -> str:
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def _opt(label: str, description: str) -> dict:
    return {"label": label, "description": description}


def _fallback(options: list[dict], typed: str) -> str:
    items = [f"{i} {o['label']}" for i, o in enumerate(options, 1)] + [f"{len(options) + 1} {typed}"]
    return "Reply with: " + " · ".join(items)


def _day(iso: str | None) -> str:
    return (iso or "")[:10] or "—"


# ---------------------------------------------------------------- gaps

def gap_options(g: dict, carry_ok: bool, blocker_why: str | None, suggested: dict | None) -> list[dict]:
    if g["status"] == "DEFERRED":
        return [_opt("Explain This Question", "Explain it to me in plain words with an example"),
                _opt("Keep Deferred", "Leave it deferred; nothing changes")]
    if g["status"] == "OPEN" and g.get("carry_forward"):
        return [_opt("Explain This Question", "Explain it to me in plain words with an example"),
                _opt("Keep Carried Forward", "Leave it carried forward; nothing changes")]
    if not carry_ok:
        return [_opt("Explain This Question", "Explain it to me in plain words with an example"),
                _opt("Answer Later", "Keep this gap open and ask me again later"),
                _opt("Defer This Gap", "Park this gap with a reason; it stays on the outstanding list")]
    opts = []
    if suggested:
        opts.append(_opt("Suggested Answer", short(f"Use: {suggested.get('answer')} ({suggested.get('source')})", 150)))
    opts.append(_opt("Explain This Question", "Explain it to me in plain words with an example"))
    opts.append(_opt("Carry Forward", "Keep this gap open, continue to the acceptance criteria, and flag them until it is answered"))
    return opts


def gap_view(g: dict, ctx: dict) -> dict:
    """`ctx`: reqmap (key → requirement), blocks_ac, carry_ok, blocker_why, left, ac_keys, fallback."""
    reqmap = ctx["reqmap"]
    reqs = [reqmap[r] for r in g["impacted_req_keys"] if r in reqmap]
    flags = ["blocks acceptance criteria"] if ctx["blocks_ac"] else []
    if g.get("material"):
        flags.append("sensitive")
    if g["status"] == "DEFERRED":
        flags.append("deferred")
    elif g.get("carry_forward"):
        flags.append("carried forward")
    head = f"[{g['priority']}" + (f" · {', '.join(flags)}" if flags else "") + "]"
    affects = ", ".join(f"{r['requirement_id']} {short(r['title'], 40)}" for r in reqs) or ", ".join(g["impacted_req_keys"])
    suggested = g.get("suggested_answer")
    options = gap_options(g, ctx["carry_ok"], ctx["blocker_why"], suggested)

    extra = bool(g["answers"]) or any(c["status"] == "OPEN" for c in g["conflicts"]) or bool(g.get("deferral")) \
        or bool(g.get("carry_forward")) or g.get("needs_impact_review")
    why = short(g["why_it_matters"], 130)
    ev = short(g["evidence"], 130)
    q = (f"{head} {g['gap_id']} ({ctx['left']} left): {short(g['question'], 230)}\n"
         f"Why: {why}\nSource: {ev}\nAffects: {affects}\n{OTHER_HINT}")
    truncated = len(g["question"]) > 230 or len(g["why_it_matters"]) > 130 or len(g["evidence"]) > 130
    inline = not extra and not truncated and len(q) <= DIALOG_QUESTION_MAX

    card = [f"**{g['gap_id']} · {g['priority']} · {g['category']}**" + (f" — {', '.join(flags)}" if flags else ""),
            f"**Question:** {g['question']}", f"**Evidence:** {g['evidence']}", f"**Why it matters:** {g['why_it_matters']}"]
    card.append("**Affects:** " + ("; ".join(f"{r['requirement_id']} {r['title']} — “{short(r['statement'], 160)}” ({r['source_reference']})"
                                             for r in reqs) or affects))
    if ctx["ac_keys"]:
        card.append("**Acceptance criteria already written for:** " + ", ".join(reqmap[k]["requirement_id"] if k in reqmap else k
                                                                           for k in ctx["ac_keys"]))
    if suggested:
        card.append(f"**Suggestion** from {suggested.get('source')}: {suggested.get('answer')} — only an answer if you choose it.")
    if ctx["carry_ok"] and g["status"] == "WAITING_FOR_ANSWER":
        card.append("You can carry this forward: it stays open and visible, acceptance criteria are still written and flagged until you answer.")
    elif ctx["blocker_why"] and g["status"] == "WAITING_FOR_ANSWER":
        card.append(f"Cannot be carried forward: {ctx['blocker_why']}.")
    d = g.get("deferral")
    if g["status"] == "DEFERRED" and d:
        card.append(f"**Deferred** {_day(d.get('at_ist'))} by {d.get('by_name')} ({d.get('by_role')}): {d.get('reason')}"
                    + (f" · revisit by {d['revisit_by']}" if d.get("revisit_by") else "") + (f" · waiting on {d['owner']}" if d.get("owner") else ""))
    cf = g.get("carry_forward")
    if cf:
        card.append(f"**Carried forward** {_day(cf.get('at_ist'))} by {cf.get('by_name')} ({cf.get('by_role')})"
                    + (f": {cf['reason']}" if cf.get("reason") else "") + " — still OPEN, not resolved.")
    for a in g["answers"]:
        card.append(f"**Earlier answer {a['answer_id']}:** “{short(a['response'], 200)}”"
                    + (f" — {a.get('assessment')}: {short(a.get('assessment_note'), 160)}" if a.get("assessment") else ""))
    for c in g["conflicts"]:
        if c["status"] == "OPEN":
            card.append(f"**Open conflict:** {short(c.get('description'), 240)}")
    if g.get("needs_impact_review"):
        card.append("**This gap needs an impact review after a change in the requirements** — check the question still stands.")
    dialog = {"header": g["gap_id"], "question": q, "options": options, "multiSelect": False}
    reply = _fallback(options, "Type an answer or instruction") if ctx.get("fallback") else None
    if inline:  # the dialog text already says everything: no card, and without the tool the question itself is the message
        return {"inline": True, "dialog": dialog, **({"fallback": f"{q}\n{reply}"} if reply else {})}
    return {"inline": False, "card": "\n".join(card), "dialog": dialog, **({"fallback": reply} if reply else {})}


# ------------------------------------------------------- acceptance criteria

def blocker_tags(blockers: list[str]) -> list[str]:
    """One or two words per blocker for the dialog; the full wording stays on the card."""
    out = []
    for b in blockers:
        if "source change" in b:
            t = "source change awaiting review"
        elif "impact review" in b:
            t = "impact review pending"
        elif "requirement text changed" in b:
            t = "requirement changed"
        elif "unresolved gap" in b:
            t = "open " + b.split("gap(s): ", 1)[-1]
        else:
            t = short(b, 40)
        if t not in out:
            out.append(t)
    return out


def ac_options(a: dict, blockers: list[str], flagged: bool, cause: str | None, bucket: str) -> list[dict]:
    v = a["version"]
    approve = _opt(f"Approve v{v}", "Saves this exact version to acceptance/approved/ and adds it to the enriched copy")
    if not blockers:
        if bucket == "deferred":
            return [approve, _opt("Not approved", "Saves it to acceptance/unapproved/, never to approved/"),
                    _opt("Keep deferred", "Leave it in acceptance/deferred/; it is listed again next time")]
        if bucket == "unapproved":
            return [_opt(f"Approve v{v}", "Reconsider and approve this exact version"),
                    _opt("Defer", "Parks it in acceptance/deferred/ to revisit later"),
                    _opt("Keep as is", "Leave it in acceptance/unapproved/; nothing changes")]
        return [approve, _opt("Defer", "Parks it in acceptance/deferred/ to revisit later"),
                _opt("Not approved", "Saves it to acceptance/unapproved/, never to approved/")]
    keep = _opt("Keep deferred", "Leave it in acceptance/deferred/; it is listed again next time") if bucket == "deferred" \
        else _opt("Keep as is", "Nothing changes; it is listed again next time")
    out = []
    if flagged or any("requirement text changed" in b for b in blockers):
        out.append(_opt("Update criteria", "Apply the decision and create the next version, which comes back here for approval"))
        if flagged and cause != "gap-resolved":
            out.append(_opt("No change needed", "Confirm the criteria are unaffected (records your words)"))
    if bucket != "deferred":
        out.append(_opt("Defer", "Parks it in acceptance/deferred/ to revisit later"))
    out.append(keep)
    return out[:4]


def ac_card(a: dict, ctx: dict) -> str:
    notes = a.get("notes") or {}
    lines = [f"**{ctx['label']} · {short(a.get('requirement_title') or '', 70)} · AC v{a['version']}** ({a['status'].replace('_', ' ').title()}"
             + (f" · {a['readiness'].replace('_', ' ').title()}" if a.get("readiness") else "") + ")"]
    if ctx.get("req_text"):
        lines.append(f"**Requirement** ({ctx.get('source_ref') or ''}): “{short(ctx['req_text'], 700)}”")
    lines.append("**Acceptance criteria**")
    lines += [f"{i}. {c}" for i, c in enumerate(a["acceptance_criteria"], 1)]
    if a["decision_ids"]:
        lines.append("**Decisions applied:** " + ", ".join(a["decision_ids"]))
    for u in a.get("open_gap_refs") or []:
        lines.append(f"**Open {u['gap_id']}** ({u.get('priority', '?')}{', carried forward' if u.get('carried_forward') else ''}) — "
                     f"{short(u.get('question'), 140)} · impact: {short(u['potential_impact'], 200)}"
                     + (f" · working assumption, NOT approved: {short(u['interim_assumption'], 140)}" if u.get("interim_assumption") else ""))
    extra_live = [g for g in ctx["live_gaps"] if g not in {u["gap_id"] for u in a.get("open_gap_refs") or []}]
    if extra_live:
        lines.append(f"**Open gaps not declared in the criteria:** {', '.join(extra_live)}")
    for label, key in (("Business rules", "business_rules"), ("Validation/exceptions", "validation_exceptions"), ("Non-functional", "non_functional")):
        if notes.get(key):
            lines.append(f"**{label}:** {short(notes[key], 200)}")
    if a.get("assumptions"):
        lines.append("**Assumptions:** " + "; ".join(short(x, 120) for x in a["assumptions"]))
    ir = a.get("impact_review") or {}
    if ir.get("pending"):
        lines.append(f"**Flagged for impact review:** {ir.get('reason')}")
    for u in ctx["update_ctx"]:
        lines.append(f"**Decision to apply — {u['decision_id']} ({u['gap_id']}):** {u['interpretation']}")
    if a["status"] == "DEFERRED" and a.get("deferral"):
        lines.append(f"**Deferred** {_day(a['deferral'].get('at_ist'))}: {a['deferral'].get('reason') or 'no reason given'}")
    if ctx["blockers"]:
        lines.append("**Cannot be approved yet:** " + "; ".join(ctx["blockers"]))
    return "\n".join(lines)


def ac_view(a: dict, ctx: dict) -> dict:
    """`ctx`: label, req_text, source_ref, blockers, bucket, live_gaps, update_ctx, position ('2 of 6' or None), fallback."""
    ir = a.get("impact_review") or {}
    flagged = bool(ir.get("pending"))
    options = ac_options(a, ctx["blockers"], flagged, ir.get("cause"), ctx["bucket"])
    can = not ctx["blockers"]
    q = (f"{ctx['label']} AC v{a['version']}" + (f" ({ctx['position']})" if ctx.get("position") else "") + ": "
         + ("approve it, suggest changes, or choose below? Type suggestions in “Other”."
            if can else f"cannot be approved yet ({'; '.join(blocker_tags(ctx['blockers']))}). Pick an option, or type edit suggestions in “Other”."))
    return {"card": ac_card(a, ctx),
            "dialog": {"header": short(ctx["label"], 12), "question": q, "options": options, "multiSelect": False},
            **({"fallback": _fallback(options, "Type suggestions")} if ctx.get("fallback") else {})}
