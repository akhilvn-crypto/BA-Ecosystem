#!/usr/bin/env python3
"""Build an upload archive for Claude Cowork / claude.ai (Customize → Plugins → Add → Upload plugin).

    python scripts/package_plugin.py [--out DIR]

Writes <out>/ba-agent-<version>.zip with `.claude-plugin/plugin.json` at the zip root (the documented
requirement). Excludes tests, caches and VCS files. Read-only on the plugin folder.
"""
import argparse
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE_DIRS = {"__pycache__", ".git", "tests", "dist", ".pytest_cache"}
EXCLUDE_SUFFIX = {".pyc", ".tmp", ".bak"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT.parent / "dist"))
    a = ap.parse_args(argv)
    manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{manifest['name']}-{manifest['version']}.zip"
    n = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(ROOT.rglob("*")):
            rel = f.relative_to(ROOT)
            if f.is_dir() or EXCLUDE_DIRS & set(rel.parts) or f.suffix in EXCLUDE_SUFFIX:
                continue
            z.write(f, rel.as_posix())
            n += 1
    with zipfile.ZipFile(target) as z:
        assert ".claude-plugin/plugin.json" in z.namelist()
    print(json.dumps({"ok": True, "archive": str(target), "files": n, "bytes": target.stat().st_size}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
