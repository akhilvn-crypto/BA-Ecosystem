"""Requirement documents: find the requirement units in a Markdown file and build the enriched copy of it.

Nothing in this module touches a file. The supplied requirement files are read-only input; the enriched copy is a pure
function of (source text, approved acceptance criteria, gap state): the source text byte for byte, a one-line generated
header, and one fenced BA block appended inside each requirement's extent.

Render invariant: `strip_blocks(render_enriched(text, ...)) == text`, byte for byte (line endings, BOM and a missing final
newline included). `docsync` checks it after every render and refuses to write a file that fails it.

A requirement *unit* is whatever the upstream document already treats as one requirement:
  - a heading whose text carries an ID token (`### REQ-012 Password reset`), or
  - a table row whose ID cell holds an ID token (the block goes after the table), or
  - with neither in the file, every heading that has body text (key = heading path); a warning says so once.
"""
from __future__ import annotations

import re

from . import ingest
from .util import normalize_ws, sha256_text

DEFAULT_ID_PATTERN = r"\b[A-Z][A-Z0-9]{0,9}(?:-[A-Z][A-Z0-9]*)*-\d+(?:\.\d+)*\b"

_EOL = re.compile(r"\r\n|\n|\r")
_EOL_S = r"(?:\r\n|\n|\r)"
_HEAD = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_SEP = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_ID_HEADER = re.compile(r"^(id|req(uirement)?\.?\s*(id|no\.?|#|ref)?|ref(erence)?|#|key)$", re.I)
_AC_LABEL = re.compile(r"^\s*(?:#{1,6}\s*|\*\*|__)?\s*acceptance criteri", re.I)
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*\S)\s*$")
_GWT = re.compile(r"\bgiven\b.+\bwhen\b.+\bthen\b", re.I)

# ------------------------------------------------------------------ markers
BEGIN_RE = re.compile(r'<!-- ba:begin key="(?P<key>[^"]*)"(?P<attrs>[^\r\n]*?)-->')
BLOCK_RE = re.compile(r'<!-- ba:begin key="(?P<key>[^"]*)"(?P<attrs>[^\r\n]*?)-->(?P<body>.*?)<!-- ba:end key="(?P=key)" -->', re.S)
_STRIP_BLOCK_RE = re.compile(_EOL_S + "{2}" + r'<!-- ba:begin key="[^"]*"[^\r\n]*?-->.*?<!-- ba:end key="[^"]*" -->', re.S)
_HEADER_RE = re.compile(r"<!-- ba:generated [^\r\n]*?-->" + _EOL_S)
MARKER_WORDS = ("ba:begin", "ba:end", "ba:generated")


def has_markers(text: str) -> bool:
    return any(w in text for w in MARKER_WORDS)


def esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace('"', "&quot;").replace(">", "&gt;").replace("<", "&lt;")


def unesc(s: str) -> str:
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&amp;", "&")


def safe(s, n: int | None = None) -> str:
    """One line of generated text that can neither close a comment nor forge a BA marker."""
    t = normalize_ws(str(s or "")).replace("<!--", "&lt;!--").replace("-->", "--&gt;")
    return t if n is None or len(t) <= n else t[: n - 1].rstrip() + "…"


def cell(s, n: int = 200) -> str:
    return safe(s, n).replace("|", "\\|")


# -------------------------------------------------------------------- lines
def split_lines(text: str) -> list[tuple[str, str]]:
    """[(content, eol)] - joining content+eol again gives the text back exactly."""
    out, pos = [], 0
    for m in _EOL.finditer(text):
        out.append((text[pos:m.start()], m.group()))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], ""))
    return out


def join_lines(lines) -> str:
    return "".join(c + e for c, e in lines)


def dominant_eol(lines) -> str:
    n: dict[str, int] = {}
    for _, e in lines:
        if e:
            n[e] = n.get(e, 0) + 1
    return max(n, key=lambda k: (n[k], k == "\n")) if n else "\n"


