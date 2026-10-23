"""Project: the artifact store, workflow state machine and audit log."""
from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from pathlib import Path

from . import ingest, render, schema, snapshots
from .util import (BAError, append_jsonl, atomic_write_text, ist_now_iso, is_within, read_json, read_jsonl,
                   safe_join)

FAULT_HOOK = None  # tests set this to simulate a crash at named points


def fault(point: str) -> None:
    if FAULT_HOOK:
        FAULT_HOOK(point)


STAGES = ["INITIALIZED", "BRD_PARSED", "REQUIREMENTS_READY", "GAP_ANALYSIS", "WAITING_FOR_GAP_INPUT",
          "REQUIREMENTS_BASELINED", "STORIES_READY", "WAITING_FOR_STORY_REVIEW", "COMPLETED", "BLOCKED", "PAUSED"]
_REOPEN = {"GAP_ANALYSIS", "WAITING_FOR_GAP_INPUT"}
STAGE_TRANSITIONS = {
    "INITIALIZED": {"BRD_PARSED"},
    "BRD_PARSED": {"REQUIREMENTS_READY"},
    "REQUIREMENTS_READY": {"GAP_ANALYSIS", "BRD_PARSED"},
    "GAP_ANALYSIS": {"WAITING_FOR_GAP_INPUT", "REQUIREMENTS_BASELINED", "REQUIREMENTS_READY"},
    "WAITING_FOR_GAP_INPUT": {"GAP_ANALYSIS", "REQUIREMENTS_BASELINED"},
    "REQUIREMENTS_BASELINED": {"STORIES_READY"} | _REOPEN,
    "STORIES_READY": {"WAITING_FOR_STORY_REVIEW", "REQUIREMENTS_BASELINED"} | _REOPEN,
    "WAITING_FOR_STORY_REVIEW": {"COMPLETED", "STORIES_READY", "REQUIREMENTS_BASELINED"} | _REOPEN,
    "COMPLETED": {"WAITING_FOR_STORY_REVIEW", "STORIES_READY", "REQUIREMENTS_BASELINED", "REQUIREMENTS_READY"} | _REOPEN,
}
# PAUSED / BLOCKED may be entered from any stage except themselves and only exit to the saved resume_stage.
PAUSABLE = set(STAGES) - {"PAUSED", "BLOCKED"}

GAP_STATUSES = ["OPEN", "WAITING_FOR_ANSWER", "RESOLVED", "DEFERRED", "REOPENED", "BLOCKED"]
GAP_TRANSITIONS = {
    "OPEN": {"WAITING_FOR_ANSWER", "DEFERRED", "BLOCKED"},
    "WAITING_FOR_ANSWER": {"RESOLVED", "DEFERRED", "OPEN", "BLOCKED"},
    "RESOLVED": {"REOPENED"},
    "DEFERRED": {"WAITING_FOR_ANSWER"},
    "REOPENED": {"WAITING_FOR_ANSWER", "DEFERRED", "BLOCKED"},
    "BLOCKED": {"OPEN", "WAITING_FOR_ANSWER"},
}
STORY_TRANSITIONS = {
    "CANDIDATE": {"IN_REVIEW", "APPROVED", "DEFERRED", "CHANGES_REQUESTED", "SUPERSEDED", "BLOCKED"},
    "IN_REVIEW": {"APPROVED", "DEFERRED", "CHANGES_REQUESTED", "CANDIDATE", "SUPERSEDED", "BLOCKED"},
    "CHANGES_REQUESTED": {"SUPERSEDED", "CANDIDATE", "DEFERRED"},
    "APPROVED": {"SUPERSEDED"},
    "DEFERRED": {"CANDIDATE", "SUPERSEDED"},
    "BLOCKED": {"CANDIDATE", "SUPERSEDED"},
    "SUPERSEDED": set(),
}

REQ_DIR = "atomic-requirements"  # one folder the user can open; older projects used "requirements" (migrated on open)
LEGACY_REQ_DIR = "requirements"

DIRS = ["state", "state/backups", "state/pending", "extracted", REQ_DIR, "gaps", "decisions", "baselines",
        "stories/candidates", "stories/approved", "stories/unapproved", "stories/deferred", "stories/archive", "reports",
        "changes", "snapshots/brd", "snapshots/stories"]
UNRESOLVED_OK_STORY_BUCKETS = ("candidates", "approved", "unapproved", "deferred")


def slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return (s or "ba-project")[:60]


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

    # ------------------------------------------------------------ lifecycle
    @classmethod
    def create(cls, root, brd, name: str | None = None, copy_source: bool = False) -> "Project":
        root, brd = Path(root).resolve(), Path(brd).resolve()
        meta = ingest.extract(brd)  # fail on unsupported/unreadable BEFORE creating anything
        if (root / "state" / "workflow.json").exists():
            raise BAError("ALREADY_INITIALIZED", f"{root} already contains a BA project. Use resume/status instead.")
        if root == brd or root.is_file():
            raise BAError("BAD_ROOT", "Project root must be a folder, not the BRD file.")
        try:
            root.mkdir(parents=True, exist_ok=True)
            for d in DIRS:
                (root / d).mkdir(parents=True, exist_ok=True)
            probe = root / "state" / ".write-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as e:
            raise BAError("OUTPUT_NOT_WRITABLE", f"Cannot write to project folder {root}: {e}")
        p = cls(root)
        source_path, rel = brd, None
        if is_within(root, brd):
            rel = os.path.relpath(brd, root).replace("\\", "/")
        if copy_source and not is_within(root, brd):
            dest = root / "source" / brd.name
            dest.parent.mkdir(exist_ok=True)
            shutil.copy2(brd, dest)
            source_path, rel = dest, f"source/{brd.name}"
        now = ist_now_iso()
        state = {
            "schema_version": "1.0", "project_id": slugify(name or brd.stem), "workflow_id": "wf-" + uuid.uuid4().hex[:12],
            "stage": "INITIALIZED", "resume_stage": None,
            "source": {"path": str(source_path), "relative_path": rel, "original_path": str(brd),
                       "fingerprint": meta["fingerprint"], "text_fingerprint": meta["text_fingerprint"],
                       "material_fingerprint": meta["material_fingerprint"], "format": meta["format"]},
            "baseline_version": None, "decision_owner": None, "decision_owner_unknown": None,
            "decision_owner_history": [], "gap_analysis_complete": False,
            "policy": {"blocking_priorities": ["CRITICAL", "HIGH"], "accept_nonblocking_carry_forward": False,
                       "deferral_revisit_required_priorities": [], "accepted_by": None},
            "impact_review": {"pending": False, "reasons": []},
            "pending_gap_ids": [], "pending_story_ids": [], "last_completed_action": "project-initialized",
            "created_at": now, "updated_at": now,
        }
        p._state = state
        p.save_state("project-initialized")
        p.write_source_metadata(meta)
        snapshots.record(p, meta, "initial BRD")
        p.audit("WORKFLOW_STARTED", source_format=meta["format"], source_fingerprint=meta["fingerprint"],
                parser=meta["parser"], copied_source=bool(copy_source and rel and rel.startswith("source/")))
        p.set_stage("BRD_PARSED", "brd-parsed")
        if meta["injection_suspects"]:
            p.audit("SOURCE_INSTRUCTION_LIKE_CONTENT", count=len(meta["injection_suspects"]),
                    lines=[h["line"] for h in meta["injection_suspects"]])
        return p

    @classmethod
    def open(cls, root) -> "Project":
        p = cls(Path(root))
        if not (p.root / "state" / "workflow.json").exists():
            raise BAError("NOT_A_PROJECT", f"No BA project found at {p.root} (missing state/workflow.json).")
        p._migrate_legacy_layout()
        p.state  # load + validate
        return p

    def _migrate_legacy_layout(self) -> None:
        self._migrate_unapproved_stories()
        old, new = self.root / LEGACY_REQ_DIR, self.root / REQ_DIR
        if old.is_dir() and not any(new.glob("REQ-*")) and any(old.glob("REQ-*")):
            try:
                if new.is_dir():
                    new.rmdir()  # empty dir only; leave it alone if rmdir fails
                old.rename(new)
            except OSError as e:
                raise BAError("MIGRATION_FAILED", f"Could not rename {old} to {new}: {e}")

    def _migrate_unapproved_stories(self) -> None:
        """Before 1.1.0 'not approved' stories stayed in stories/candidates/ with status CHANGES_REQUESTED."""
        cand = self.root / "stories" / "candidates"
        if not cand.is_dir():
            return
        dest = self.root / "stories" / "unapproved"
        for f in sorted(cand.glob("US-*.json")):
            try:
                if json.loads(f.read_text(encoding="utf-8")).get("status") != "CHANGES_REQUESTED":
                    continue
                dest.mkdir(parents=True, exist_ok=True)
                for ext in ("json", "md"):
                    src = cand / f"{f.stem}.{ext}"
                    if src.exists():
                        src.replace(dest / src.name)
            except (OSError, ValueError) as e:
                raise BAError("MIGRATION_FAILED", f"Could not move {f.name} to stories/unapproved/: {e}")

    # ----------------------------------------------------------------- paths / IO
    def path(self, rel: str) -> Path:
        return safe_join(self.root, rel)

    def _guard_source(self, target: Path) -> None:
        st = self._state or {}
        for key in ("path", "original_path"):
            sp = (st.get("source") or {}).get(key)
            if sp and os.path.realpath(sp) == os.path.realpath(target):
                raise BAError("SOURCE_PROTECTED", "Refusing to write to the original BRD.")

    def write_text(self, rel: str, text: str, backup: bool = True) -> None:
        target = self.path(rel)
        self._guard_source(target)
        try:
            if backup and target.exists() and target.read_text(encoding="utf-8") != text:
                b = self.path("state/backups/" + rel.replace("/", "__") + ".bak")
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

    def read_json(self, rel: str):
        return read_json(self.path(rel))

    def exists(self, rel: str) -> bool:
        return self.path(rel).exists()

    # ------------------------------------------------------------------ state
    @property
    def state(self) -> dict:
        if self._state is None:
            try:
                self._state = self.read_json("state/workflow.json")
            except (OSError, json.JSONDecodeError) as e:
                raise BAError("STATE_CORRUPT", f"state/workflow.json unreadable: {e}. Restore from state/backups/.")
            errs = schema.validate(self._state, "workflow_state")
            if errs:
                raise BAError("STATE_INVALID", "state/workflow.json is invalid", errors=errs)
        return self._state

    def save_state(self, action: str) -> None:
        st = self._state
        st["last_completed_action"] = action
        st["updated_at"] = ist_now_iso()
        try:
            st["pending_gap_ids"] = [g["gap_id"] for g in self.gaps() if g["status"] != "RESOLVED"] if self._has_register() else []
            st["pending_story_ids"] = sorted({s["story_id"] for b, s in self.all_stories()
                                              if b in ("candidates", "unapproved", "deferred")})
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
        order = ["INITIALIZED", "BRD_PARSED", "REQUIREMENTS_READY", "GAP_ANALYSIS", "WAITING_FOR_GAP_INPUT",
                 "REQUIREMENTS_BASELINED", "STORIES_READY", "WAITING_FOR_STORY_REVIEW", "COMPLETED"]
        cur = self.state["stage"]
        if cur in order and order.index(cur) > order.index(target):
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

    # ----------------------------------------------------------------- source
    def write_source_metadata(self, meta: dict) -> None:
        doc = {"source": {k: self.state["source"].get(k) for k in ("path", "original_path", "relative_path")},
               "format": meta["format"], "fingerprint": meta["fingerprint"], "text_fingerprint": meta["text_fingerprint"],
               "parser": meta["parser"], "processed_at_ist": ist_now_iso(), "sections": meta["sections"],
               "injection_suspects": meta["injection_suspects"], "warnings": meta["warnings"]}
        self.write_json("extracted/source-metadata.json", doc)
        self.write_text("extracted/brd-outline.md", ingest.outline_markdown(Path(self.state["source"]["path"]).name, meta))

    def source_metadata(self) -> dict:
        return self.read_json("extracted/source-metadata.json")

    def sections(self) -> dict[str, dict]:
        return {s["section_id"]: s for s in self.source_metadata()["sections"]}

    def load_source(self, verify: bool = True) -> dict:
        meta = ingest.extract(Path(self.state["source"]["path"]))
        if verify and meta["fingerprint"] != self.state["source"]["fingerprint"]:
            raise BAError("SOURCE_CHANGED", "The BRD no longer matches the recorded fingerprint. "
                          "Run `source check` to assess the impact before continuing.")
        return meta

    # ----------------------------------------------------------- requirements
    def req_ids(self) -> list[str]:
        return sorted(p.stem for p in self.path(REQ_DIR).glob("REQ-*.json"))

    def requirements(self) -> list[dict]:
        return [self.read_json(f"{REQ_DIR}/{i}.json") for i in self.req_ids()]

    def requirement(self, rid: str) -> dict:
        if not re.match(r"^REQ-\d{3,}$", rid or "") or not self.exists(f"{REQ_DIR}/{rid}.json"):
            raise BAError("UNKNOWN_REQUIREMENT", f"Requirement {rid} does not exist.")
        return self.read_json(f"{REQ_DIR}/{rid}.json")

    def save_requirement(self, r: dict) -> None:
        self.path(REQ_DIR).mkdir(parents=True, exist_ok=True)
        self.write_json(f"{REQ_DIR}/{r['requirement_id']}.json", r, "requirement")
        self.write_text(f"{REQ_DIR}/{r['requirement_id']}.md", render.requirement_md(r))

    # ------------------------------------------------------------------- gaps
    def _has_register(self) -> bool:
        return self.exists("gaps/gap-register.json")

    def gaps(self) -> list[dict]:
        if not self._has_register():
            return []
        return self.read_json("gaps/gap-register.json")["gaps"]

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
        self.write_json("gaps/gap-register.json", {"schema_version": "1.0", "updated_at": ist_now_iso(), "gaps": gaps})
        for g in gaps:
            if touched is None or g["gap_id"] in touched:
                self.write_text(f"gaps/{g['gap_id']}.md", render.gap_md(g))

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
        self.write_text(f"decisions/{did}.md", render.decision_md(d, d["current_status"]))

    # ----------------------------------------------------------------- stories
    def all_stories(self) -> list[tuple[str, dict]]:
        """Every readable story. An unreadable file is skipped here (it must not take status/validate down with it);
        `unreadable_story_files` lists it so `validate` and change detection can report it."""
        out = []
        for b in UNRESOLVED_OK_STORY_BUCKETS:
            for f in sorted(self.path(f"stories/{b}").glob("US-*.json")):
                try:
                    out.append((b, read_json(f)))
                except (ValueError, OSError):
                    continue
        return out

    def unreadable_story_files(self) -> list[str]:
        bad = []
        for b in UNRESOLVED_OK_STORY_BUCKETS:
            for f in sorted(self.path(f"stories/{b}").glob("US-*.json")):
                try:
                    read_json(f)
                except (ValueError, OSError):
                    bad.append(f"stories/{b}/{f.name}")
        return bad

    def archived_stories(self) -> list[dict]:
        return [read_json(f) for f in sorted(self.path("stories/archive").glob("US-*.json"))]

    def save_story(self, bucket: str, s: dict) -> None:
        self.write_json(f"stories/{bucket}/{s['story_id']}.json", s, "story")
        self.write_text(f"stories/{bucket}/{s['story_id']}.md", render.story_md(s))

    def remove_story_files(self, bucket: str, sid: str) -> None:
        for ext in ("json", "md"):
            f = self.path(f"stories/{bucket}/{sid}.{ext}")
            if f.exists():
                f.unlink()

    def archive_story(self, s: dict, status: str = "SUPERSEDED") -> None:
        rec = dict(s, status=status)
        base = f"stories/archive/{s['story_id']}.v{s['version']}"
        self.write_json(base + ".json", rec)
        self.write_text(base + ".md", render.story_md(rec), backup=False)

    # ---------------------------------------------------------------- baseline
    def baseline_manifest(self) -> dict:
        if self.exists("baselines/baseline-manifest.json"):
            return self.read_json("baselines/baseline-manifest.json")
        return {"schema_version": "1.0", "current_version": None, "versions": [], "stale": False, "stale_reason": None}

    def approved_baseline(self) -> dict | None:
        m = self.baseline_manifest()
        v = self.state.get("baseline_version")
        if v is None:
            return None
        ent = next((x for x in m["versions"] if x["version"] == v and x["status"] == "APPROVED"), None)
        return self.read_json(f"baselines/baseline-v{v}.json") if ent else None

    def mark_baseline_stale(self, reason: str) -> None:
        m = self.baseline_manifest()
        if m["versions"] and not m["stale"]:
            m["stale"], m["stale_reason"] = True, reason
            self.write_json("baselines/baseline-manifest.json", m)
            self.audit("BASELINE_MARKED_STALE", reason=reason)

    # ------------------------------------------------------------------ impact
    def flag_stories(self, req_ids: set[str], reason: str, cause: str, gap_id: str | None = None,
                     change_id: str | None = None) -> list[str]:
        """Flag every non-archived story that links to any of req_ids (or lists `gap_id` as an open gap) for impact review."""
        flagged = []
        for bucket, s in self.all_stories():
            if set(s["requirement_ids"]) & req_ids or (gap_id and gap_id in s.get("open_gap_ids", [])):
                cur = s.get("impact_review") or {}
                if cur.get("pending") and cur.get("reason") == reason:
                    continue  # idempotent on replay
                s["impact_review"] = {"pending": True, "reason": reason, "cause": cause, "at_ist": ist_now_iso(), "change_id": change_id}
                s["review_history"].append({"action": "IMPACT_FLAGGED", "version": s["version"],
                                            "at_ist": ist_now_iso(), "note": reason, "change_id": change_id})
                s["updated_at"] = ist_now_iso()
                self.save_story(bucket, s)
                self.audit("STORY_IMPACT_FLAGGED", story_id=s["story_id"], version=s["version"], bucket=bucket,
                           requirement_ids=sorted(set(s["requirement_ids"]) & req_ids), cause=cause, gap_id=gap_id, change_id=change_id)
                flagged.append(s["story_id"])
        return flagged
