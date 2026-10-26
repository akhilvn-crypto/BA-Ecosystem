"""Project: the artifact store, workflow state machine and audit log."""
from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from pathlib import Path

from . import docmodel, ingest, reqindex, render, schema, snapshots
from .util import (BAError, append_jsonl, atomic_write_bytes, atomic_write_text, ist_now_iso, is_within, read_json, read_jsonl,
                   safe_join)

FAULT_HOOK = None  # tests set this to simulate a crash at named points
SCHEMA_VERSION = "2.0"


def fault(point: str) -> None:
    if FAULT_HOOK:
        FAULT_HOOK(point)


STAGES = ["INITIALIZED", "SOURCE_INDEXED", "GAP_ANALYSIS", "WAITING_FOR_GAP_INPUT", "AC_DRAFTING", "WAITING_FOR_AC_REVIEW",
          "COMPLETED", "BLOCKED", "PAUSED"]
_REOPEN = {"GAP_ANALYSIS", "WAITING_FOR_GAP_INPUT"}
STAGE_TRANSITIONS = {
    "INITIALIZED": {"SOURCE_INDEXED"},
    "SOURCE_INDEXED": {"GAP_ANALYSIS"},
    "GAP_ANALYSIS": {"WAITING_FOR_GAP_INPUT", "AC_DRAFTING", "SOURCE_INDEXED"},
    "WAITING_FOR_GAP_INPUT": {"GAP_ANALYSIS", "AC_DRAFTING"},
    "AC_DRAFTING": {"WAITING_FOR_AC_REVIEW"} | _REOPEN,
    "WAITING_FOR_AC_REVIEW": {"COMPLETED", "AC_DRAFTING"} | _REOPEN,
    "COMPLETED": {"WAITING_FOR_AC_REVIEW", "AC_DRAFTING", "SOURCE_INDEXED"} | _REOPEN,
}
# PAUSED / BLOCKED may be entered from any stage except themselves and only exit to the saved resume_stage.
PAUSABLE = set(STAGES) - {"PAUSED", "BLOCKED"}
STAGE_ORDER = ["INITIALIZED", "SOURCE_INDEXED", "GAP_ANALYSIS", "WAITING_FOR_GAP_INPUT", "AC_DRAFTING", "WAITING_FOR_AC_REVIEW", "COMPLETED"]

GAP_STATUSES = ["OPEN", "WAITING_FOR_ANSWER", "RESOLVED", "DEFERRED", "REOPENED", "BLOCKED"]
GAP_TRANSITIONS = {
    "OPEN": {"WAITING_FOR_ANSWER", "DEFERRED", "BLOCKED"},
    "WAITING_FOR_ANSWER": {"RESOLVED", "DEFERRED", "OPEN", "BLOCKED"},
    "RESOLVED": {"REOPENED"},
    "DEFERRED": {"WAITING_FOR_ANSWER"},
    "REOPENED": {"WAITING_FOR_ANSWER", "DEFERRED", "BLOCKED"},
    "BLOCKED": {"OPEN", "WAITING_FOR_ANSWER"},
}
AC_TRANSITIONS = {
    "CANDIDATE": {"IN_REVIEW", "APPROVED", "DEFERRED", "CHANGES_REQUESTED", "SUPERSEDED", "BLOCKED"},
    "IN_REVIEW": {"APPROVED", "DEFERRED", "CHANGES_REQUESTED", "CANDIDATE", "SUPERSEDED", "BLOCKED"},
    "CHANGES_REQUESTED": {"SUPERSEDED", "CANDIDATE", "DEFERRED"},
    "APPROVED": {"SUPERSEDED"},
    "DEFERRED": {"CANDIDATE", "SUPERSEDED"},
    "BLOCKED": {"CANDIDATE", "SUPERSEDED"},
    "SUPERSEDED": set(),
}

# Every user-facing artifact folder keeps its readable copies in `md/` and its machine records in `json/`.
AC_BUCKETS = ("candidates", "approved", "unapproved", "deferred", "archive")
# A gap lives in the folder that matches its status and moves when the status changes: open (asked, answered later,
# reopened, carried forward), deferred, resolved. The register that holds them all is machine state, not a user folder.
GAP_BUCKETS = ("open", "deferred", "resolved")
GAP_REGISTER = "state/gap-register.json"
SPLIT_DIRS = ("index", "decisions", "changes") + tuple(f"gaps/{b}" for b in GAP_BUCKETS) + tuple(f"acceptance/{b}" for b in AC_BUCKETS)
FORMAT_DIRS = {".md": "md", ".json": "json", ".jsonl": "json"}
DIRS = ["state", "state/backups", "state/pending", "reports", "snapshots/source", "snapshots/ac"] + \
       [f"{d}/{sub}" for d in SPLIT_DIRS for sub in ("md", "json")]
