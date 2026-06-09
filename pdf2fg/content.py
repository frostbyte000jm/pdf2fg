"""Content parser (menu option 2).

Pick a ### Page, give a PDF page range, and this extracts text (with bold /
italic / underline), headings, bullet lists, tables and images from those PDF
pages into Markdown, then splices the result under that page in sourcebook.md.

Images are rendered+cropped to PNGs in the campaign images folder, named
    CC_SC_PN_PageTitle_CT.png
(chapter idx, subchapter idx, pdf page number, page title, count).

Per design, the parser only attempts what is reliable. Frames, dual-text and
image-left/right layout are left for you to mark up by hand (see README); a
TODO note is added when content is inserted so you remember to review.
"""

from __future__ import annotations

import re  # noqa
from pathlib import Path

from . import config, mdmodel
from .config import Rules
from .mdfmt import runs_to_md
from .pdfio import PdfReader, Line


# ---------------------------------------------------------------------------
# Block classification
# ---------------------------------------------------------------------------

def _starts_with_bullet(line: Line, glyphs: list[str], bullet_fonts: list[str]) -> bool:
    # A line is a bullet if its left-most word is in a configured bullet/marker
    # font (e.g. a Wingdings dingbat), or its text starts with a bullet glyph.
    if bullet_fonts and line.lead_font and line.lead_font in bullet_fonts:
        return True
    t = line.text
    if not t:
        return False
    return any(g and t.startswith(g) for g in glyphs)


def _strip_bullet(text: str, glyphs: list[str]) -> str:
    for g in glyphs:
        if g and text.startswith(g):
            return text[len(g):].lstrip(" \t.")
    return text


def _median(values: list[float]) -> float | None:
    vals = sorted(values)
    n = len(vals)
    if n == 0:
        return None
    mid = n // 2
    if n % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2.0


def typical_line_pitch(lines: list[Line], body_size: float) -> float | None:
    """Estimate the normal within-paragraph line pitch (top-to-top distance)
    for body text on a page.

    We look at consecutive same-column lines that are both body-sized and close
    together vertically, and take the median of their top-to-top gaps. Lines far
    apart (paragraph breaks, headings) are excluded so they don't inflate the
    estimate. Returns None when there isn't enough body text to judge."""
    deltas: list[float] = []
    for prev, ln in zip(lines, lines[1:]):
        if prev.column != ln.column:
            continue
        # only compare body-ish lines (ignore titles/headings)
        if ln.size > body_size + 1.5 or prev.size > body_size + 1.5:
            continue
        d = ln.top - prev.top
        # plausible single line step: positive and not a huge jump
        if 0 < d <= 2.5 * max(ln.size, 1):
            deltas.append(d)
    return _median(deltas)


def _column_left_edges(lines: list[Line], body_size: float) -> dict[int, float]:
    """The left margin (min x0) of body text per column, used for indent-based
    paragraph detection."""
    edges: dict[int, float] = {}
    for ln in lines:
        if ln.size > body_size + 1.5:
            continue
        if ln.column not in edges or ln.x0 < edges[ln.column]:
            edges[ln.column] = ln.x0
    return edges


