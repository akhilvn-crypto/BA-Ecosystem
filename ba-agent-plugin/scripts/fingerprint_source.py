#!/usr/bin/env python3
"""Print the SHA-256 content fingerprint of a source file (read-only): python fingerprint_source.py FILE"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ba_lib.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["fingerprint"] + sys.argv[1:]))
