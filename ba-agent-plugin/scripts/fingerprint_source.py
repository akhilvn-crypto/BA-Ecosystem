#!/usr/bin/env python3
"""Print the SHA-256 content fingerprint of a source file (read-only): python fingerprint_source.py FILE"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # the plugin folder may be read-only or cached (Cowork): never write __pycache__ into it
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ba_lib.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["fingerprint"] + sys.argv[1:]))
