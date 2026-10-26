"""The requirement index: a read-only locator over the supplied Markdown files.

Nothing is decomposed, rewritten or invented. The index records, for every requirement unit the upstream document already has,
its key, title, line extent and hashes; `req show` reads the text itself from the accepted snapshot of the file.

A requirement key is `<path relative to the requirements folder>::<ID>` (`requirements/auth.md::REQ-012`). The bare ID is
accepted wherever it is unique in the folder; two files that both contain `REQ-001` need the full key.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from . import conflicts, docmodel, ingest, snapshots
from .util import BAError, ist_now_iso, normalize_ws, sha256_bytes, sha256_text

INDEX = "index/requirements-index.json"
INDEX_MD = "index/requirements-index.md"
PAIRS_LIMIT = 400  # above this many requirements the all-pairs comparison is not run unprompted


# ------------------------------------------------------------------ scanning
def _skip_dir(name: str) -> bool:
    return name.startswith(".") or name in ("node_modules", "__pycache__")


def discover_files(root: Path, kind: str, recursive: bool, exclude: tuple[Path, ...] = (),
                   name: str | None = None) -> tuple[list[tuple[str, Path]], list[dict]]:
    """[(relative posix path, absolute path)] of the Markdown files to read, and the files passed over with the reason.
    For a single file, `root` is its folder and `name` the file."""
    root = Path(root)
    skipped: list[dict] = []
    if kind == "file":
        return [(name, root / name)], skipped
    out: list[tuple[str, Path]] = []
    ex = [os.path.realpath(x) for x in exclude]
    for dp, dns, fns in os.walk(root):
        here = os.path.realpath(dp)
        dns[:] = sorted(d for d in dns if not _skip_dir(d) and recursive and os.path.realpath(os.path.join(dp, d)) not in ex)
        for f in sorted(fns):
            path = Path(dp) / f
            rel = path.relative_to(root).as_posix()
            if f.startswith((".", "~$")) or path.suffix.lower() not in ingest.MARKDOWN_EXT or here in ex:
                continue
            if path.stem.lower() in ingest.SKIP_STEMS:
                skipped.append({"file": rel, "reason": "not a requirements file (README, index, changelog ...)"})
                continue
            out.append((rel, path))
        if not recursive:
            break
    out.sort(key=lambda t: t[0].lower())
    return out, skipped


def scan(root: Path, kind: str, recursive: bool, id_pattern: str | None, exclude: tuple[Path, ...] = (), only: list[str] | None = None,
         name: str | None = None) -> dict:
    """Read every requirement file (read-only) and locate its units. Nothing is written."""
    files, skipped = discover_files(root, kind, recursive, exclude, name)
    if only is not None:
        files = [(r, p) for r, p in files if r in only]
    out = {"root": str(root), "files": {}, "skipped": skipped, "enriched_files": [], "unreadable": [], "warnings": []}
    for rel, path in files:
        try:
            data = path.read_bytes()
        except OSError as e:
            out["unreadable"].append({"file": rel, "error": str(e)})
            continue
        try:
            text = ingest.decode(data, rel)
        except BAError as e:
            out["unreadable"].append({"file": rel, "error": e.message})
            continue
        if docmodel.has_markers(text):
            out["enriched_files"].append(rel)
            continue
        if len(text.strip()) < 3:
            skipped.append({"file": rel, "reason": "empty"})
            continue
        loc = docmodel.locate(text, id_pattern)
        if not loc["units"]:
            skipped.append({"file": rel, "reason": "no requirement units found (no headings with an ID, no ID table rows, no headings with text)"})
            continue
        out["files"][rel] = {"data": data, "file_hash": sha256_bytes(data), "text": text, "units": loc["units"], "mode": loc["mode"],
                             "warnings": loc["warnings"], "unclosed_fence": loc["unclosed_fence"],
                             "injection_suspects": ingest.scan_injection(text)}
    return out


def analysis_text(text: str, id_pattern: str | None = None) -> str:
    """A unit's text as the duplicate / conflict heuristics should see it: without its own ID token (REQ-007 would otherwise
    'conflict' with REQ-003 on the numbers 7 and 3), heading markers or table pipes."""
    return ingest.material_normalize(re.compile(id_pattern or docmodel.DEFAULT_ID_PATTERN).sub(" ", text))


def make_entries(sc: dict, id_pattern: str | None = None) -> list[dict]:
    """Index entries for every unit of a scan, with advisory duplicate flags across the whole folder."""
    ents = []
    for rel in sorted(sc["files"], key=str.lower):
        for u in sc["files"][rel]["units"]:
            ents.append({"req_key": f"{rel}::{u['id']}", "id": u["id"], "file": rel, "title": u["title"], "kind": u["kind"],
                         "level": u["level"], "line_start": u["line_start"], "line_end": u["line_end"], "insert_after": u["insert_after"],
                         "text_hash": u["text_hash"], "material_hash": u["material_hash"], "status": "ACTIVE",
                         "existing_ac_hint": u["existing_ac_hint"], "flags": [{"kind": "DUPLICATE_ID", "note": "this ID appears more than once in the file"}]
                         if u.get("duplicate_id") else []})
    if 1 < len(ents) <= PAIRS_LIMIT:
        texts = {}
        for rel, f in sc["files"].items():
            lines = docmodel.split_lines(f["text"])
            for e in (x for x in ents if x["file"] == rel):
                texts[e["req_key"]] = analysis_text(docmodel.unit_text(lines, e["line_start"], e["line_end"]), id_pattern)
        by = {e["req_key"]: e for e in ents}
        for a, b, s in conflicts.duplicate_pairs([(k, t) for k, t in texts.items()]):
            by[a]["flags"].append({"kind": "POSSIBLE_DUPLICATE", "note": f"Very similar to {b} (similarity {s:.2f}).", "related_ids": [b]})
            by[b]["flags"].append({"kind": "POSSIBLE_DUPLICATE", "note": f"Very similar to {a} (similarity {s:.2f}).", "related_ids": [a]})
    return ents


def build_doc(sc: dict, locator: dict, previous: dict | None = None) -> dict:
    """The index document for an accepted scan. Requirements that were in `previous` and are gone stay as REMOVED, so a gap or
    an acceptance record that names one still resolves."""
    ents = make_entries(sc, locator.get("id_pattern"))
    files = {rel: {"file_hash": f["file_hash"], "units": len(f["units"]), "mode": f["mode"], "status": "ACTIVE",
                   "unclosed_fence": bool(f["unclosed_fence"]), "instruction_like_lines": [h["line"] for h in f["injection_suspects"]],
                   "warnings": f["warnings"]} for rel, f in sc["files"].items()}
    if previous:
        have = {e["req_key"] for e in ents}
        for e in previous["requirements"]:
            if e["req_key"] not in have:
                ents.append(dict(e, status="REMOVED", line_start=None, line_end=None, insert_after=None))
        for rel, f in previous["files"].items():
            if rel not in files:
                files[rel] = dict(f, status="REMOVED", units=0)
    ents.sort(key=lambda e: (e["file"].lower(), e["status"] == "REMOVED", e["line_start"] or 0))
    return {"schema_version": "2.0", "generated_at_ist": ist_now_iso(), "parser": ingest.PARSER_VERSION, "locator": locator,
            "files": files, "requirements": ents}


def tree_fingerprint(doc: dict) -> str:
    return sha256_text(",".join(f"{r}={f['file_hash']}" for r, f in sorted(doc["files"].items()) if f["status"] == "ACTIVE"))


def markdown(doc: dict) -> str:
    out = ["# Requirement index", "", "A read-only list of the requirements found in the supplied files. The files themselves are never changed; "
           "the enriched copies are in `enriched/`.", "", f"- Files: {sum(1 for f in doc['files'].values() if f['status'] == 'ACTIVE')}  ·  "
           f"Requirements: {sum(1 for e in doc['requirements'] if e['status'] == 'ACTIVE')}", "",
           "| Key | Title | Lines | Flags |", "|---|---|---|---|"]
    for e in doc["requirements"]:
        lines = f"{e['line_start']}–{e['line_end']}" if e["status"] == "ACTIVE" else "REMOVED"
        out.append(f"| {e['req_key']} | {docmodel.cell(e['title'], 80)} | {lines} | {', '.join(f['kind'] for f in e['flags']) or '—'} |")
    return "\n".join(out) + "\n"


def write(p, doc: dict) -> None:
    p.write_json(INDEX, doc)
    p.write_text(INDEX_MD, markdown(doc))


# ------------------------------------------------------------------- access
def load(p) -> dict:
    doc = getattr(p, "_index", None)
    if doc is None:
        if not p.exists(INDEX):
            return {"schema_version": "2.0", "locator": {}, "files": {}, "requirements": []}
        doc = p._index = p.read_json(INDEX)
    return doc


def entries(p, active_only: bool = True) -> list[dict]:
    return [e for e in load(p)["requirements"] if not active_only or e["status"] == "ACTIVE"]


def by_key(p, active_only: bool = False) -> dict[str, dict]:
    return {e["req_key"]: e for e in entries(p, active_only)}


def resolve(p, ref: str, active_only: bool = False) -> str:
    """A full key from a key, a bare ID that is unique in the folder, or an unambiguous `file::ID` suffix."""
    ref = normalize_ws(str(ref or ""))
    ents = entries(p, active_only)
    keys = [e["req_key"] for e in ents]
    if ref in keys:
        return ref
    for hits in ([e["req_key"] for e in ents if e["id"] == ref], [k for k in keys if k.endswith("/" + ref)]):
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise BAError("AMBIGUOUS_REQUIREMENT", f"{ref} matches several requirements; use the full key: {hits}", keys=hits)
    raise BAError("UNKNOWN_REQUIREMENT", f"Requirement {ref} does not exist (see `req list`).")


def labels(p) -> dict[str, str]:
    """key -> the short name to show a person: the bare ID, or the full key when the ID is not unique in the folder."""
    ents = entries(p, False)
    n: dict[str, int] = {}
    for e in ents:
        n[e["id"]] = n.get(e["id"], 0) + 1
    return {e["req_key"]: (e["id"] if n[e["id"]] == 1 else e["req_key"]) for e in ents}


def label(p, key: str) -> str:
    return labels(p).get(key, key)


def _lines(p, rel: str, cache: dict) -> list:
    if rel not in cache:
        text = snapshots.accepted_text(p, rel)
        if text is None:  # snapshot damaged: fall back to the file itself, but only if it is still what was indexed
            path = Path(p.state["source"]["root"]) / rel
            data = path.read_bytes() if path.is_file() else b""
            if sha256_bytes(data) != load(p)["files"].get(rel, {}).get("file_hash"):
                raise BAError("SNAPSHOT_MISSING", f"The accepted copy of {rel} is missing or damaged and the file has changed since; "
                              "run `changes check`.", file=rel)
            text = data.decode("utf-8")
        cache[rel] = docmodel.split_lines(text)
    return cache[rel]


def text(p, ref_or_entry, _cache: dict | None = None) -> str:
    e = ref_or_entry if isinstance(ref_or_entry, dict) else by_key(p)[resolve(p, ref_or_entry)]
    if e["status"] != "ACTIVE":
        raise BAError("REQUIREMENT_REMOVED", f"{e['req_key']} was removed from the source document.")
    return docmodel.unit_text(_lines(p, e["file"], _cache if _cache is not None else {}), e["line_start"], e["line_end"])


def texts(p, active_only: bool = True) -> dict[str, str]:
    cache: dict = {}
    return {e["req_key"]: text(p, e, cache) for e in entries(p, active_only) if e["status"] == "ACTIVE"}


def analysis_texts(p) -> dict[str, str]:
    pat = p.state["source"]["locator"]["id_pattern"]
    return {k: analysis_text(t, pat) for k, t in texts(p).items()}


def view(p, key: str, with_text: bool = True) -> dict:
    e = by_key(p)[key]
    out = {"req_key": key, "id": e["id"], "label": label(p, key), "title": e["title"], "file": e["file"], "status": e["status"],
           "lines": [e["line_start"], e["line_end"]], "flags": e["flags"], "existing_ac_hint": e["existing_ac_hint"]}
    if with_text and e["status"] == "ACTIVE":
        out["text"] = text(p, e)
    return out


def pairs_report(p) -> dict:
    items = list(analysis_texts(p).items())
    dups = [{"a": a, "b": b, "similarity": s} for a, b, s in conflicts.duplicate_pairs(items)]
    cons = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            why = conflicts.conflict_reasons(items[i][1], items[j][1])
            if why:
                cons.append({"a": items[i][0], "b": items[j][0], "reasons": why})
    return {"possible_duplicates": dups, "possible_conflicts": cons, "compared": len(items),
            "note": "Advisory candidates only. Confirm real contradictions as a 'Contradiction, duplication, or dependency' gap."}


# --------------------------------------------------------------- delegation
BATCH_CHARS = 30000      # about 7.5k tokens of requirement text per helper batch (env BA_BATCH_CHARS or --batch-chars overrides)
DELEGATE_ABOVE = 60000   # above this much text (~15k tokens), reading in helper agents beats reading here (env BA_DELEGATE_ABOVE or --delegate-above)


def _limit(value, env: str, default: int) -> int:
    for v in (value, os.environ.get(env)):
        try:
            if v is not None and int(v) > 0:
                return int(v)
        except (TypeError, ValueError):
            pass
    return default


def outline_batches(p, delegate_above: int | None = None, batch_chars: int | None = None) -> dict:
    """Requirement sizes and a batching plan for gap analysis. When `delegate` is true the requirements are large enough that helper
    agents may be worth their cold start (each re-reads its batch): the main session asks once and otherwise reads one at a time."""
    limit = _limit(batch_chars, "BA_BATCH_CHARS", BATCH_CHARS)
    sizes = [(k, len(t)) for k, t in texts(p).items()]
    batches, cur, used = [], [], 0
    for k, n in sizes:
        if cur and used + n > limit:
            batches.append(cur)
            cur, used = [], 0
        cur.append(k)
        used += n
    if cur:
        batches.append(cur)
    total = sum(n for _, n in sizes)
    return {"requirements": [{"key": k, "chars": n} for k, n in sizes], "total_chars": total,
            "delegate": total > _limit(delegate_above, "BA_DELEGATE_ABOVE", DELEGATE_ABOVE), "batches": batches}
