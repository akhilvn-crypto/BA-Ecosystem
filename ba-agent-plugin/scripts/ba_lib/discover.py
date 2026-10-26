"""`find`: look in a folder for BA projects and likely requirement folders so the user is not asked for paths the folder already shows."""
from __future__ import annotations

import json
from pathlib import Path

from . import docmodel, ingest
from .util import BAError

SKIP_DIRS = {"state", "snapshots", "reports", "index", "decisions", "changes", "gaps", "acceptance", "enriched", "node_modules", ".git",
             "source", "ba-output", "dist"}
REQ_WORDS = ("requirement", "req", "spec", "prd", "scope", "brd", "stories", "features")
MAX_READ = 2_000_000


def _project_row(root: Path) -> dict | None:
    try:
        st = json.loads((root / "state" / "workflow.json").read_text(encoding="utf-8"))
        return {"folder": str(root), "project_id": st.get("project_id"), "stage": st.get("stage"), "schema_version": st.get("schema_version")}
    except (OSError, ValueError):
        return None


def _md_files(folder: Path) -> list[Path]:
    try:
        return sorted(f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in ingest.MARKDOWN_EXT and not f.name.startswith((".", "~$"))
                      and f.stem.lower() not in ingest.SKIP_STEMS)
    except OSError:
        return []


def _units(f: Path) -> tuple[int, str]:
    try:
        if f.stat().st_size > MAX_READ:
            return 0, "too large to look at"
        text = f.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return 0, "unreadable"
    if docmodel.has_markers(text):
        return 0, "enriched copy (BA Agent output)"
    loc = docmodel.locate(text)
    return len(loc["units"]), loc["mode"]


def _candidate(folder: Path) -> dict | None:
    files = _md_files(folder)
    rows = [(f, *_units(f)) for f in files]
    rows = [r for r in rows if r[1]]
    if not rows:
        return None
    n_id = sum(r[1] for r in rows if r[2] == "id")
    return {"path": str(folder), "kind": "folder", "md_files": len(rows), "requirements": sum(r[1] for r in rows),
            "with_ids": n_id > 0, "looks_like_requirements": any(w in folder.name.lower() for w in REQ_WORDS) or n_id > 0,
            "files": [f.name for f, *_ in rows][:12]}


def find(folder: str | None) -> dict:
    base = Path(folder or ".").resolve()
    if not base.is_dir():
        raise BAError("NOT_A_FOLDER", f"{base} is not a folder.")
    projects, cands = [], []
    own = _project_row(base)
    if own:
        projects.append(own)
    if not own:
        c = _candidate(base)
        if c:
            cands.append(c)
    for child in sorted(base.iterdir()):
        try:
            if not child.is_dir() or child.name.startswith(".") or child.name in SKIP_DIRS:
                continue
            row = _project_row(child)
            if row:
                projects.append(row)
                continue
            if own:
                continue
            c = _candidate(child)
            if c:
                cands.append(c)
        except OSError:
            continue
    cands.sort(key=lambda c: (not c["looks_like_requirements"], not c["with_ids"], c["path"].lower()))
    if own:
        nxt = "This folder is already a BA project: run `status`, then continue from NEXT."
    elif projects:
        nxt = "A BA project exists here: resume it (`status` on its folder), unless the user wants a new one."
    elif len(cands) == 1:
        nxt = ("One place holds requirement files: propose it as the requirements folder and `<its parent>/ba-output/<name>` as the project "
               "folder in a single question.")
    elif cands:
        nxt = "Several folders hold requirement files: ask which one (list them, best match first)."
    else:
        nxt = "No requirement files (.md) found here: ask the user where the requirements are (a folder, or a single .md file)."
    return {"folder": str(base), "projects": projects, "requirement_candidates": cands[:12], "next": nxt}
