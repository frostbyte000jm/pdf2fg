"""Table-of-Contents parser (menu option 1).

Reads a page range of the printed TOC and writes sourcebook.md skeleton:

    # Chapter
    ## Subchapter
    ### Page

Level detection (per the agreed policy):
  * The parser tries to find THREE distinguishable layers and maps them to
    #, ##, ###.
  * If it can only tell TWO layers apart, it uses ## and ### (no chapters).
  * If it can only tell ONE layer apart, everything becomes ### (a Page).
  * You then correct the result by hand.

How the layers are told apart:
  * Chapter   = entries set in a distinctive display font (the font whose
    headings are clearly the largest). Auto-detected, or set
    `toc.chapter_fonts` in rules.json.
  * Subchapter = ALL-CAPS entries that are not in the chapter font.
  * Page       = everything else (normal/title-case entries).

Wrapped titles (a title that spills onto a second line) are merged when the
continuation line shares the same font and has no page number yet.

Note: this produces a *best guess*. It does NOT enforce Fantasy Grounds'
nesting rules - use "Fix structure for Fantasy Grounds" (or Export, which does
it automatically) to insert the required parent Subchapters/Pages.
"""

from __future__ import annotations

import re
import statistics

from . import config
from .config import Rules
from .pdfio import PdfReader, Line

_TRAIL_NUM = re.compile(r"[\s\.…·•]*\d{1,4}\s*$")
_NUM_END = re.compile(r"\d{1,4}\s*$")


def _clean(t: str) -> str:
    return _TRAIL_NUM.sub("", t).strip(" .…\t")


def _has_num(t: str) -> bool:
    return bool(_NUM_END.search(t.strip()))


def _is_caps(t: str) -> bool:
    letters = [c for c in t if c.isalpha()]
    return bool(letters) and sum(c.isupper() for c in letters) / len(letters) > 0.8


def _is_entry(text: str) -> bool:
    c = _clean(text)
    if len(c) < 3 or not re.search(r"[A-Za-z]", c):
        return False
    return c.lower() not in ("contents", "table of contents", "index")


def _detect_chapter_fonts(entries: list[Line]) -> set[str]:
    """Chapter font = the caps-entry font (used >=3 times) whose median size is
    clearly the largest. Returns empty set if no font clearly stands out."""
    caps = [e for e in entries if _is_caps(_clean(e.text))]
    byfont: dict[str, list[float]] = {}
    for e in caps:
        byfont.setdefault(e.font, []).append(e.size)
    cand = {f: statistics.median(s) for f, s in byfont.items() if len(s) >= 3}
    if not cand:
        return set()
    top = max(cand.values())
    others = [v for v in cand.values() if v < top - 0.001]
    if not others or top >= max(others) + 0.15:
        return {f for f, v in cand.items() if v >= top - 0.05}
    return set()


def _merge_wrapped(entries: list[Line]) -> list[dict]:
    """Merge continuation lines into one logical entry. A line continues the
    previous one when they share a column/page, are vertically close, the
    previous line has no page number yet, and either they share a font or the
    line starts with '&' (e.g. 'PLANNING' + '& ENGAGEMENT')."""
    merged: list[dict] = []
    buf: dict | None = None
    for e in entries:
        same_place = (buf is not None and e.column == buf["column"]
                      and e.pno == buf["pno"] and (e.top - buf["_top"]) <= 18
                      and not _has_num(buf["parts"][-1]))
        wrap_ok = buf is not None and (
            e.font == buf["font"] or _clean(e.text).startswith("&"))
        cont = bool(same_place and wrap_ok)
        if cont:
            buf["parts"].append(e.text)
            buf["_top"] = e.top
            buf["size"] = max(buf["size"], e.size)
        else:
            if buf:
                merged.append(buf)
            buf = {"parts": [e.text], "font": e.font, "size": e.size,
                   "column": e.column, "pno": e.pno, "_top": e.top}
    if buf:
        merged.append(buf)
    return merged


def parse_toc(reader: PdfReader, rules: Rules, start: int, stop: int) -> list[tuple[int, str]]:
    """Return list of (level, title) where level is 1=#, 2=##, 3=###."""
    entries: list[Line] = []
    for pno in range(start, stop + 1):
        if not (1 <= pno <= reader.page_count):
            continue
        for ln in reader.extract_lines(pno - 1, rules):
            if ln.size >= 13:           # skip oversized page-corner folios etc.
                continue
            if not _is_entry(ln.text):
                continue
            ln.pno = pno                # stash page no on the Line
            entries.append(ln)
    if not entries:
        return []

    chap_fonts = set(rules.data.get("toc", {}).get("chapter_fonts") or [])
    if not chap_fonts:
        chap_fonts = _detect_chapter_fonts(entries)

    merged = _merge_wrapped(entries)

    # classify into style groups: 0 = chapter, 1 = subchapter (caps), 2 = page
    items: list[tuple[int, str]] = []
    for m in merged:
        title = _clean(" ".join(m["parts"]))
        if not title:
            continue
        if m["font"] in chap_fonts:
            style = 0
        elif _is_caps(title):
            style = 1
        else:
            style = 2
        items.append((style, title))

    # Map however many distinct layers we found onto the DEEPEST levels.
    present = sorted({s for s, _ in items})
    n = len(present)
    if n >= 3:
        depth = {0: 1, 1: 2, 2: 3}
    elif n == 2:
        depth = {present[0]: 2, present[1]: 3}
    else:
        depth = {present[0]: 3} if present else {}
    return [(depth[s], t) for s, t in items]


def build_sourcebook_md(levels: list[tuple[int, str]]) -> str:
    header = (
        "<!-- sourcebook.md - generated table of contents.\n"
        "     #  = Chapter   |   ## = Subchapter   |   ### = Page\n"
        "     Edit titles/structure freely, then save & close. Then run\n"
        "     \"Fix structure for FG\" so every Page sits under a Subchapter\n"
        "     and every Subchapter under a Chapter. Add page content with\n"
        "     option 2. See README for formatting tags. -->\n\n"
    )
    body = [f"{'#' * depth} {title}" for depth, title in levels]
    return header + "\n".join(body) + "\n"


def run_create_toc(rules: Rules) -> None:
    print("\n=== Create Table of Contents ===\n")
    start = int(input("Page Start of ToC: ").strip())
    stop = int(input("Page Stop of ToC: ").strip() or start)

    sb = config.sourcebook_path()
    if sb.exists():
        if input(f"{sb.name} already exists - overwrite? (y/N): ").strip().lower() != "y":
            print("Cancelled.")
            return

    reader = PdfReader(rules.pdf_path)
    try:
        levels = parse_toc(reader, rules, start, stop)
    finally:
        reader.close()

    if not levels:
        print("No TOC entries detected. Check the page range / Rules File.")
        return

    with open(sb, "w", encoding="utf-8") as fh:
        fh.write(build_sourcebook_md(levels))
    from collections import Counter
    c = Counter(d for d, _ in levels)
    print(f"\nWrote {sb.name}: {c.get(1,0)} chapters, {c.get(2,0)} subchapters, "
          f"{c.get(3,0)} pages.")
    print("Open it, fix any mis-leveled lines, then run option 5 "
          "(Fix structure for FG).")