def _content(lines, i: int) -> str:
    c = lines[i][0]
    return c.lstrip("﻿") if i == 0 else c


def unit_text(lines, line_start: int, line_end: int) -> str:
    """The text of a unit (1-based inclusive lines): trailing spaces and line-ending style do not count."""
    return "\n".join(_content(lines, i).rstrip() for i in range(line_start - 1, line_end))


# ------------------------------------------------------------------- locate
def compile_locator(pattern: str | None) -> re.Pattern:
    try:
        return re.compile(pattern or DEFAULT_ID_PATTERN)
    except re.error as e:
        from .util import BAError
        raise BAError("BAD_LOCATOR", f"The requirement-ID pattern is not a valid regular expression: {e}")


def _scan(lines):
    """(headings [(idx, level, title)], fenced line indexes, unclosed fence at the end)."""
    heads, fenced = [], set()
    fence = None
    for i in range(len(lines)):
        c = _content(lines, i)
        m = _FENCE.match(c)
        if fence is None:
            if m:
                fence = (m.group(1)[0], len(m.group(1)))
                fenced.add(i)
                continue
        else:
            fenced.add(i)
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1] and not m.group(2).strip():
                fence = None
            continue
        h = _HEAD.match(c)
        if h:
            heads.append((i, len(h.group(1)), h.group(2).strip()))
    return heads, fenced, fence is not None


def _tables(lines, fenced):
    """[(first_idx, last_idx)] of Markdown tables (header row + separator row + body) outside code fences."""
    out, i, n = [], 0, len(lines)
    while i < n:
        if i not in fenced and _content(lines, i).lstrip().startswith("|"):
            j = i
            while j + 1 < n and (j + 1) not in fenced and _content(lines, j + 1).lstrip().startswith("|"):
                j += 1
            if j - i >= 1 and _SEP.match(_content(lines, i + 1)):
                out.append((i, j))
            i = j + 1
        else:
            i += 1
    return out


