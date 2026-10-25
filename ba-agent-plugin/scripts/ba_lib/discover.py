"""`find`: look in a folder for BA projects and likely BRDs so the user is not asked for paths the folder already shows."""
from __future__ import annotations

import json
from pathlib import Path

from .ingest import SUPPORTED, UNSUPPORTED_HINT
from .util import BAError

BRD_EXT = set(SUPPORTED) | set(UNSUPPORTED_HINT)
SKIP_NAMES = {"readme", "changelog", "license", "start-here", "adr-log", "contributing"}
SKIP_DIRS = {"state", "snapshots", "reports", "extracted", "atomic-requirements", "gaps", "decisions", "baselines", "changes",
             "stories", "node_modules", ".git", "source"}
BRD_WORDS = ("brd", "requirement", "spec", "prd", "scope", "business")


def _project_row(root: Path) -> dict | None:
    try:
        st = json.loads((root / "state" / "workflow.json").read_text(encoding="utf-8"))
        return {"folder": str(root), "project_id": st.get("project_id"), "stage": st.get("stage")}
    except (OSError, ValueError):
        return None


def find(folder: str | None) -> dict:
    base = Path(folder or ".").resolve()
    if not base.is_dir():
        raise BAError("NOT_A_FOLDER", f"{base} is not a folder.")
    projects, brds = [], []
    own = _project_row(base)
    if own:
        projects.append(own)
    for child in sorted(base.iterdir()):
        try:
            if child.is_dir() and child.name not in SKIP_DIRS and not child.name.startswith("."):
                row = _project_row(child)
                if row:
                    projects.append(row)
                continue
            if not child.is_file() or child.suffix.lower() not in BRD_EXT or child.name.startswith(("~$", ".")):
                continue
        except OSError:
            continue
        if child.stem.lower() in SKIP_NAMES or own:
            continue
        brds.append({"path": str(child), "name": child.name, "format": child.suffix.lower().lstrip("."),
                     "readable_directly": child.suffix.lower() in SUPPORTED,
                     "looks_like_a_brd": any(w in child.stem.lower() for w in BRD_WORDS)})
    brds.sort(key=lambda b: (not b["looks_like_a_brd"], not b["readable_directly"], b["name"].lower()))
    if own:
        nxt = "This folder is already a BA project: run `status`, then continue from NEXT."
    elif projects:
        nxt = "A BA project exists here: resume it (`status` on its folder), unless the user wants a new one."
    elif len(brds) == 1:
        nxt = "One document looks like the BRD: propose it and this folder as the output folder in a single question."
    elif brds:
        nxt = "Several documents found: ask which is the BRD (list them, best match first), with this folder as the output folder."
    else:
        nxt = "No BRD in this folder: ask the user for it (or to attach the folder that holds it)."
    return {"folder": str(base), "projects": projects, "brd_candidates": brds[:12], "next": nxt}
