#!/usr/bin/env python3
"""Safely write a helper/input file inside the project root.

    python safe_write.py --root <project> --path state/inbox/reqs.json --content-file <local file>
    python safe_write.py --root <project> --path state/inbox/reqs.json --stdin < content

Guarantees: path must be relative and stay inside the project root (no traversal / symlink escape), the
original BRD can never be the target, writes are atomic (temp file + replace), and a previous version is backed up.
Only `state/inbox/` and `reports/` are writable here; authoritative state is changed only through ba.py.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ba_lib.project import Project  # noqa: E402
from ba_lib.util import BAError  # noqa: E402

ALLOWED = ("state/inbox/", "reports/")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--path", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--content-file")
    g.add_argument("--stdin", action="store_true")
    a = ap.parse_args(argv)
    try:
        rel = a.path.replace("\\", "/")
        if not rel.startswith(ALLOWED):
            raise BAError("PATH_NOT_ALLOWED", f"safe_write only writes under {ALLOWED}; got {rel!r}")
        p = Project.open(a.root)
        text = sys.stdin.read() if a.stdin else Path(a.content_file).read_text(encoding="utf-8-sig")
        p.write_text(rel, text)
        print(json.dumps({"ok": True, "written": rel, "bytes": len(text.encode("utf-8"))}))
        return 0
    except BAError as e:
        print(json.dumps({"ok": False, "error": {"code": e.code, "message": e.message}}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