def _cells(row: str) -> list[str]:
    parts = re.split(r"(?<!\\)\|", row.strip())
    if parts and not parts[0].strip():
        parts = parts[1:]
    if parts and not parts[-1].strip():
        parts = parts[:-1]
    return [re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", c).strip().strip("*_`").strip() for c in parts]


def _last_nonblank(lines, lo: int, hi_excl: int) -> int:
    k = hi_excl - 1
    while k > lo and not _content(lines, k).strip():
        k -= 1
    return max(k, lo)


def _hint(lines, a: int, b: int) -> list[str]:
    """Acceptance statements the upstream document already carries: a list under an 'Acceptance criteria' label, or any
    Given/When/Then line. They are read as input only, so that new criteria do not contradict them."""
    out: list[str] = []
    i = a
    while i <= b:
        c = _content(lines, i)
        if _AC_LABEL.match(c):
            j = i + 1
            while j <= b and (not _content(lines, j).strip() or _LIST_ITEM.match(_content(lines, j))):
                m = _LIST_ITEM.match(_content(lines, j))
                if m:
                    out.append(m.group(1))
                elif out and not _content(lines, j).strip() and j + 1 <= b and not _LIST_ITEM.match(_content(lines, j + 1)):
                    break
                j += 1
            i = j
            continue
        if _GWT.search(c) and c.strip() not in out:
            out.append(re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", c).strip())
        i += 1
    return [safe(x, 200) for x in dict.fromkeys(out)][:6]


def _unit(lines, uid, title, kind, level, a, b, insert_after):
    text = unit_text(lines, a + 1, b + 1)
    return {"id": uid, "title": title, "kind": kind, "level": level, "line_start": a + 1, "line_end": b + 1,
            "insert_after": insert_after, "text_hash": sha256_text(text),
            "material_hash": sha256_text(ingest.material_normalize(text)), "existing_ac_hint": _hint(lines, a, b)}


def _title_without_id(title: str, token: str) -> str:
    t = title.replace(token, " ", 1)
    t = re.sub(r"^[\s:;,.\-–—)(\[\]|]+|[\s:;,.\-–—)(\[\]|]+$", "", t)
    return normalize_ws(t) or token


def locate(text: str, id_pattern: str | None = None) -> dict:
    """Find the requirement units of one Markdown file.

    Returns {units, mode ('id' | 'headings' | 'none'), warnings, unclosed_fence}. Units are in document order and never
    overlap; each carries `line_start`/`line_end` (1-based, trailing blank lines excluded), `insert_after` (0-based index of
    the line after which its BA block goes), hashes of its text, and any acceptance statements the document already has.
    A second unit with the same ID gets the id `<ID>#2` and a DUPLICATE_ID warning."""
    id_re = compile_locator(id_pattern)
    lines = split_lines(text)
    heads, fenced, unclosed = _scan(lines)
    warnings: list[str] = []
    if unclosed:
        warnings.append("a code fence is never closed; the document cannot be enriched safely")
    hid = [(i, lv, t, id_re.search(t)) for i, lv, t in heads]
    hid = [(i, lv, t, m.group(0)) for i, lv, t, m in hid if m]
    unit_heads = {i for i, *_ in hid}

    def extent_end(hi: int, level: int) -> int:
        for j, lv, _ in heads:
            if j > hi and (lv <= level or j in unit_heads):
                return j
        return len(lines)

    units, spans = [], []
    for hi, lv, title, token in hid:
        end = _last_nonblank(lines, hi, extent_end(hi, lv))
        spans.append((hi, end))
        units.append(_unit(lines, token, _title_without_id(title, token), "heading", lv, hi, end, end))
    for a, b in _tables(lines, fenced):
        if any(s <= a <= e for s, e in spans):
            continue
        cells = _cells(_content(lines, a))
        col = next((k for k, c in enumerate(cells) if _ID_HEADER.match(c)), 0)
        for r in range(a + 2, b + 1):
            rc = _cells(_content(lines, r))
            if col < len(rc) and rc[col] and id_re.fullmatch(rc[col]):
                other = [c for k, c in enumerate(rc) if k != col and c]
                units.append(_unit(lines, rc[col], safe(other[0], 80) if other else rc[col], "row", 0, r, r, b))
    mode = "id" if units else "none"
    if not units:
        mode, units = _fallback(lines, heads, fenced, warnings)
    units.sort(key=lambda u: (u["line_start"], u["line_end"]))
    seen: dict[str, int] = {}
    for u in units:
        seen[u["id"]] = seen.get(u["id"], 0) + 1
        if seen[u["id"]] > 1:
            warnings.append(f"{u['id']} appears more than once (line {u['line_start']}); the later one is keyed {u['id']}#{seen[u['id']]}")
            u["id"] = f"{u['id']}#{seen[u['id']]}"
            u["duplicate_id"] = True
    return {"units": units, "mode": mode, "warnings": warnings, "unclosed_fence": unclosed}


def _fallback(lines, heads, fenced, warnings):
    """No IDs anywhere: every heading with body text of its own is a unit, keyed by its heading path."""
    if not heads:
        return "none", []
    titles = [""] * 7
    seen: dict[str, int] = {}
    units = []
    lvl1 = [h for h in heads if h[1] == 1]
    skip_title = len(lvl1) == 1 and len(heads) > 1 and heads[0][1] == 1
    for n, (hi, lv, title) in enumerate(heads):
        titles[lv - 1] = normalize_ws(title).lower()
        for j in range(lv, 7):
            titles[j] = ""
        nxt = heads[n + 1][0] if n + 1 < len(heads) else len(lines)
        body = [k for k in range(hi + 1, nxt) if _content(lines, k).strip()]
        if not body or (skip_title and n == 0):
            continue
        key = "/".join(t for t in titles[:lv] if t)
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            key = f"{key}#{seen[key]}"
        end = _last_nonblank(lines, hi, nxt)
        units.append(_unit(lines, key, title, "section", lv, hi, end, end))
    if units:
        warnings.append("no requirement IDs found; every heading with text is treated as one requirement and keyed by its heading "
                        "path (no IDs are added to the document). Set the ID pattern with `init --locator` if the IDs look different.")
    return ("headings" if units else "none"), units


# ------------------------------------------------------------------- blocks
def body_hash(body_lines: list[str]) -> str:
    return sha256_text("\n".join(x.rstrip() for x in body_lines))


def wrap_block(key: str, ac_version: int | None, body_lines: list[str]) -> list[str]:
    attrs = (f' ac="v{ac_version}"' if ac_version else "") + f' rendered="{body_hash(body_lines)}"'
    return [f'<!-- ba:begin key="{esc(key)}"{attrs} -->', *body_lines, f'<!-- ba:end key="{esc(key)}" -->']


def _front_matter_end(lines) -> int | None:
    """Index of the line that closes a leading YAML front matter, or None."""
    if lines and _content(lines, 0).strip() == "---":
        for i in range(1, len(lines)):
            if _content(lines, i).strip() in ("---", "...") and lines[i][1]:
                return i
    return None


def header_line(relpath: str) -> str:
    return f'<!-- ba:generated source="{esc(relpath)}" - generated by the BA Agent; edit the source file, not this copy -->'


def render_enriched(text: str, blocks: list[tuple[int, list[str]]], relpath: str) -> str:
    """The enriched copy: `text` with each block inserted after line `insert_after` (0-based), plus the header line."""
    lines = split_lines(text)
    eol = dominant_eol(lines)
    at: dict[int, list[list[str]]] = {}
    for idx, blk in blocks:
        if not 0 <= idx < len(lines):
            raise ValueError(f"insertion point {idx} is outside the document")
        at.setdefault(idx, []).append(blk)
    out = []
    for i, (c, e) in enumerate(lines):
        if i in at:
            E = e or eol
            c = c + "".join(E + E + E.join(b) for b in at[i])
        out.append((c, e))
    fm = _front_matter_end(lines)
    head = header_line(relpath) + eol
    if fm is None:
        bom = "﻿" if text.startswith("﻿") else ""
        return bom + head + join_lines(out)[len(bom):]
    return join_lines(out[:fm + 1]) + head + join_lines(out[fm + 1:])


def strip_blocks(text: str) -> str:
    """Undo `render_enriched`: the header and every BA block removed, the original bytes restored."""
    return _HEADER_RE.sub("", _STRIP_BLOCK_RE.sub("", text), count=1)


def find_blocks(text: str) -> list[dict]:
    """Every well-formed BA block: {key, attrs, body (without the marker lines), start, end}."""
    out = []
    for m in BLOCK_RE.finditer(text):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group("attrs")))
        body = m.group("body").strip("\r\n")
        out.append({"key": unesc(m.group("key")), "attrs": attrs, "body": body, "start": m.start(), "end": m.end()})
    return out


def markers_balanced(text: str) -> bool:
    return len(BEGIN_RE.findall(text)) == len(find_blocks(text)) == text.count("<!-- ba:end ")


def criteria_from_body(body: str) -> list[str] | None:
    """The numbered criteria of a block's 'Acceptance Criteria' section, or None when that section is missing."""
    m = re.search(r"(?m)^#{1,6}\s+Acceptance Criteria\s*$", body)
    if not m:
        return None
    out: list[str] = []
    for line in body[m.end():].split("\n"):
        if re.match(r"^#{1,6}\s", line) or line.startswith("**Readiness"):
            break
        n = re.match(r"^\s*\d+[.)]\s+(.*\S)\s*$", line)
        if n:
            out.append(n.group(1))
        elif line.strip() and out and not line.startswith("*"):
            out[-1] += " " + line.strip()
    return out


def normalize_body(body: str) -> str:
    return "\n".join(x.rstrip() for x in body.replace("\r\n", "\n").replace("\r", "\n").strip("\n").split("\n"))
