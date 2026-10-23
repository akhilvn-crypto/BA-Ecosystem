#!/usr/bin/env python3
"""Entry point for the Interactive BA plugin's deterministic core. Run: python ba.py --help"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ba_lib.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
