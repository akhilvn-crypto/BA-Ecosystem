"""BRD ingestion: format detection, text extraction, section outline, fingerprints.

Supported with the standard library only: Markdown, plain text, DOCX.
PDF and other formats are reported as unsupported (never silently dropped).
"""
from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from .util import BAError, normalize_ws, sha256_file, sha256_text

PARSER_VERSION = "ba-ingest/1.0"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

SUPPORTED = {".md": "markdown", ".markdown": "markdown", ".txt": "text", ".docx": "docx"}
UNSUPPORTED_HINT = {
    ".pdf": "PDF text extraction needs a library that is not available without installing packages. "
            "Please export the BRD to .docx, .md or .txt (or approve installing a PDF library).",
    ".doc": "Legacy .doc is not supported. Please save as .docx, .md or .txt.",
    ".rtf": "RTF is not supported. Please save as .docx, .md or .txt.",
    ".xlsx": "Spreadsheets are not supported as a BRD source. Export the relevant sheet to .md or .txt.",
}

# Heuristic only: surfaces text that tries to instruct the assistant. It is advisory; the real
# defence is that BRD content is always treated as data (see human-approval rules).
INJECTION_PATTERNS = [
    r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|rules)",
    r"disregard (all |any |the )?(previous|prior|above|plugin|system)",
    r"(reveal|print|show) (your |the )?(system prompt|instructions)",
    r"you are now\b",
    r"\bapprove (all|every) (stories|story|requirements?|gaps?)\b",
    r"mark (all |every )?(gaps?|stories) (as )?(resolved|approved)",
    r"(run|execute) (the following|this) (command|script|code)",
    r"\b(curl|wget|Invoke-WebRequest)\b.*https?://",
    r"(delete|remove|overwrite) (the )?(original|source) (brd|file|document)",
    r"do not (ask|tell) the user",
]
_INJ = [re.compile(p, re.I) for p in INJECTION_PATTERNS]


def detect_format(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in SUPPORTED:
        return SUPPORTED[ext]
    hint = UNSUPPORTED_HINT.get(ext, f"Unsupported file type {ext or '(none)'}. Supported: "
                                + ", ".join(sorted(SUPPORTED)) + ".")
    raise BAError("UNSUPPORTED_FORMAT", hint, extension=ext)


def normalize_text(text: str) -> str:
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in text.split("\n")]
    out, blank = [], 0
    for ln in lines:
        blank = blank + 1 if not ln else 0
        if blank <= 2:
            out.append(ln)
    return "\n".join(out).strip("\n") + "\n"


def _docx_text(path: Path) -> tuple[str, list[str]]:
    warnings: list[str] = []
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            if "word/document.xml" not in names:
                raise BAError("UNREADABLE_SOURCE", "DOCX has no word/document.xml; the file may be corrupt.")
            root = ET.fromstring(z.read("word/document.xml"))
            if any(n.startswith("word/media/") for n in names):
                warnings.append("DOCX contains images; image content is NOT extracted.")
            if any(n.startswith("word/header") or n.startswith("word/footer") for n in names):
                warnings.append("DOCX headers/footers are not extracted.")
    except zipfile.BadZipFile:
        raise BAError("UNREADABLE_SOURCE", "File is not a valid DOCX (zip) archive.")
    except ET.ParseError as e:
        raise BAError("UNREADABLE_SOURCE", f"DOCX XML could not be parsed: {e}")

    def para_text(p) -> str:
        parts = []
        for el in p.iter():
            if el.tag == W + "t" and el.text:
                parts.append(el.text)
            elif el.tag == W + "tab":
                parts.append("\t")
            elif el.tag == W + "br":
                parts.append(" ")
        return "".join(parts).strip()

    def para_style(p) -> tuple[int, bool]:
        ppr = p.find(W + "pPr")
        level, is_list = 0, False
        if ppr is not None:
            ps = ppr.find(W + "pStyle")
            if ps is not None:
                val = ps.get(W + "val", "")
                m = re.match(r"(?i)^heading\s*(\d)$", val)
                if m:
                    level = int(m.group(1))
                elif val.lower() == "title":
                    level = 1
                elif re.match(r"(?i)^list", val):
                    is_list = True
            if ppr.find(W + "numPr") is not None:
                is_list = True
        return level, is_list

    lines: list[str] = []
    body = root.find(W + "body")
    for child in (body if body is not None else []):
        if child.tag == W + "p":
            txt = para_text(child)
            if not txt:
                lines.append("")
                continue
            level, is_list = para_style(child)
            if level:
                lines.append("#" * min(level, 6) + " " + txt)
            elif is_list:
                lines.append("- " + txt)
            else:
                lines.append(txt)
        elif child.tag == W + "tbl":
            lines.append("")
            first = True
            for tr in child.iter(W + "tr"):
                cells = [normalize_ws(" ".join(para_text(p) for p in tc.iter(W + "p"))) for tc in tr.findall(W + "tc")]
                lines.append("| " + " | ".join(cells) + " |")
                if first:
                    lines.append("|" + "---|" * max(len(cells), 1))
                    first = False
            lines.append("")
    return "\n".join(lines), warnings


