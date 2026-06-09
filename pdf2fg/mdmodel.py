"""Parse sourcebook.md into a Chapter/Subchapter/Page tree.

Headings:  #  Chapter   |   ## Subchapter   |   ### Page
Everything between a ### Page heading and the next heading of level <= 3 is
that page's *body* (raw markdown lines, kept verbatim for splicing).

This model is shared by the content inserter (option 2) and the
Markdown -> Fantasy Grounds converter (option 4).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")


@dataclass
class Page:
    title: str
    chapter_index: int          # 1-based ordinal of the parent chapter
    sub_index: int              # 1-based ordinal of the subchapter within chapter
    page_index: int             # 1-based ordinal of the page within subchapter
    heading_line: int           # line number (0-based) of the ### heading
    body_start: int = 0         # first body line (after heading)
    body_end: int = 0           # exclusive end (start of next heading / EOF)
    body: list[str] = field(default_factory=list)


@dataclass
class Subchapter:
    title: str
    pages: list[Page] = field(default_factory=list)


@dataclass
class Chapter:
    title: str
    subchapters: list[Subchapter] = field(default_factory=list)


@dataclass
class Document:
    lines: list[str]
    chapters: list[Chapter] = field(default_factory=list)
    pages: list[Page] = field(default_factory=list)   # flat, in document order


def parse_text(text: str) -> Document:
    lines = text.splitlines()
    doc = Document(lines=lines)

    chapters: list[Chapter] = []
    flat: list[Page] = []
    cur_ch: Chapter | None = None
    cur_sub: Subchapter | None = None
    ch_ord = 0
    sub_ord = 0
    page_ord = 0

    # First pass: identify headings and their line numbers.
    heading_positions: list[tuple[int, int, str]] = []  # (lineno, level, title)
    in_code = False
    for i, ln in enumerate(lines):
        if ln.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        m = _HEADING.match(ln)
        if m:
            heading_positions.append((i, len(m.group(1)), m.group(2)))

    for idx, (lineno, level, title) in enumerate(heading_positions):
        if level == 1:
            ch_ord += 1
            sub_ord = 0
            cur_ch = Chapter(title=title)
            chapters.append(cur_ch)
            cur_sub = None
        elif level == 2:
            if cur_ch is None:
                ch_ord += 1
                cur_ch = Chapter(title="Chapter")
                chapters.append(cur_ch)
            sub_ord += 1
            page_ord = 0
            cur_sub = Subchapter(title=title)
            cur_ch.subchapters.append(cur_sub)
        elif level == 3:
            if cur_ch is None:
                ch_ord += 1
                cur_ch = Chapter(title="Chapter")
                chapters.append(cur_ch)
            if cur_sub is None:
                sub_ord += 1
                cur_sub = Subchapter(title="Subchapter")
                cur_ch.subchapters.append(cur_sub)
            page_ord += 1
            # body span: from next line to the next level<=3 heading
            body_start = lineno + 1
            body_end = len(lines)
            for nlineno, nlevel, _ in heading_positions[idx + 1:]:
                if nlevel <= 3:
                    body_end = nlineno
                    break
            pg = Page(title=title, chapter_index=ch_ord, sub_index=sub_ord,
                      page_index=page_ord, heading_line=lineno,
                      body_start=body_start, body_end=body_end,
                      body=lines[body_start:body_end])
            cur_sub.pages.append(pg)
            flat.append(pg)
        # deeper headings (####+) are body content, ignored here

    doc.chapters = chapters
    doc.pages = flat
    return doc


def parse_file(path: Path) -> Document:
    return parse_text(Path(path).read_text(encoding="utf-8"))


def splice_page_body(path: Path, page: Page, new_body_lines: list[str],
                     mode: str = "replace") -> None:
    """Rewrite a page's body in the file. mode = 'replace' | 'append'."""
    text = Path(path).read_text(encoding="utf-8")
    lines = text.splitlines()
    if mode == "append":
        insert_at = page.body_end
        block = new_body_lines
        new_lines = lines[:insert_at] + block + lines[insert_at:]
    else:  # replace
        new_lines = lines[:page.body_start] + new_body_lines + lines[page.body_end:]
    Path(path).write_text("\n".join(new_lines) + "\n", encoding="utf-8")
