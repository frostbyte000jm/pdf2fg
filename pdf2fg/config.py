"""Paths, constants and Rules File loading/saving.

Everything is resolved relative to the *current working directory* - i.e. the
folder the CLI is launched from. That folder is one "ruleset" (a copy of the
templateTemplate). It is expected to contain:

    rules.json                 (the Rules File - created during Setup)
    <your-sourcebook>.pdf      (the PDF to convert)
    sourcebook.md              (generated; the working Markdown)

The FG campaign folder (with db.xml and images/) lives elsewhere on disk and
its location is stored inside rules.json.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

RULES_FILENAME = "rules.json"
SOURCEBOOK_FILENAME = "sourcebook.md"
RAW_SCAN_FILENAME = "raw_scan.md"


def cwd() -> Path:
    return Path.cwd()


def rules_path() -> Path:
    return cwd() / RULES_FILENAME


def sourcebook_path() -> Path:
    return cwd() / SOURCEBOOK_FILENAME


def raw_scan_path() -> Path:
    return cwd() / RAW_SCAN_FILENAME


def find_pdfs() -> list[Path]:
    return sorted(p for p in cwd().glob("*.pdf"))


# ---------------------------------------------------------------------------
# Rules File
# ---------------------------------------------------------------------------

DEFAULT_RULES: dict = {
    "_comment": "Edit this file to teach the converter how YOUR pdf is laid out. "
                "Run Setup (menu) to regenerate raw_scan.md with stats + AI help.",
    "campaign_folder": "",
    "images_subfolder": "images",
    "pdf_file": "",
    "page_offset": 0,
    "fonts": {
        "bold": [],
        "italic": [],
        "bold_italic": [],
        "regular": [],
    },
    "headings": {
        "page_title_min_size": 16.0,
        "heading_min_size": 12.0,
        "body_size": 9.0,
    },
    "columns": {
        "mode": "auto",
        "count": 2,
        "gutter_x": None,
    },
    "lists": {
        "bullet_glyphs": ["•", "▪", "●", "◦", "‣", "-"],
        "bullet_fonts": [],
    },
    "paragraphs": {
        # Distinguish a wrapped "next line" of the same paragraph from a real
        # paragraph break ("line break"). Within a paragraph, wrapped lines sit
        # one line-pitch apart; a new paragraph either adds extra vertical space
        # or indents its first line.
        "detect_breaks": True,
        # New paragraph when the top-to-top distance between two body lines is
        # greater than gap_ratio x the page's typical line pitch.
        "gap_ratio": 1.3,
        # New paragraph when the first line is indented at least this many points
        # past the column's left edge (for books that indent instead of space).
        # 0 disables indent detection.
        "indent_pts": 0.0,
    },
    "tables": {"detect": True},
    "underline": {"detect": True, "max_gap": 3.0},
    "toc": {"chapter_fonts": []},
    "ignore_rotated_text": True,
    "size_rounding": 1,
}


@dataclass
class Rules:
    data: dict = field(default_factory=lambda: json.loads(json.dumps(DEFAULT_RULES)))

    # ---- convenience accessors -------------------------------------------
    @property
    def campaign_folder(self) -> Path:
        return Path(self.data.get("campaign_folder", ""))

    @property
    def images_dir(self) -> Path:
        return self.campaign_folder / self.data.get("images_subfolder", "images")

    @property
    def db_path(self) -> Path:
        return self.campaign_folder / "db.xml"

    @property
    def pdf_path(self) -> Path:
        pf = self.data.get("pdf_file") or ""
        p = Path(pf)
        if not p.is_absolute():
            p = cwd() / pf
        return p

    @property
    def page_offset(self) -> int:
        return int(self.data.get("page_offset", 0) or 0)

    def font_role(self, fontname: str) -> str:
        """Return 'bold' | 'italic' | 'bold_italic' | 'regular' for a span font.

        Matches on the suffix after a subset prefix (e.g. 'GXZPEU+Exo2-Bold'
        -> 'Exo2-Bold'). Falls back to name heuristics if the Rules File has
        not listed the font yet.
        """
        base = fontname.split("+", 1)[-1]
        fonts = self.data.get("fonts", {})
        for role in ("bold_italic", "bold", "italic", "regular"):
            for f in fonts.get(role, []):
                if f and (f == base or f == fontname or f in base):
                    return role
        # heuristic fallback
        low = base.lower()
        is_b = "bold" in low or "black" in low or "heavy" in low or "semibold" in low
        is_i = "italic" in low or "oblique" in low
        if is_b and is_i:
            return "bold_italic"
        if is_b:
            return "bold"
        if is_i:
            return "italic"
        return "regular"


def load_rules() -> Rules | None:
    p = rules_path()
    if not p.exists():
        return None
    with open(p, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    # merge defaults for forward-compat
    merged = json.loads(json.dumps(DEFAULT_RULES))
    _deep_update(merged, data)
    return Rules(merged)


def save_rules(rules: Rules) -> None:
    with open(rules_path(), "w", encoding="utf-8") as fh:
        json.dump(rules.data, fh, indent=2, ensure_ascii=False)


def rules_is_complete(rules: Rules | None) -> bool:
    """A Rules File counts as 'complete' once the user has pointed it at a
    campaign folder and a PDF and listed at least the bold/regular fonts."""
    if rules is None:
        return False
    d = rules.data
    if not d.get("campaign_folder") or not d.get("pdf_file"):
        return False
    fonts = d.get("fonts", {})
    return bool(fonts.get("regular") or fonts.get("bold"))


def _deep_update(base: dict, new: dict) -> dict:
    for k, v in new.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v
    return base
