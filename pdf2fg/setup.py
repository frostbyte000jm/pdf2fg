"""Setup flow (menu when no complete Rules File exists).

Asks for the FG campaign folder + a small page range, scans that range, and
writes two things:

  rules.json    - a best-guess Rules File (already usable; edit as needed)
  raw_scan.md   - a self-contained prompt + font/size statistics. Paste the
                  whole file into any AI chat and it will return a corrected
                  rules.json for tricky PDFs.
"""

from __future__ import annotations

from pathlib import Path

from . import config
from .config import Rules
from .pdfio import PdfReader


AI_INSTRUCTIONS = """\
<!-- ===================================================================
PASTE THIS ENTIRE FILE INTO AN AI CHAT (Claude, ChatGPT, etc.)

Ask it: "Using the font/size statistics below, produce a corrected
`rules.json` for my pdf2fg tool. Map each font to bold / italic /
bold_italic / regular, pick sensible `page_title_min_size`,
`heading_min_size` and `body_size`, identify bullet glyph/fonts, and set
the `paragraphs` block (gap_ratio / indent_pts) from the line-spacing
section. Return ONLY the JSON." Then paste the JSON it gives you into
rules.json.

The tool already wrote a best-guess rules.json - you only need this step
if the automatic guesses look wrong.

WHAT EACH FIELD MEANS
- fonts.bold/italic/bold_italic/regular : list the font NAMES (without the
  subset prefix, e.g. "Exo2-Bold") that correspond to each style.
- headings.page_title_min_size : font size at/above which a line is a PAGE
  title. heading_min_size : size for an in-page #### Heading. body_size :
  the normal paragraph size.
- columns.mode : "auto" | "single". count/gutter_x only used if you must
  force a split.
- lists.bullet_glyphs : characters that start a bullet. bullet_fonts : any
  dingbat/symbol font used for bullets - see the "Likely list / bullet MARKER
  fonts" section below; lines whose first glyph is in one of these fonts are
  treated as list items.
- ignore_rotated_text : leave true to drop vertical margin tabs (see the
  "Rotated / vertical text" section).
- paragraphs.gap_ratio : a new paragraph is started when the vertical distance
  between two body lines is more than this multiple of the normal line pitch.
  paragraphs.indent_pts : set > 0 only if the book indents new paragraphs
  instead of spacing them. See the "Line spacing" section below for measured
  values and a suggested gap_ratio.
==================================================================== -->
"""