def extract(path: Path) -> dict:
    """Read the source (never modifies it) and return text + metadata."""
    if not path.exists() or not path.is_file():
        raise BAError("SOURCE_NOT_FOUND", f"BRD not found: {path}")
    fmt = detect_format(path)
    warnings: list[str] = []
    if fmt == "docx":
        raw, warnings = _docx_text(path)
    else:
        data = path.read_bytes()
        try:
            raw = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                raw = data.decode("cp1252")
                warnings.append("File was not UTF-8; decoded as Windows-1252. Check special characters.")
            except UnicodeDecodeError:
                raise BAError("UNREADABLE_SOURCE", "File is not valid UTF-8 text.")
    text = normalize_text(raw)
    if len(text.strip()) < 20:
        raise BAError("EMPTY_SOURCE", "The BRD has (almost) no extractable text.")
    sections = parse_sections(text, fmt)
    suspects = scan_injection(text)
    if suspects:
        warnings.append(f"{len(suspects)} line(s) look like instructions aimed at an AI assistant. "
                        "They are treated as document content only and will not be followed.")
    return {
        "format": fmt,
        "text": text,
        "fingerprint": sha256_file(path),
        "text_fingerprint": sha256_text(text),
        "sections": sections,
        "injection_suspects": suspects,
        "warnings": warnings,
        "parser": PARSER_VERSION,
    }


_NUM_HEAD = re.compile(r"^(\d+(?:\.\d+){0,5})[.)]?\s+([A-Za-z][^\n]{0,90})$")


def parse_sections(text: str, fmt: str) -> list[dict]:
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    heads: list[tuple[int, int, str]] = []
    fence = False
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        m = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", ln)
        if m:
            heads.append((i, len(m.group(1)), m.group(2).strip()))
            continue
        if fmt == "text":
            m = _NUM_HEAD.match(ln.strip())
            if m and not m.group(2).rstrip().endswith((".", ";", ",", ":")):
                heads.append((i, m.group(1).count(".") + 1, f"{m.group(1)} {m.group(2).strip()}"))
    sections: list[dict] = []

    def add(sid, title, level, start, end, key):
        body = "\n".join(lines[start:end]).rstrip()  # trailing blank lines are not content
        sections.append({
            "section_id": sid, "title": title, "level": level, "key": key,
            "line_start": start + 1, "line_end": max(end, start + 1),
            "hash": sha256_text(body), "has_table": any(l.strip().startswith("|") for l in lines[start:end]),
        })

    first = heads[0][0] if heads else len(lines)
    if any(l.strip() for l in lines[:first]):
        add("S0", "(preamble)", 0, 0, first, "(preamble)")
    if not heads:
        if not sections:
            add("S1", "Document", 1, 0, len(lines), "document")
        return sections
    counters = [0] * 7
    titles = [""] * 7
    seen: dict[str, int] = {}
    for n, (idx, level, title) in enumerate(heads):
        counters[level - 1] += 1
        titles[level - 1] = normalize_ws(title).lower()
        for j in range(level, 7):
            counters[j] = 0
            titles[j] = ""
        sid = "S" + ".".join(str(c) for c in counters[:level])
        key = "/".join(t for t in titles[:level] if t)
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            key = f"{key}#{seen[key]}"
        end = heads[n + 1][0] if n + 1 < len(heads) else len(lines)
        add(sid, title, level, idx, end, key)
    return sections


def section_text(text: str, sec: dict) -> str:
    lines = text.split("\n")
    return "\n".join(lines[sec["line_start"] - 1: sec["line_end"]])


def scan_injection(text: str) -> list[dict]:
    hits = []
    for n, ln in enumerate(text.split("\n"), 1):
        for pat in _INJ:
            if pat.search(ln):
                hits.append({"line": n, "excerpt": normalize_ws(ln)[:140]})
                break
    return hits


def _plain(s: str) -> str:
    return normalize_ws(re.sub(r"[*_`|>#]", " ", s)).lower()


def excerpt_in_text(excerpt: str, text: str) -> bool:
    ex = _plain(excerpt)
    return bool(ex) and ex in _plain(text)


def diff_sections(old: list[dict], new: list[dict]) -> dict:
    """Section-level diff by key (title path), not by positional id."""
    o = {s["key"]: s for s in old}
    n = {s["key"]: s for s in new}
    return {
        "added": sorted(k for k in n if k not in o),
        "removed": sorted(k for k in o if k not in n),
        "changed": sorted(k for k in n if k in o and n[k]["hash"] != o[k]["hash"]),
        "unchanged": sorted(k for k in n if k in o and n[k]["hash"] == o[k]["hash"]),
    }


def outline_markdown(name: str, meta: dict) -> str:
    out = [f"# BRD outline — {name}", "",
           f"- Format: {meta['format']}  ·  Parser: {meta['parser']}",
           f"- Fingerprint: `{meta['fingerprint']}`",
           f"- Sections: {len(meta['sections'])}", ""]
    for s in meta["sections"]:
        pad = "  " * max(s["level"] - 1, 0)
        tbl = " · table" if s["has_table"] else ""
        out.append(f"{pad}- **{s['section_id']}** {s['title']} (lines {s['line_start']}–{s['line_end']}{tbl})")
    if meta.get("injection_suspects"):
        out += ["", "## Content flagged as instruction-like (treated as data only)"]
        out += [f"- line {h['line']}: {h['excerpt']}" for h in meta["injection_suspects"]]
    if meta.get("warnings"):
        out += ["", "## Extraction warnings"] + [f"- {w}" for w in meta["warnings"]]
    return "\n".join(out) + "\n"
