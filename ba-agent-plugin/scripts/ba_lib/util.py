"""Shared primitives: IST clock, hashing, safe/atomic file writes, JSONL, errors.

Standard library only. IST (Asia/Kolkata) has no DST and a fixed +05:30 offset, so a
fixed-offset tzinfo is exact and does not depend on the tz database being installed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ_NAME = "Asia/Kolkata"
IST = timezone(timedelta(hours=5, minutes=30), "IST")
IST_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+05:30$")
MIN_SANE_YEAR = 2025

_clock = None  # test hook: callable returning an aware datetime


class BAError(Exception):
    """Expected, user-reportable failure. `code` is a stable machine-readable string."""

    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def set_clock(fn) -> None:
    """Install a clock for in-process tests. Never used by the CLI."""
    global _clock
    _clock = fn


def now_ist() -> datetime:
    dt = _clock() if _clock else datetime.now(timezone.utc)
    if dt.tzinfo is None:
        raise BAError("CLOCK_UNRELIABLE", "Clock returned a timezone-naive time.")
    dt = dt.astimezone(IST)
    if dt.year < MIN_SANE_YEAR:
        raise BAError("CLOCK_UNRELIABLE",
                      f"System clock reads {dt.year}, which is implausible; refusing to record audit timestamps.")
    return dt


def ist_now_iso() -> str:
    return now_ist().replace(microsecond=0).isoformat()


def is_ist_iso(value) -> bool:
    return isinstance(value, str) and bool(IST_RE.match(value))


def human_ist(iso: str | None) -> str:
    if not iso:
        return "—"
    dt = datetime.fromisoformat(iso)
    return dt.strftime("%d %b %Y, %H:%M:%S") + " IST (Asia/Kolkata, UTC+05:30)"


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


# ---------------------------------------------------------------- filesystem

def is_within(root: Path, target: Path) -> bool:
    try:
        root_r = Path(os.path.realpath(root))
        target_r = Path(os.path.realpath(target))
        return os.path.commonpath([str(root_r), str(target_r)]) == str(root_r)
    except ValueError:  # different drives on Windows
        return False


def safe_join(root: Path, rel: str) -> Path:
    """Resolve `rel` under `root`, rejecting absolute paths, traversal and symlink escapes."""
    if not rel or os.path.isabs(rel) or re.match(r"^[A-Za-z]:", rel):
        raise BAError("PATH_ESCAPE", f"Path must be relative to the project root: {rel!r}")
    parts = Path(rel.replace("\\", "/")).parts
    if ".." in parts:
        raise BAError("PATH_ESCAPE", f"Path traversal is not allowed: {rel!r}")
    target = root.joinpath(*parts)
    if not is_within(root, target):
        raise BAError("PATH_ESCAPE", f"Path resolves outside the project root (symlink?): {rel!r}")
    return target


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix="." + path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.replace("\r\n", "\n").encode("utf-8"))


def append_jsonl(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n"
    with open(path, "ab") as f:
        f.write(line.encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())


def read_jsonl(path: Path):
    """Return (records, errors). A torn/invalid line is reported, not fatal."""
    records, errors = [], []
    if not path.exists():
        return records, errors
    with open(path, "r", encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                errors.append(f"{path.name} line {n}: invalid JSON ({e.msg})")
    return records, errors


def read_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


PLACEHOLDER_NAMES = {"", "unknown", "n/a", "na", "none", "tbd", "tba", "claude", "assistant", "ai", "model",
                     "user", "the user", "me", "i", "agent", "bot", "system", "anonymous", "null"}


def require_identity(name, role, what: str) -> tuple[str, str]:
    """Reject empty or obviously invented identities. Cannot prove a person is real."""
    n, r = normalize_ws(name or ""), normalize_ws(role or "")
    if n.lower() in PLACEHOLDER_NAMES or len(n) < 2:
        raise BAError("IDENTITY_REQUIRED", f"{what}: a real person's name is required (got {name!r}). "
                      "Ask the user; never infer it from account metadata.")
    if r.lower() in PLACEHOLDER_NAMES or len(r) < 2:
        raise BAError("IDENTITY_REQUIRED", f"{what}: a role is required (got {role!r}). Ask the user.")
    return n, r


def same_person(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and normalize_ws(a).lower() == normalize_ws(b).lower()
