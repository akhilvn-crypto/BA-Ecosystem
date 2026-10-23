"""Deterministic, advisory conflict/duplicate heuristics.

These do NOT decide whether two statements truly conflict (that is semantic judgement for the
model and the human). They produce candidates that the closing step must explicitly review, so a
contradiction cannot slip through simply because nobody looked.
"""
from __future__ import annotations

import re

STOP = set("""a an and are as at be been by can do does for from has have if in into is it its may of on or
shall should that the their then there these this those to was were will with within when where which who
must user users system all any each per also not no via than so such""".split())
NEG = {"not", "no", "never", "cannot", "without", "prohibited", "forbidden", "n't", "nor", "neither"}
ANTONYMS = [("mandatory", "optional"), ("required", "optional"), ("allow", "deny"), ("allowed", "prohibited"),
            ("allowed", "forbidden"), ("enabled", "disabled"), ("always", "never"), ("increase", "decrease"),
            ("manual", "automatic"), ("approve", "reject"), ("synchronous", "asynchronous"), ("before", "after"),
            ("public", "private"), ("visible", "hidden"), ("encrypted", "plaintext"), ("only", "any"),
            ("daily", "weekly"), ("weekly", "monthly"), ("hourly", "daily")]
_NUM = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?(?![\w])")


def tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z][a-z'-]+", (text or "").lower())
    return {w for w in words if w not in STOP and len(w) > 2}


def numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUM.findall(text or "")}


def polarity_negative(text: str) -> bool:
    words = re.findall(r"[a-z']+", (text or "").lower())
    return sum(1 for w in words if w in NEG or w.endswith("n't")) % 2 == 1


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a | b) else 0.0


def _antonym_hit(a: set[str], b: set[str]) -> str | None:
    for x, y in ANTONYMS:
        if (x in a and y in b) or (y in a and x in b):
            return f"opposing terms '{x}' / '{y}'"
    return None


def conflict_reasons(text_a: str, text_b: str, min_overlap: float = 0.34) -> list[str]:
    ta, tb = tokens(text_a), tokens(text_b)
    shared = ta & tb
    overlap = len(shared) / max(1, min(len(ta), len(tb)))
    reasons = []
    na, nb = numbers(text_a), numbers(text_b)
    # Numeric disagreement is the most common silent contradiction, so it needs only one shared content word.
    if shared and overlap >= 0.3 and na and nb and na != nb and not (na <= nb or nb <= na):
        reasons.append(f"different numeric values ({', '.join(sorted(na))} vs {', '.join(sorted(nb))})")
    if len(shared) < 3 or overlap < min_overlap:
        return reasons
    if polarity_negative(text_a) != polarity_negative(text_b):
        reasons.append("one statement is negated and the other is not")
    ant = _antonym_hit(ta, tb)
    if ant:
        reasons.append(ant)
    return reasons


def candidate_conflicts(text: str, against: list[tuple[str, str]]) -> list[dict]:
    out = []
    for aid, other in against:
        r = conflict_reasons(text, other)
        if r:
            out.append({"artifact_id": aid, "reasons": r})
    return out


def classify(text_a: str, text_b: str, dup_threshold: float = 0.8):
    """('conflict', reasons) takes precedence over ('duplicate', similarity): same wording with a
    different number or polarity is a contradiction, not a duplicate."""
    why = conflict_reasons(text_a, text_b)
    if why:
        return "conflict", why
    sim = jaccard(tokens(text_a), tokens(text_b))
    if sim >= dup_threshold:
        return "duplicate", round(sim, 2)
    return None


def duplicate_pairs(items: list[tuple[str, str]], threshold: float = 0.8) -> list[tuple[str, str, float]]:
    out = []
    for x in range(len(items)):
        for y in range(x + 1, len(items)):
            k = classify(items[x][1], items[y][1], threshold)
            if k and k[0] == "duplicate":
                out.append((items[x][0], items[y][0], k[1]))
    return out