PENDING_AC_BUCKETS = ("candidates", "unapproved", "deferred")
LIVE_AC_BUCKETS = ("candidates", "approved", "unapproved", "deferred")


def gap_bucket(g: dict) -> str:
    return {"RESOLVED": "resolved", "DEFERRED": "deferred"}.get(g["status"], "open")


def physical(rel: str) -> str:
    """Map a logical artifact path (`acceptance/approved/auth--REQ-1.md`) to where it lives on disk (`.../md/auth--REQ-1.md`).

    Only files directly inside a split folder move; folders, `state/`, `reports/`, `snapshots/` and `enriched/` are returned unchanged."""
    r = rel.replace("\\", "/").strip("/")
    d, _, name = r.rpartition("/")
    sub = FORMAT_DIRS.get(os.path.splitext(name)[1].lower()) if name else None
    return f"{d}/{sub}/{name}" if d in SPLIT_DIRS and sub else r


def slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60].strip("-") or "ba-project"
    return s if len(s) >= 2 else s + "-project"


def ac_fname(key: str) -> str:
    """A file name for a requirement key: `requirements/auth.md::REQ-012` -> `requirements__auth.md--REQ-012`."""
    base = key.replace("::", "--").replace("/", "__")
    if re.fullmatch(r"[A-Za-z0-9._-]+", base):
        return base
    import hashlib
    return re.sub(r"[^A-Za-z0-9._-]+", "_", base)[:100] + "-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:6]


def check_transition(table: dict, kind: str, cur: str, new: str) -> None:
    if cur == new:
        return
    if new not in table.get(cur, set()):
        raise BAError("INVALID_TRANSITION", f"{kind} transition {cur} → {new} is not allowed. "
                      f"Allowed from {cur}: {sorted(table.get(cur, set())) or 'none'}.", current=cur, requested=new)


