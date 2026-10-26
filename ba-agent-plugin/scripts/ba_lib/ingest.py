"""Reading requirement files: Markdown only, text normalisation, the formatting-versus-meaning comparison, and a scan for
text that tries to instruct the assistant.

The supplied files are never modified. Requirements arrive as `.md` from the upstream agent; other formats are refused with a
plain message rather than converted.
"""
from __future__ import annotations

import re
from pathlib import Path

from .util import BAError, normalize_ws, sha256_bytes

PARSER_VERSION = "ba-ingest/2.0"
MARKDOWN_EXT = (".md", ".markdown")
# Files in a requirements folder that are about the folder, not requirements.
SKIP_STEMS = {"readme", "changelog", "license", "contributing", "start-here", "adr-log", "index", "toc", "summary"}

# Heuristic only: surfaces text that tries to instruct the assistant. It is advisory; the real defence is that requirement
# text is always treated as data (see the human-approval rules).
INJECTION_PATTERNS = [
    r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|rules)",
    r"disregard (all |any |the )?(previous|prior|above|plugin|system)",
    r"(reveal|print|show) (your |the )?(system prompt|instructions)",
    r"you are now\b",
    r"\bapprove (all|every) (acceptance criteria|criteria|requirements?|gaps?)\b",
    r"mark (all |every )?(gaps?|criteria) (as )?(resolved|approved)",
    r"(run|execute) (the following|this) (command|script|code)",
    r"\b(curl|wget|Invoke-WebRequest)\b.*https?://",
    r"(delete|remove|overwrite) (the )?(original|source) (requirements?|file|document)",
    r"do not (ask|tell) the user",
]
_INJ = [re.compile(p, re.I) for p in INJECTION_PATTERNS]

UNSUPPORTED_HINT = ("The BA Agent reads Markdown (.md) requirement files only. {name} is a {ext} file: ask the upstream agent for "
                    "a Markdown export, or save its text as .md yourself (headings, lists and tables unchanged) and point the project at that.")


def check_markdown(path: Path) -> None:
    if path.suffix.lower() not in MARKDOWN_EXT:
        raise BAError("UNSUPPORTED_FORMAT", UNSUPPORTED_HINT.format(name=path.name, ext=path.suffix or "(no extension)"),
                      extension=path.suffix.lower())


def decode(data: bytes, name: str = "file") -> str:
    """UTF-8 text with a BOM, if any, kept as its first character (so a copy of the text is byte-identical)."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise BAError("UNREADABLE_SOURCE", f"{name} is not valid UTF-8 text. Save it as UTF-8 and try again.", file=name)


def normalize_text(text: str) -> str:
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in text.split("\n")]
    out, blank = [], 0
    for ln in lines:
        blank = blank + 1 if not ln else 0
        if blank <= 2:
            out.append(ln)
    return "\n".join(out).strip("\n") + "\n"


def file_hash(data: bytes) -> str:
    return sha256_bytes(data)


_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})


def material_normalize(text: str) -> str:
    """Reduce text to what carries meaning, so formatting-only edits compare equal: markdown emphasis, heading, list and
    table markers, line wrapping, runs of spaces and typographic quotes are ignored. Words, numbers, punctuation inside a
    sentence and letter case are kept - changing any of them is a material change."""
    t = text.translate(_QUOTES)
    t = re.sub(r"(?m)^\s*\|?[\s:|-]*-{3,}[\s:|-]*\|?\s*$", " ", t)           # table separator rows
    t = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", t)                                 # heading markers
    t = re.sub(r"(?m)^\s*(?:[-*+]|\d+[.)])\s+", "", t)                          # list bullets / numbers
    t = re.sub(r"(?m)^\s*>\s?", "", t)                                          # block quotes
    t = re.sub(r"[*`|]", " ", t)                                                # emphasis, code, table pipes
    t = re.sub(r"(?<!\w)_|_(?!\w)", " ", t)                                     # _emphasis_ but not snake_case
    return normalize_ws(t)


def scan_injection(text: str) -> list[dict]:
    hits = []
    for n, ln in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), 1):
        for pat in _INJ:
            if pat.search(ln):
                hits.append({"line": n, "excerpt": normalize_ws(ln)[:140]})
                break
    return hits


def _plain(s: str) -> str:
    return normalize_ws(re.sub(r"[*_`|>#]", " ", s)).lower()


def excerpt_in_text(excerpt: str, text: str) -> bool:
    """Is `excerpt` (a verbatim quote, formatting aside) part of `text`?"""
    ex = _plain(excerpt)
    return bool(ex) and ex in _plain(text)
