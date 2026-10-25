"""Presentation layer: the chat card and the choice dialog for ONE gap or story, built by the core.

The model relays what is built here instead of composing it, so every presentation is the same size and shape, costs
no composition tokens and cannot drift from the rules (which options exist, when `Approve` is withheld, ...).

`dialog` is a ready `AskUserQuestion` payload (one question; the tool adds the free-text “Other” box itself).
`inline: true` means the dialog already carries everything the human needs, so **no separate chat message is sent**.
`card` is the compact markdown for when it does not (long history, a story's acceptance criteria). Without the dialog
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
    opts.append(_opt("Carry Forward", "Keep this gap open, continue to the stories, and flag them until it is answered"))
    return opts


def gap_view(g: dict, ctx: dict) -> dict:
    """`ctx`: reqmap (id → requirement), blocks_stories, carry_ok, blocker_why, left, story_ids, owner_missing, fallback."""
    reqmap = ctx["reqmap"]
    reqs = [reqmap[r] for r in g["impacted_requirement_ids"] if r in reqmap]
    flags = ["blocks stories"] if ctx["blocks_stories"] else []
    if g.get("material"):
        flags.append("sensitive")
    if g["status"] == "DEFERRED":
        flags.append("deferred")
    elif g.get("carry_forward"):
        flags.append("carried forward")
    head = f"[{g['priority']}" + (f" · {', '.join(flags)}" if flags else "") + "]"
    affects = ", ".join(f"{r['requirement_id']} {short(r['title'], 40)}" for r in reqs) or ", ".join(g["impacted_requirement_ids"])
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
    if ctx["story_ids"]:
        card.append("**Stories already written for these requirements:** " + ", ".join(ctx["story_ids"]))
    if suggested:
        card.append(f"**Suggestion** from {suggested.get('source')}: {suggested.get('answer')} — only an answer if you choose it.")
    if ctx["carry_ok"] and g["status"] == "WAITING_FOR_ANSWER":
        card.append("You can carry this forward: it stays open and visible, stories are still written and flagged until you answer.")
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
        card.append("**This gap needs an impact review after a BRD change** — check the question still stands.")
    dialog = {"header": g["gap_id"], "question": q, "options": options, "multiSelect": False}
    reply = _fallback(options, "Type an answer or instruction") if ctx.get("fallback") else None
    if inline:  # the dialog text already says everything: no card, and without the tool the question itself is the message
        return {"inline": True, "dialog": dialog, **({"fallback": f"{q}\n{reply}"} if reply else {})}
    return {"inline": False, "card": "\n".join(card), "dialog": dialog, **({"fallback": reply} if reply else {})}


# ---------------------------------------------------------------- stories

def blocker_tags(blockers: list[str]) -> list[str]:
    """One or two words per blocker for the dialog; the full wording stays on the card."""
    out = []
    for b in blockers:
        if "baseline" in b:
            t = "baseline needs refreshing"
        elif "BRD change" in b:
            t = "BRD change awaiting review"
        elif "impact review" in b:
            t = "impact review pending"
        elif "linked requirement" in b:
            t = "requirement changed"
        elif "unresolved gap" in b:
            t = "open " + b.split("gap(s): ", 1)[-1]
        else:
            t = short(b, 40)
        if t not in out:
            out.append(t)
    return out


def story_options(s: dict, blockers: list[str], flagged: bool, cause: str | None, bucket: str) -> list[dict]:
    v = s["version"]
    approve = _opt(f"Approve v{v}", "Saves this exact version to stories/approved/")
    if not blockers:
        if bucket == "deferred":
            return [approve, _opt("Not approved", "Saves it to stories/unapproved/, never to approved/"),
                    _opt("Keep deferred", "Leave it in stories/deferred/; it is listed again next time")]
        if bucket == "unapproved":
            return [_opt(f"Approve v{v}", "Reconsider and approve this exact version"),
                    _opt("Defer story", "Parks it in stories/deferred/ to revisit later"),
                    _opt("Keep as is", "Leave it in stories/unapproved/; nothing changes")]
        return [approve, _opt("Defer story", "Parks it in stories/deferred/ to revisit later"),
                _opt("Not approved", "Saves it to stories/unapproved/, never to approved/")]
    keep = _opt("Keep deferred", "Leave it in stories/deferred/; it is listed again next time") if bucket == "deferred" \
        else _opt("Keep as is", "Nothing changes; it is listed again next time")
    out = []
    if flagged or any("linked requirement" in b for b in blockers):
        out.append(_opt("Update story", "Apply the decision and create the next version, which comes back here for approval"))
        if flagged and cause != "gap-resolved":
            out.append(_opt("No change needed", "Confirm the story is unaffected (records your words)"))
    if bucket != "deferred":
        out.append(_opt("Defer story", "Parks it in stories/deferred/ to revisit later"))
    out.append(keep)
    return out[:4]


def story_card(s: dict, live_gaps: list[str], blockers: list[str], update_ctx: list[dict]) -> str:
    us = s["user_story"]
    notes = s.get("notes") or {}
    lines = [f"**{s['story_id']} · {s['title']} · v{s['version']}** ({s['status'].replace('_', ' ').title()}"
             + (f" · {s['readiness'].replace('_', ' ').title()}" if s.get("readiness") else "") + ")",
             f"As a **{us['role']}**, I want {us['capability']}, so that {us['value']}.", "**Acceptance criteria**"]
    lines += [f"{i}. {c}" for i, c in enumerate(s["acceptance_criteria"], 1)]
    lines.append("**Traceability:** " + ", ".join(s["requirement_ids"]) + (f" · decisions {', '.join(s['decision_ids'])}" if s["decision_ids"] else "")
                 + (f" · {', '.join(s['source_references'])}" if s["source_references"] else ""))
    for u in s.get("unresolved_decisions") or []:
        lines.append(f"**Open {u['gap_id']}** ({u.get('priority', '?')}{', carried forward' if u.get('carried_forward') else ''}) — "
                     f"{short(u.get('question'), 140)} · impact: {short(u['potential_impact'], 200)}"
                     + (f" · working assumption, NOT approved: {short(u['interim_assumption'], 140)}" if u.get("interim_assumption") else ""))
    extra_live = [g for g in live_gaps if g not in {u["gap_id"] for u in s.get("unresolved_decisions") or []}]
    if extra_live:
        lines.append(f"**Open gaps not declared in the story:** {', '.join(extra_live)}")
    for label, key in (("Business rules", "business_rules"), ("Validation/exceptions", "validation_exceptions"), ("Non-functional", "non_functional")):
        if notes.get(key):
            lines.append(f"**{label}:** {short(notes[key], 200)}")
    if s.get("assumptions"):
        lines.append("**Assumptions:** " + "; ".join(short(a, 120) for a in s["assumptions"]))
    ir = s.get("impact_review") or {}
    if ir.get("pending"):
        lines.append(f"**Flagged for impact review:** {ir.get('reason')}")
    for u in update_ctx:
        lines.append(f"**Decision to apply — {u['decision_id']} ({u['gap_id']}):** {u['interpretation']}")
    if s["status"] == "DEFERRED" and s.get("deferral"):
        lines.append(f"**Deferred** {_day(s['deferral'].get('at_ist'))}: {s['deferral'].get('reason') or 'no reason given'}")
    if blockers:
        lines.append("**Cannot be approved yet:** " + "; ".join(blockers))
    return "\n".join(lines)


def story_view(s: dict, ctx: dict) -> dict:
    """`ctx`: blockers, bucket, live_gaps, update_ctx, position ('2 of 6' or None), fallback."""
    ir = s.get("impact_review") or {}
    flagged = bool(ir.get("pending"))
    options = story_options(s, ctx["blockers"], flagged, ir.get("cause"), ctx["bucket"])
    can = not ctx["blockers"]
    q = (f"{s['story_id']} v{s['version']}" + (f" ({ctx['position']})" if ctx.get("position") else "") + ": "
         + ("approve it, suggest changes, or choose below? Type suggestions in “Other”."
            if can else f"cannot be approved yet ({'; '.join(blocker_tags(ctx['blockers']))}). Pick an option, or type edit suggestions in “Other”."))
    return {"card": story_card(s, ctx["live_gaps"], ctx["blockers"], ctx["update_ctx"]),
            "dialog": {"header": s["story_id"], "question": q, "options": options, "multiSelect": False},
            **({"fallback": _fallback(options, "Type suggestions")} if ctx.get("fallback") else {})}