def _prompt(msg: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{msg}{suffix}: ").strip()
    return val or default


def run_setup(existing: Rules | None) -> None:
    print("\n=== Setup Rules File ===\n")

    pdfs = config.find_pdfs()
    if not pdfs:
        print("No PDF found in this folder. Copy your sourcebook PDF here and re-run.")
        return
    if len(pdfs) == 1:
        pdf = pdfs[0]
        print(f"Using PDF: {pdf.name}")
    else:
        print("Multiple PDFs found:")
        for i, p in enumerate(pdfs, 1):
            print(f"  {i}) {p.name}")
        idx = int(_prompt("Which one? (number)", "1")) - 1
        pdf = pdfs[max(0, min(idx, len(pdfs) - 1))]

    rules = existing or Rules()
    default_cf = rules.data.get("campaign_folder", "")
    cf = _prompt("Location of FG Campaign Folder", default_cf)
    rules.data["campaign_folder"] = cf
    rules.data["pdf_file"] = pdf.name

    start = int(_prompt("Page Start in PDF"))
    stop = int(_prompt("Page Stop in PDF", str(start)))

    reader = PdfReader(pdf)
    try:
        idxs = [p - 1 for p in range(start, stop + 1) if 1 <= p <= reader.page_count]
        stats = reader.font_size_stats(idxs)
        _autofill_fonts(rules, stats)
        spacing = _measure_line_spacing(reader, rules, idxs)
        _autofill_paragraphs(rules, spacing)
        _write_raw_scan(stats, spacing, pdf.name, start, stop, rules)
    finally:
        reader.close()

    config.save_rules(rules)
    print(f"\nWrote best-guess {config.RULES_FILENAME}")
    print(f"Wrote {config.RAW_SCAN_FILENAME} (paste into an AI chat to refine).")
    print("Open rules.json, confirm the font lists & sizes, then re-run the CLI.")


def _autofill_fonts(rules: Rules, stats: dict) -> None:
    fonts = {"bold": [], "italic": [], "bold_italic": [], "regular": []}
    for fn in stats["fonts"]:
        low = fn.lower()
        b = any(k in low for k in ("bold", "black", "heavy", "semibold"))
        i = any(k in low for k in ("italic", "oblique"))
        if b and i:
            fonts["bold_italic"].append(fn)
        elif b:
            fonts["bold"].append(fn)
        elif i:
            fonts["italic"].append(fn)
        else:
            fonts["regular"].append(fn)
    rules.data["fonts"] = fonts

    # size guesses: body = most common size; page title = largest with decent
    # count; heading = between.
    sizes = stats["sizes"]
    if sizes:
        body = max(sizes.items(), key=lambda kv: kv[1])[0]
        big = sorted([s for s, n in sizes.items() if n >= 3 and s > body], reverse=True)
        page_title = big[0] if big else body + 6
        heading = big[1] if len(big) > 1 else max(body + 1.5, (page_title + body) / 2)
        rules.data["headings"] = {
            "page_title_min_size": float(page_title),
            "heading_min_size": float(round(heading, 1)),
            "body_size": float(body),
        }

    # bullet/marker fonts: detected dingbat fonts + fonts used as single-glyph
    # line-leading markers (see PdfReader.font_size_stats).
    bf = list(stats.get("marker_fonts", []))
    if bf:
        rules.data.setdefault("lists", {})["bullet_fonts"] = bf


def _measure_line_spacing(reader: PdfReader, rules: Rules, idxs: list[int]) -> dict:
    """Measure the gap between consecutive body-text lines so we can tell a
    wrapped 'next line' from a real paragraph 'line break'.

    Collects same-column top-to-top distances for body-sized lines across the
    scanned pages, finds the normal line pitch (the most common small gap) and,
    if present, the larger gap that marks a new paragraph. Also notes whether
    new paragraphs look indented rather than spaced."""
    from collections import Counter
    from . import content

    body_size = float(rules.data.get("headings", {}).get("body_size", 10.0))
    deltas: list[float] = []
    indents: list[float] = []
    for pi in idxs:
        try:
            lines = reader.extract_lines(pi, rules)
        except Exception:
            continue
        col_left = content._column_left_edges(lines, body_size)
        for prev, ln in zip(lines, lines[1:]):
            if prev.column != ln.column:
                continue
            if ln.size > body_size + 1.5 or prev.size > body_size + 1.5:
                continue
            d = ln.top - prev.top
            if 0 < d <= 3.0 * max(ln.size, 1):
                deltas.append(round(d, 1))
            left = col_left.get(ln.column)
            if left is not None:
                indents.append(round(ln.x0 - left, 1))

    if not deltas:
        return {"pitch": None, "para_gap": None, "suggested_ratio": 1.3,
                "hist": [], "indent_suggested": False, "indent_pts": 0.0}

    hist = Counter(deltas)
    # normal pitch = the most common gap (wrapped lines dominate body text)
    pitch = hist.most_common(1)[0][0]
    # paragraph gaps: clearly larger than the pitch but not a full blank region
    para_candidates = [d for d in deltas if 1.12 * pitch < d <= 2.2 * pitch]
    para_gap = None
    suggested = 1.3
    if para_candidates:
        para_gap = round(sum(para_candidates) / len(para_candidates), 1)
        # threshold midway between the two clusters, expressed as a ratio
        suggested = round(((pitch + para_gap) / 2.0) / pitch, 2)
        suggested = max(1.2, min(1.8, suggested))

    # indent-based paragraphs: a meaningful share of body lines start clearly
    # to the right of their column margin
    indent_hits = sum(1 for x in indents if x >= 4.0)
    indent_suggested = bool(indents) and indent_hits >= max(3, int(0.08 * len(indents)))
    indent_pts = 0.0
    if indent_suggested:
        big = sorted(x for x in indents if x >= 4.0)
        indent_pts = round(big[len(big) // 2], 1)  # median indent

    return {"pitch": pitch, "para_gap": para_gap, "suggested_ratio": suggested,
            "hist": hist.most_common(12), "indent_suggested": indent_suggested,
            "indent_pts": indent_pts}


def _autofill_paragraphs(rules: Rules, spacing: dict) -> None:
    p = rules.data.setdefault("paragraphs", {})
    p.setdefault("detect_breaks", True)
    p["gap_ratio"] = float(spacing.get("suggested_ratio", 1.3))
    p["indent_pts"] = float(spacing.get("indent_pts", 0.0)) if spacing.get("indent_suggested") else 0.0


def _write_raw_scan(stats: dict, spacing: dict, pdf_name: str, start: int,
                    stop: int, rules: Rules) -> None:
    lines = [AI_INSTRUCTIONS, ""]
    lines.append(f"# Raw scan of `{pdf_name}` pages {start}-{stop}\n")

    lines.append("## Fonts (name -> char count)\n")
    for fn, n in sorted(stats["fonts"].items(), key=lambda kv: -kv[1]):
        lines.append(f"- `{fn}` : {n}")
    lines.append("")

    lines.append("## Sizes (size -> char count)\n")
    for sz, n in sorted(stats["sizes"].items(), key=lambda kv: -kv[1]):
        lines.append(f"- {sz} : {n}")
    lines.append("")

    lines.append("## Font @ size combinations with sample text\n")
    combos = sorted(stats["combos"].items(), key=lambda kv: -kv[1])
    for (fn, sz), n in combos:
        sample = stats["samples"].get(f"{fn}@{sz}", "")
        lines.append(f"- `{fn}` @ {sz}  (x{n})  e.g. {sample!r}")
    lines.append("")

    lines.append("## Likely list / bullet MARKER fonts\n")
    lines.append("These fonts look like bullet markers (dingbats, or single "
                 "glyphs that start lines). Put the right ones in "
                 "`lists.bullet_fonts` so the parser treats those lines as list "
                 "items (e.g. the SaV ship list uses a Wingdings arrow).\n")
    mf = stats.get("marker_fonts", [])
    if mf:
        for fn in mf:
            lines.append(f"- `{fn}`")
    else:
        lines.append("- (none detected - bullets may use a text glyph like "
                     "• or -, see lists.bullet_glyphs)")
    lines.append("")

    lines.append("## Line spacing: next line vs. paragraph break\n")
    lines.append("Within a paragraph, wrapped lines sit one \"line pitch\" apart. A new "
                 "paragraph either adds extra vertical space or indents its first "
                 "line. pdf2fg starts a new paragraph when the top-to-top distance "
                 "between two body lines exceeds `paragraphs.gap_ratio` x the normal "
                 "pitch (or, for indented books, when the first line is indented at "
                 "least `paragraphs.indent_pts`).\n")
    pitch = spacing.get("pitch")
    if pitch:
        lines.append(f"- Normal line pitch (wrapped \"next line\"): **{pitch} pt**")
        pg = spacing.get("para_gap")
        if pg:
            lines.append(f"- Typical paragraph \"line break\" gap: **{pg} pt**")
        else:
            lines.append("- No clearly larger paragraph gap detected — this book may "
                         "indent new paragraphs instead of spacing them (see below).")
        lines.append(f"- Suggested `paragraphs.gap_ratio`: **{spacing.get('suggested_ratio')}** "
                     "(gap above this multiple of the pitch = new paragraph)")
        if spacing.get("indent_suggested"):
            lines.append(f"- First-line indents detected — suggested "
                         f"`paragraphs.indent_pts`: **{spacing.get('indent_pts')}**")
        else:
            lines.append("- No consistent first-line indent — leave "
                         "`paragraphs.indent_pts` at 0.")
        hist = spacing.get("hist") or []
        if hist:
            pretty = ", ".join(f"{g}pt x{n}" for g, n in hist)
            lines.append(f"- Most common line gaps (pt x count): {pretty}")
    else:
        lines.append("- Not enough body text in this range to measure line spacing. "
                     "Defaults are used; widen the page range and re-run Setup if "
                     "paragraphs come in merged.")
    lines.append("")

    lines.append("## Rotated / vertical text\n")
    rot = stats.get("rotated", 0)
    if rot:
        lines.append(f"- Found {rot} rotated glyphs (e.g. margin tabs like "
                     "\"OVERVIEW // 1\"). These are ignored automatically while "
                     "`ignore_rotated_text` is true in rules.json.")
    else:
        lines.append("- None found.")
    lines.append("")

    lines.append("## Current best-guess rules.json\n")
    lines.append("```json")
    import json
    lines.append(json.dumps(rules.data, indent=2, ensure_ascii=False))
    lines.append("```")

    with open(config.raw_scan_path(), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