class Project:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self._state: dict | None = None
        self._index: dict | None = None
        self._syncing = False
        self.doc_warnings: list[str] = []
        # Paths a move left behind (a gap changing folder, an AC changing bucket). They are reported as removed even when the
        # file was never in this workspace: a lean staged copy has no md/ view to notice missing, but the user's side does.
        self.vacated: set[str] = set()

    # ------------------------------------------------------------ lifecycle
    @classmethod
    def create(cls, root, requirements, name: str | None = None, copy_source: bool = False, recursive: bool = False,
               locator: str | None = None, enriched_dir: str | None = None) -> "Project":
        root, src = Path(root).resolve(), Path(requirements).resolve()
        if not src.exists():
            raise BAError("SOURCE_NOT_FOUND", f"Requirements not found: {src}. Give a folder of .md files or one .md file.")
        kind = "file" if src.is_file() else "folder"
        if kind == "file":
            ingest.check_markdown(src)
        src_root = src.parent if kind == "file" else src
        if (root / "state" / "workflow.json").exists():
            raise BAError("ALREADY_INITIALIZED", f"{root} already contains a BA project. Use resume/status instead.")
        if root.is_file() or root == src or (kind == "folder" and is_within(src, root)):
            raise BAError("OUTPUT_INSIDE_SOURCE", "The project folder must be a separate folder next to the requirements, not inside them "
                          f"(got {root}). The requirements are read-only input; everything the BA Agent writes goes in the project folder.")
        enr = Path(enriched_dir).resolve() if enriched_dir else None
        if enr and (is_within(src_root, enr) if kind == "folder" else enr == src.parent):
            raise BAError("OUTPUT_INSIDE_SOURCE", f"The enriched copies cannot be written inside the requirements folder ({enr}).")
        pattern = locator or docmodel.DEFAULT_ID_PATTERN
        docmodel.compile_locator(pattern)
        sc = reqindex.scan(src_root, kind, recursive, pattern, exclude=(root,), name=src.name if kind == "file" else None)  # fail on unreadable BEFORE creating anything
        if sc["enriched_files"]:
            raise BAError("ENRICHED_FILE_AS_SOURCE", f"{sc['enriched_files']} already carry BA Agent markers: they are enriched copies, not "
                          "the original requirements. Point the project at the original files.", files=sc["enriched_files"])
        if sc["unreadable"]:
            raise BAError("UNREADABLE_SOURCE", "Some files could not be read: " + "; ".join(f"{u['file']}: {u['error']}" for u in sc["unreadable"]),
                          files=sc["unreadable"])
        if not sc["files"]:
            raise BAError("NO_REQUIREMENTS_FOUND", f"No requirement files found in {src}. Looked for .md files with headings carrying an ID "
                          "(e.g. `### REQ-012 Title`), table rows with an ID, or headings with text.", skipped=sc["skipped"])
        try:
            root.mkdir(parents=True, exist_ok=True)
            for d in DIRS + ([] if enr else ["enriched"]):
                (root / d).mkdir(parents=True, exist_ok=True)
            if enr:
                enr.mkdir(parents=True, exist_ok=True)
            probe = root / "state" / ".write-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as e:
            raise BAError("OUTPUT_NOT_WRITABLE", f"Cannot write to project folder {root}: {e}")
        copied = False
        source_path = src
        if copy_source and not is_within(root, src):
            dest = root / "source" / (src.name if kind == "folder" else src.parent.name or "requirements")
            for rel, f in sc["files"].items():
                t = dest / rel
                t.parent.mkdir(parents=True, exist_ok=True)
                t.write_bytes(f["data"])
            copied, src_root = True, dest
            source_path = dest / src.name if kind == "file" else dest
        p = cls(root)
        now = ist_now_iso()
        loc = {"id_pattern": pattern}
        doc = reqindex.build_doc(sc, loc)
        state = {
            "schema_version": SCHEMA_VERSION, "project_id": slugify(name or (src.stem if kind == "file" else src.name)),
            "workflow_id": "wf-" + uuid.uuid4().hex[:12], "stage": "INITIALIZED", "resume_stage": None,
            "source": {"path": str(source_path), "original_path": str(src), "root": str(src_root), "kind": kind, "recursive": bool(recursive),
                       "locator": loc, "fingerprint": reqindex.tree_fingerprint(doc), "copied": copied},
            "enriched_dir": str(enr) if enr else None,
            "decision_owner": None, "decision_owner_unknown": None,
            "decision_owner_history": [], "gap_analysis_complete": False,
            "policy": {"blocking_priorities": ["CRITICAL", "HIGH"], "accept_nonblocking_carry_forward": False,
                       "deferral_revisit_required_priorities": [], "accepted_by": None},
            "pending_gap_ids": [], "pending_ac_keys": [], "last_completed_action": "project-initialized",
            "created_at": now, "updated_at": now,
        }
        p._state = state
        p.save_state("project-initialized")
        for rel, f in sc["files"].items():
            snapshots.record(p, rel, f["data"], "initial requirements")
        reqindex.write(p, doc)
        p._index = None
        p.audit("WORKFLOW_STARTED", source_kind=kind, files=len(sc["files"]), requirements=len(doc["requirements"]),
                source_fingerprint=state["source"]["fingerprint"], parser=ingest.PARSER_VERSION, copied_source=copied, recursive=bool(recursive))
        p.set_stage("SOURCE_INDEXED", "requirements-indexed")
        suspects = {rel: [h["line"] for h in f["injection_suspects"]] for rel, f in sc["files"].items() if f["injection_suspects"]}
        if suspects:
            p.audit("SOURCE_INSTRUCTION_LIKE_CONTENT", files=suspects)
        p.index_report = {"warnings": [f"{rel}: {w}" for rel, f in sc["files"].items() for w in f["warnings"]],
                          "skipped": sc["skipped"], "instruction_like_lines": sum(len(v) for v in suspects.values())}
        p.sync_docs()
        return p

    @classmethod
    def open(cls, root) -> "Project":
        p = cls(Path(root))
        if not (p.root / "state" / "workflow.json").exists():
            raise BAError("NOT_A_PROJECT", f"No BA project found at {p.root} (missing state/workflow.json).")
        p.state  # load + validate
        return p

    # ----------------------------------------------------------------- paths / IO
    def path(self, rel: str) -> Path:
        return safe_join(self.root, physical(rel))

    def files(self, folder: str, pattern: str) -> list[Path]:
        """Files of a split folder matching `pattern` (e.g. `*.json`), looked up in its `md/` or `json/` subfolder."""
        sub = FORMAT_DIRS.get(os.path.splitext(pattern)[1].lower(), "")
        return sorted(self.path(folder).joinpath(sub).glob(pattern))

    @property
    def enriched_root(self) -> Path:
        d = (self._state or {}).get("enriched_dir") if self._state is not None else None
        return Path(d) if d else self.root / "enriched"

    def _enriched_files(self) -> list[Path]:
        base = self.enriched_root
        return [f for f in base.rglob("*") if f.is_file()] if base.is_dir() else []

    def _dir_sig(self) -> dict:
        """(file count, newest mtime) per artifact folder: cheap enough to take before and after every command."""
        out = {}
        for d in SPLIT_DIRS:
            for sub in ("md", "json"):
                files = [x for x in (self.root / d / sub).glob("*") if x.is_file()]
                out[f"{d}/{sub}"] = (len(files), max((x.stat().st_mtime_ns for x in files), default=0))
        reports = [x for x in (self.root / "reports").glob("*.md")]
        out["reports"] = (len(reports), max((x.stat().st_mtime_ns for x in reports), default=0))
        en = self._enriched_files()
        out["enriched"] = (len(en), max((x.stat().st_mtime_ns for x in en), default=0))
        return out

    def receipt(self, since: dict | None = None) -> dict:
        """What is on disk right now, folder by folder, so a stage can prove its files are where the user can open them.
        With `since` (a `_dir_sig()` taken before a command) only the folders that command changed are listed."""
        now = self._dir_sig() if since is not None else None
        out = {}
        for d in SPLIT_DIRS:
            if since is not None and all(now[f"{d}/{sub}"] == since.get(f"{d}/{sub}") for sub in ("md", "json")):
                continue
            base = self.root / d
            md = [x for x in (base / "md").glob("*") if x.is_file()]
            js = [x for x in (base / "json").glob("*") if x.is_file()]
            if md or js:
                out[d] = {"folder": str(base), "md_folder": str(base / "md"), "md_files": len(md), "json_files": len(js)} if since is not None else \
                    {"md_folder": str(base / "md"), "md_files": len(md), "json_folder": str(base / "json"), "json_files": len(js)}
        reports = [x for x in (self.root / "reports").glob("*.md")]
        if reports and (since is None or now["reports"] != since.get("reports")):
            out["reports"] = {"folder": str(self.root / "reports"), "files": len(reports)}
        en = self._enriched_files()
        if en and (since is None or now["enriched"] != since.get("enriched")):
            out["enriched"] = {"folder": str(self.enriched_root), "files": len(en)}
        return out

    def _guard_source(self, target: Path) -> None:
        sp = ((self._state or {}).get("source") or {})
        if sp.get("kind") == "folder":  # everything under the folder is input
            hit = any(sp.get(k) and is_within(Path(sp[k]), target) for k in ("root", "original_path"))
        else:  # one file: only that file
            hit = any(sp.get(k) and os.path.realpath(sp[k]) == os.path.realpath(target) for k in ("path", "original_path"))
        if hit:
            raise BAError("SOURCE_PROTECTED", "Refusing to write to the supplied requirements: they are read-only input.")

    def write_text(self, rel: str, text: str, backup: bool = True) -> None:
        target = self.path(rel)
        self._guard_source(target)
        try:
            if backup and target.exists() and target.read_text(encoding="utf-8") != text:
                b = self.path("state/backups/" + rel.replace("/", "__") + ".bak")
                b.parent.mkdir(parents=True, exist_ok=True)  # a lean staged copy (cowork) does not carry state/backups/
                shutil.copyfile(target, b)
            atomic_write_text(target, text)
        except OSError as e:
            try:
                append_jsonl(self.path("state/audit.jsonl"), self._event("OUTPUT_WRITE_FAILED", path=rel, error=str(e)))
            except Exception:
                pass
            raise BAError("WRITE_FAILED", f"Could not write {rel}: {e}")

    def write_json(self, rel: str, obj, schema_name: str | None = None) -> None:
        if schema_name:
            errs = schema.validate(obj, schema_name)
            if errs:
                raise BAError("SCHEMA_INVALID", f"{rel} failed {schema_name} validation", errors=errs)
        self.write_text(rel, json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=False) + "\n")
        if rel.startswith("index/"):
            self._index = None

    def write_enriched(self, rel: str, data: bytes) -> None:
        """Write one generated copy under the enriched folder (bytes exactly as given; the previous copy is kept in state/backups/)."""
        target = safe_join(self.enriched_root, rel)
        self._guard_source(target)
        try:
            if target.exists() and target.read_bytes() != data:
                b = self.path("state/backups/enriched__" + rel.replace("/", "__") + ".bak")
                b.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(target, b)
            atomic_write_bytes(target, data)
        except OSError as e:
            raise BAError("WRITE_FAILED", f"Could not write the enriched copy {rel}: {e}")

    def remove_enriched(self, rel: str) -> None:
        target = safe_join(self.enriched_root, rel)
        if target.exists():
            b = self.path("state/backups/enriched__" + rel.replace("/", "__") + ".bak")
            b.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(target, b)
            target.unlink()

    def read_json(self, rel: str):
        return read_json(self.path(rel))

    def exists(self, rel: str) -> bool:
        return self.path(rel).exists()

    def sync_docs(self) -> dict | None:
        """Bring the enriched copies up to date with the approved criteria and the gap state. A convenience view of state: a problem
        here never fails the command that changed the state; it is kept in `doc_warnings` and reported by `validate`."""
        if self._syncing or self._state is None:
            return None
        from . import docsync
        self._syncing = True
        try:
            return docsync.refresh(self)
        except BAError as e:
            self.doc_warnings.append(f"{e.code}: {e.message}")
            return None
        finally:
            self._syncing = False

    # ------------------------------------------------------------------ state
    @property
    def state(self) -> dict:
        if self._state is None:
            try:
                st = self.read_json("state/workflow.json")
            except (OSError, json.JSONDecodeError) as e:
                raise BAError("STATE_CORRUPT", f"state/workflow.json unreadable: {e}. Restore from state/backups/.")
            if isinstance(st, dict) and st.get("schema_version") != SCHEMA_VERSION:
                raise BAError("LEGACY_PROJECT", f"This project was created by ba-agent 1.x (schema {st.get('schema_version')}, stage "
                              f"{st.get('stage')}). ba-agent 2.0 works on a requirements folder and does not read BRD/baseline/story "
                              "projects. Keep using ba-agent 1.10.2 for it, or start a new project on the requirements folder.",
                              schema_version=st.get("schema_version"), stage=st.get("stage"), project_id=st.get("project_id"))
            errs = schema.validate(st, "workflow_state")
            if errs:
                raise BAError("STATE_INVALID", "state/workflow.json is invalid", errors=errs)
            self._state = st
        return self._state

    def save_state(self, action: str) -> None:
        st = self._state
        st["last_completed_action"] = action
        st["updated_at"] = ist_now_iso()
        try:
            st["pending_gap_ids"] = [g["gap_id"] for g in self.gaps() if g["status"] != "RESOLVED"] if self._has_register() else []
            st["pending_ac_keys"] = sorted({a["req_key"] for b, a in self.all_acs() if b in PENDING_AC_BUCKETS})
        except BAError:
            pass
        self.write_json("state/workflow.json", st, "workflow_state")

    def set_stage(self, new: str, action: str) -> None:
        cur = self.state["stage"]
        if new in ("PAUSED", "BLOCKED"):
            if cur not in PAUSABLE:
                raise BAError("INVALID_TRANSITION", f"Cannot enter {new} from {cur}.")
            self.state["resume_stage"] = cur
        elif cur in ("PAUSED", "BLOCKED"):
            if new != self.state.get("resume_stage"):
                raise BAError("INVALID_TRANSITION",
                              f"{cur} can only resume to {self.state.get('resume_stage')}, not {new}.")
            self.state["resume_stage"] = None
        else:
            check_transition(STAGE_TRANSITIONS, "Workflow stage", cur, new)
        if cur != new:
            self.audit("STAGE_CHANGED", from_stage=cur, to_stage=new)
        self.state["stage"] = new
        self.save_state(action)

    def reopen_to(self, target: str, reason: str) -> None:
        """Move back to an earlier stage when later work is invalidated (no-op if already earlier)."""
        cur = self.state["stage"]
        if cur in STAGE_ORDER and STAGE_ORDER.index(cur) > STAGE_ORDER.index(target):
            self.audit("WORKFLOW_REOPENED", from_stage=cur, to_stage=target, reason=reason)
            self.set_stage(target, f"reopened-{reason}")

    # ------------------------------------------------------------------ audit
    def _event(self, event_type: str, **fields) -> dict:
        ev = {"event_id": "EV-" + uuid.uuid4().hex[:12], "at_ist": ist_now_iso(), "timezone": "Asia/Kolkata",
              "event_type": event_type, "clock_source": "host-system-clock"}
        ev.update(fields)
        return ev

    def audit(self, event_type: str, **fields) -> dict:
        ev = self._event(event_type, **fields)
        append_jsonl(self.path("state/audit.jsonl"), ev)
        return ev

    def audit_events(self) -> list[dict]:
        return read_jsonl(self.path("state/audit.jsonl"))[0]

    # -------------------------------------------------------------------- ids
    def next_id(self, prefix: str, existing: list[str]) -> str:
        nums = [int(m.group(1)) for x in existing if (m := re.match(rf"^{prefix}-(\d+)", x))]
        return f"{prefix}-{(max(nums) + 1 if nums else 1):03d}"

    # ----------------------------------------------------------- requirements
    def source_root(self) -> Path:
        return Path(self.state["source"]["root"])

    def requirements(self, active_only: bool = True) -> list[dict]:
        return reqindex.entries(self, active_only)

    def requirement(self, ref: str) -> dict:
        return reqindex.by_key(self)[reqindex.resolve(self, ref)]

    def req_key(self, ref: str, active_only: bool = False) -> str:
        return reqindex.resolve(self, ref, active_only)

    def label(self, key: str) -> str:
        return reqindex.label(self, key)

    # ------------------------------------------------------------------- gaps
    def _has_register(self) -> bool:
        return self.exists(GAP_REGISTER)

    def gaps(self) -> list[dict]:
        if not self._has_register():
            return []
        return self.read_json(GAP_REGISTER)["gaps"]

    def gap(self, gid: str) -> dict:
        for g in self.gaps():
            if g["gap_id"] == gid:
                return g
        raise BAError("UNKNOWN_GAP", f"Gap {gid} does not exist.")

    def save_gaps(self, gaps: list[dict], touched: list[str] | None = None) -> None:
        for g in gaps:
            errs = schema.validate(g, "gap")
            if errs:
                raise BAError("SCHEMA_INVALID", f"{g.get('gap_id')} failed gap validation", errors=errs)
        was = {x["gap_id"]: gap_bucket(x) for x in self.gaps()}
        self.write_json(GAP_REGISTER, {"schema_version": SCHEMA_VERSION, "updated_at": ist_now_iso(), "gaps": gaps})
        for g in gaps:
            if touched is None or g["gap_id"] in touched:
                self.store_gap_files(g, was.get(g["gap_id"]))
        self.sync_docs()

    def store_gap_files(self, g: dict, was: str | None = None) -> str:
        """Write the gap's readable (`md/`) and record (`json/`) copies into the folder for its status and remove any copy left
        in another folder, so a gap that is answered, deferred or reopened simply moves. Returns the folder it now lives in."""
        bucket = gap_bucket(g)
        gid = g["gap_id"]
        if was and was != bucket:  # a move: the old copies are gone whether or not this workspace ever held them
            self.vacated.update(f"gaps/{was}/{ext}/{gid}.{ext}" for ext in ("md", "json"))
        self.write_json(f"gaps/{bucket}/{gid}.json", g)
        self.write_text(f"gaps/{bucket}/{gid}.md", render.gap_md(g, self))
        for other in GAP_BUCKETS:
            if other != bucket:
                for ext in ("md", "json"):
                    f = self.path(f"gaps/{other}/{gid}.{ext}")
                    if f.exists():
                        f.unlink()
        return f"gaps/{bucket}"

    def sync_gap_files(self) -> int:
        """Make the folders agree with the register (which is the record): used by `recover`."""
        gaps = self.gaps()
        for g in gaps:
            self.store_gap_files(g)
        return len(gaps)

    def gap_location(self, gid: str) -> dict:
        b = gap_bucket(self.gap(gid))
        return {"folder": f"gaps/{b}", "file": str(self.root / "gaps" / b / "md" / f"{gid}.md")}

    # -------------------------------------------------------------- decisions
    def decisions(self) -> dict[str, dict]:
        """Fold decision-log.jsonl: decision records + status records → id → record with current_status."""
        recs, _ = read_jsonl(self.path("decisions/decision-log.jsonl"))
        out: dict[str, dict] = {}
        for r in recs:
            if r.get("record_type") == "decision":
                out[r["decision_id"]] = dict(r, current_status="ACTIVE")
        for r in recs:
            if r.get("record_type") == "decision_status" and r["decision_id"] in out:
                out[r["decision_id"]]["current_status"] = r["status"]
                out[r["decision_id"]]["status_reason"] = r.get("reason")
        return out

    def render_decision(self, did: str) -> None:
        d = self.decisions()[did]
        self.write_text(f"decisions/{did}.md", render.decision_md(d, d["current_status"], self))

    # ------------------------------------------------------------------- AC
    def all_acs(self) -> list[tuple[str, dict]]:
        """Every readable acceptance-criteria record. An unreadable file is skipped here (it must not take status/validate down
        with it); `unreadable_ac_files` lists it so `validate` and change detection can report it."""
        out = []
        for b in LIVE_AC_BUCKETS:
            for f in self.files(f"acceptance/{b}", "*.json"):
                try:
                    out.append((b, read_json(f)))
                except (ValueError, OSError):
                    continue
        return out

    def unreadable_ac_files(self) -> list[str]:
        bad = []
        for b in LIVE_AC_BUCKETS:
            for f in self.files(f"acceptance/{b}", "*.json"):
                try:
                    read_json(f)
                except (ValueError, OSError):
                    bad.append(f"acceptance/{b}/{f.name}")
        return bad

    def archived_acs(self) -> list[dict]:
        return [read_json(f) for f in self.files("acceptance/archive", "*.json")]

    def save_ac(self, bucket: str, a: dict) -> None:
        name = ac_fname(a["req_key"])
        self.write_json(f"acceptance/{bucket}/{name}.json", a, "ac")
        self.write_text(f"acceptance/{bucket}/{name}.md", render.ac_md(a, self))
        self.sync_docs()

    def remove_ac_files(self, bucket: str, key: str) -> None:
        name = ac_fname(key)
        for ext in ("json", "md"):
            f = self.path(f"acceptance/{bucket}/{name}.{ext}")
            self.vacated.add(physical(f"acceptance/{bucket}/{name}.{ext}"))
            if f.exists():
                f.unlink()
        if bucket == "approved":  # the enriched copy shows approved criteria only: it must stop showing these
            self.sync_docs()

    def archive_ac(self, a: dict, status: str = "SUPERSEDED") -> None:
        rec = dict(a, status=status)
        base = f"acceptance/archive/{ac_fname(a['req_key'])}.v{a['version']}"
        self.write_json(base + ".json", rec)
        self.write_text(base + ".md", render.ac_md(rec, self), backup=False)

    # ------------------------------------------------------------------ impact
    def flag_acs(self, keys: set[str], reason: str, cause: str, gap_id: str | None = None, change_id: str | None = None) -> list[str]:
        """Flag every non-archived AC record for any of `keys` (or listing `gap_id` as an open gap) for impact review."""
        flagged = []
        for bucket, a in self.all_acs():
            if a["req_key"] in keys or (gap_id and gap_id in [r["gap_id"] for r in a.get("open_gap_refs", [])]):
                cur = a.get("impact_review") or {}
                if cur.get("pending") and cur.get("reason") == reason:
                    continue  # idempotent on replay
                a["impact_review"] = {"pending": True, "reason": reason, "cause": cause, "at_ist": ist_now_iso(), "change_id": change_id}
                a["review_history"].append({"action": "IMPACT_FLAGGED", "version": a["version"], "at_ist": ist_now_iso(), "note": reason,
                                            "change_id": change_id})
                a["updated_at"] = ist_now_iso()
                self.save_ac(bucket, a)
                self.audit("AC_IMPACT_FLAGGED", req_key=a["req_key"], version=a["version"], bucket=bucket, cause=cause, gap_id=gap_id,
                           change_id=change_id)
                flagged.append(a["req_key"])
        return flagged
