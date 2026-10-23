#!/usr/bin/env python3
"""Thin wrapper: `python validate_artifacts.py --root <project>` == `python ba.py --root <project> validate`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ba_lib.cli import main  # noqa: E402

if __name__ == "__main__":
    args = sys.argv[1:]
    sys.exit(main(args + ["validate"]) if "--root" in args else main(["validate"]))
