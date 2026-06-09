#!/usr/bin/env python3
"""Entry point for the pdf2fg CLI. Run from a ruleset folder:

    python pdf2fg.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pdf2fg.cli import main

if __name__ == "__main__":
    sys.exit(main())