def page_to_markdown_blocks(reader: PdfReader, rules: Rules, pno: int,
                            image_md: list[str]) -> list[str]:
    """Convert one PDF page (1-based) to a list of markdown block strings."""
    pi = pno - 1
    h = rules.data.get("headings", {})
    heading_min = float(h.get("page_title_min_size", 16))
    sub_heading_min = float(h.get("heading_min_size", 12))
    body_size = float(h.get("body_size", 9))
    glyphs = rules.data.get("lists", {}).get("bullet_glyphs", [])
    bullet_fonts = rules.data.get("lists", {}).get("bullet_fonts", [])
    para_cfg = rules.data.get("paragraphs", {})
    detect_breaks = bool(para_cfg.get("detect_breaks", True))
    gap_ratio = float(para_cfg.get("gap_ratio", 1.3))
    indent_pts = float(para_cfg.get("indent_pts", 0.0))

    # tables first (with bboxes so we can exclude their text from paragraphs)
    table_bboxes = []
    table_blocks = []
    page = reader.plumber.pages[pi]
    try:
        found = page.find_tables()
    except Exception:
        found = []
    for t in found:
        try:
            data = t.extract()
        except Exception:
            continue
        cleaned = [[(c or "").strip().replace("\n", " ") for c in row] for row in data]
        cleaned = [row for row in cleaned if any(cell for cell in row)]
        if len(cleaned) >= 1 and len(cleaned[0]) >= 2:
            table_bboxes.append(t.bbox)  # (x0, top, x1, bottom)
            table_blocks.append(_table_to_md(cleaned))

    def in_a_table(line: Line) -> bool:
        cy = (line.top + line.bottom) / 2
        cx = (line.x0 + line.x1) / 2
        for (x0, top, x1, bottom) in table_bboxes:
            if x0 - 2 <= cx <= x1 + 2 and top - 2 <= cy <= bottom + 2:
                return True
        return False

    lines = [ln for ln in reader.extract_lines(pi, rules) if not in_a_table(ln)]

    # Normal within-paragraph line pitch + per-column left margins, so we can
    # tell a wrapped "next line" from a real paragraph "line break" below.
    pitch = typical_line_pitch(lines, body_size) if detect_breaks else None
    col_left = _column_left_edges(lines, body_size) if detect_breaks else {}

    def is_para_break(prev: Line | None, ln: Line) -> bool:
        """True if `ln` should start a new paragraph rather than continue the
        previous one (same column assumed; column changes are handled
        separately)."""
        if prev is None or not detect_breaks:
            # fall back to the original size-relative gap test
            return prev is not None and (ln.top - prev.bottom) > 0.7 * max(ln.size, 1)
        # extra vertical space between lines (the common case)
        if pitch and (ln.top - prev.top) > gap_ratio * pitch:
            return True
        # first-line indent (books that indent new paragraphs instead of spacing)
        if indent_pts > 0:
            left = col_left.get(ln.column)
            if left is not None and (ln.x0 - left) >= indent_pts:
                return True
        # safety net for pages with too little text to estimate a pitch
        if not pitch and (ln.top - prev.bottom) > 0.7 * max(ln.size, 1):
            return True
        return False

    blocks: list[str] = []
    para: list[Line] = []
    bullets: list[str] = []
    prev: Line | None = None

    def flush_para():
        nonlocal para
        if para:
            blocks.append(_join_para(para))
            para = []

    def flush_bullets():
        nonlocal bullets
        if bullets:
            blocks.append("\n".join(bullets))
            bullets = []

    for ln in lines:
        text = ln.text
        if not text:
            prev = ln
            continue
        alpha = sum(c.isalpha() for c in text)
        is_heading = (ln.size >= sub_heading_min and len(text) < 120
                      and alpha >= 2)
        is_bullet = _starts_with_bullet(ln, glyphs, bullet_fonts)
        col_change = prev is not None and ln.column != prev.column
        para_break = col_change or is_para_break(prev, ln)

        if is_bullet:
            flush_para()
            item = _strip_bullet(text, glyphs)
            bullets.append(f"- {runs_to_md(_strip_bullet_runs(ln, glyphs, bullet_fonts))}")
        elif is_heading:
            flush_para(); flush_bullets()
            blocks.append(f"#### {runs_to_md(ln.runs)}")
        else:
            flush_bullets()
            if para and para_break:
                flush_para()
            para.append(ln)
        prev = ln

    flush_para(); flush_bullets()

    # Append tables and images at the end of the page's blocks (position is a
    # best guess; reorder during manual cleanup).
    blocks.extend(table_blocks)
    blocks.extend(image_md)
    return blocks


def _strip_bullet_runs(line: Line, glyphs: list[str], bullet_fonts: list[str]):
    """Return the line's runs with the leading bullet marker removed - either a
    dedicated marker-font run (e.g. a Wingdings dingbat) or a leading glyph."""
    from .pdfio import Run
    runs = [r for r in line.runs]
    # marker-font bullet: the glyph is its own leading run; drop it
    if bullet_fonts and line.lead_font in bullet_fonts and runs:
        runs = runs[1:]
    if runs:
        first = runs[0]
        txt = first.text.lstrip(" \t")
        for g in glyphs:
            if g and txt.startswith(g):
                txt = txt[len(g):].lstrip(" \t.")
                break
        runs[0] = Run(text=txt, bold=first.bold, italic=first.italic,
                      underline=first.underline, size=first.size)
    return runs


def _merge_runs(runs):
    from .pdfio import Run
    out = []
    for r in runs:
        if (out and out[-1].bold == r.bold and out[-1].italic == r.italic
                and out[-1].underline == r.underline):
            out[-1].text += r.text
        else:
            out.append(Run(text=r.text, bold=r.bold, italic=r.italic,
                           underline=r.underline))
    return out


def _join_para(para: list[Line]) -> str:
    """Merge all lines of a paragraph at the RUN level (so runs of the same
    style flow across line breaks into one **bold**/*italic* span) and
    de-hyphenate words broken across lines."""
    from .pdfio import Run
    flat = []
    for i, ln in enumerate(para):
        if i > 0:
            if flat and flat[-1].text.rstrip().endswith("-"):
                flat[-1].text = flat[-1].text.rstrip()[:-1]  # de-hyphenate
            else:
                flat.append(Run(text=" "))
        flat.extend(ln.runs)
    return runs_to_md(_merge_runs(flat)).strip()


def _table_to_md(rows: list[list[str]]) -> str:
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]
    out = []
    out.append("| " + " | ".join(rows[0]) + " |")
    out.append("|" + "|".join(["--"] * ncol) + "|")
    for r in rows[1:]:
        out.append("| " + " | ".join(r) + " |")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

def _sanitize(title: str) -> str:
    t = re.sub(r"[^A-Za-z0-9]+", "", title.title())
    return t or "Page"


def extract_images_for_page(reader: PdfReader, rules: Rules, pno: int,
                            page: mdmodel.Page) -> list[str]:
    """Crop images from PDF page `pno`, save to campaign images dir, return
    markdown image lines."""
    regions = reader.find_images(pno - 1)
    if not regions:
        return []
    images_dir = rules.images_dir
    cc = f"{page.chapter_index:02d}"
    sc = f"{page.sub_index:02d}"
    printed = pno + rules.page_offset
    pn = f"{printed:02d}"
    title = _sanitize(page.title)
    md = []
    for ct, region in enumerate(regions):
        name = f"{cc}_{sc}_{pn}_{title}_{ct:02d}.png"
        out_path = images_dir / name
        try:
            reader.crop_image(region, out_path)
            md.append(f"![]({rules.data.get('images_subfolder','images')}/{name})")
        except Exception as e:
            md.append(f"<!-- image extract failed for {name}: {e} -->")
    return md


# ---------------------------------------------------------------------------
# CLI flow
# ---------------------------------------------------------------------------

def run_add_content(rules: Rules) -> None:
    sb = config.sourcebook_path()
    if not sb.exists():
        print("No sourcebook.md yet - create the Table of Contents first (option 1).")
        return
    doc = mdmodel.parse_file(sb)
    if not doc.pages:
        print("sourcebook.md has no ### pages. Add some, then retry.")
        return

    print("\nChoose the Page you wish to add content to:")
    for i, pg in enumerate(doc.pages, 1):
        print(f"  {i}) {pg.title}")
    sel = int(input("Page number: ").strip()) - 1
    if not (0 <= sel < len(doc.pages)):
        print("Invalid selection.")
        return
    page = doc.pages[sel]

    start = int(input(f"Page Start of '{page.title}' (in PDF): ").strip())
    stop = int(input(f"Page Stop of '{page.title}' (in PDF): ").strip() or start)

    mode = "replace"
    if any(l.strip() for l in page.body):
        ans = input("This page already has content. (R)eplace / (A)ppend / (C)ancel? "
                    "[R]: ").strip().lower()
        if ans == "c":
            print("Cancelled.")
            return
        mode = "append" if ans == "a" else "replace"

    reader = PdfReader(rules.pdf_path)
    all_blocks: list[str] = []
    try:
        for pno in range(start, stop + 1):
            if not (1 <= pno <= reader.page_count):
                continue
            image_md = extract_images_for_page(reader, rules, pno, page)
            all_blocks.extend(page_to_markdown_blocks(reader, rules, pno, image_md))
    finally:
        reader.close()

    body_lines: list[str] = [""]
    body_lines.append("<!-- TODO review: confirm headings/lists/tables; add any "
                      "frames, dual-text or image-left/right layout by hand. -->")
    for b in all_blocks:
        body_lines.append("")
        body_lines.extend(b.split("\n"))
    body_lines.append("")

    mdmodel.splice_page_body(sb, page, body_lines, mode=mode)
    print(f"\nAdded content from PDF pages {start}-{stop} to '{page.title}'.")
    print("Open sourcebook.md to review & clean up.")
