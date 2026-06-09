"""Fantasy Grounds -> Markdown converter (menu option 3).

Reads the <reference> section of the campaign db.xml and rebuilds sourcebook.md,
walking the refmanualindex (for chapter/subchapter/page order) and following
each refpage's recordname into refmanualdata to recover its blocks. Lets you
pull manual edits made inside FG back into Markdown so they are not lost.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from . import config, mdfmt
from .config import Rules
from .mdfmt import Segment


# ---------------------------------------------------------------------------
# Inline (formattedtext) -> markdown
# ---------------------------------------------------------------------------

def _inline_segments(elem, bold=False, italic=False, underline=False,
                     frame=False) -> list[Segment]:
    segs: list[Segment] = []
    if elem.text:
        segs.append(Segment(text=elem.text, bold=bold, italic=italic,
                            underline=underline, frame=frame))
    for child in elem:
        tag = child.tag
        b, i, u, fr = bold, italic, underline, frame
        if tag == "b":
            b = True
        elif tag == "i":
            i = True
        elif tag == "u":
            u = True
        elif tag == "frame":
            fr = True
        elif tag == "link":
            url = child.get("recordname", "") or ""
            segs.append(Segment(text=(child.text or ""), link=url))
            if child.tail:
                segs.append(Segment(text=child.tail, bold=bold, italic=italic,
                                    underline=underline, frame=frame))
            continue
        segs.extend(_inline_segments(child, b, i, u, fr))
        if child.tail:
            segs.append(Segment(text=child.tail, bold=bold, italic=italic,
                                underline=underline, frame=frame))
    return segs


def _inline_md(elem) -> str:
    return mdfmt.segments_to_md(_inline_segments(elem)).strip()


# ---------------------------------------------------------------------------
# Block element -> markdown lines
# ---------------------------------------------------------------------------

def _img_filename(block) -> tuple[str, str]:
    name = ""
    layer = block.find("./image/layers/layer")
    if layer is not None:
        bm = layer.find("bitmap")
        nm = layer.find("name")
        if bm is not None and bm.text:
            name = Path(bm.text).name
        elif nm is not None and nm.text:
            name = nm.text
    return name, name


def block_to_md(block) -> list[str]:
    bt = block.find("blocktype")
    btype = bt.text if bt is not None else "singletext"
    frame_el = block.find("frame")  # decorative frame (has type attr)
    frame_name = None
    if frame_el is not None and frame_el.get("type") == "string" and frame_el.text:
        frame_name = frame_el.text.strip()

    out: list[str] = []
    if frame_name:
        out.append(f"[{frame_name}]")

    if btype == "header":
        t = block.find("text")
        out.append(f"##### {(t.text or '').strip() if t is not None else ''}")
        return out

    if btype == "dualtext":
        out.append("[[dual]]")
        out.extend(_formattedtext_to_md(block.find("text")))
        out.append("[[right]]")
        out.extend(_formattedtext_to_md(block.find("text2")))
        out.append("[[/dual]]")
        return out

    if btype in ("image", "imageright", "imageleft"):
        name, _ = _img_filename(block)
        cap_el = block.find("caption")
        caption = (cap_el.text or "") if cap_el is not None else ""
        scale_el = block.find("scale")
        scale = scale_el.text if scale_el is not None else "100"
        img_md = f"![{caption}](images/{name})"
        if btype == "image":
            out.append(img_md)
        else:
            out.append(f"[[{btype} scale={scale}]]")
            out.append(img_md)
            out.extend(_formattedtext_to_md(block.find("text")))
            out.append("[[/image]]")
        return out

    # singletext: dispatch on formattedtext content
    out.extend(_formattedtext_to_md(block.find("text")))
    return out


def _formattedtext_to_md(text_el) -> list[str]:
    if text_el is None:
        return []
    out: list[str] = []
    for child in text_el:
        tag = child.tag
        if tag == "h":
            out.append(f"#### {_inline_md(child)}")
        elif tag == "p":
            out.append(_inline_md(child))
        elif tag == "list":
            for li in child.findall("li"):
                out.append(f"- {_inline_md(li)}")
        elif tag == "linklist":
            for link in child.findall("link"):
                url = link.get("recordname", "") or ""
                out.append(f"[{link.text or ''}]({url})")
        elif tag == "table":
            rows = []
            for tr in child.findall("tr"):
                rows.append([_inline_md(td) for td in tr.findall("td")])
            out.extend(_table_md(rows))
        elif tag == "frame":
            out.append(f"<speak>{_inline_md(child)}</speak>")
    # handle formattedtext that is just bare text
    if not list(text_el) and text_el.text and text_el.text.strip():
        out.append(text_el.text.strip())
    return out


def _table_md(rows: list[list[str]]) -> list[str]:
    if not rows:
        return []
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |", "|" + "|".join(["--"] * ncol) + "|"]
    for r in rows[1:]:
        out.append("| " + " | ".join(r) + " |")
    return out


# ---------------------------------------------------------------------------
# Walk reference -> sourcebook.md
# ---------------------------------------------------------------------------

def _ordered(parent, prefix: str):
    """Return child elements whose tag starts with `prefix`, sorted by <order>."""
    kids = [c for c in parent if c.tag.startswith(prefix)]
    def okey(c):
        o = c.find("order")
        try:
            return int(o.text)
        except Exception:
            return 9999
    return sorted(kids, key=okey)


def build_markdown(root) -> str:
    ref = root.find("reference")
    if ref is None:
        return ""
    data = ref.find("refmanualdata")
    index = ref.find("refmanualindex")
    # map recordname id -> data element
    data_by_id = {}
    if data is not None:
        for pageel in data:
            data_by_id[pageel.tag] = pageel

    lines: list[str] = [
        "<!-- sourcebook.md - imported from Fantasy Grounds db.xml.",
        "     #  = Chapter | ## = Subchapter | ### = Page -->",
        "",
    ]

    def blocks_md(page_data_el):
        if page_data_el is None:
            return []
        blocks_el = page_data_el.find("blocks")
        if blocks_el is None:
            return []
        out = []
        for b in _ordered_blocks(blocks_el):
            md = block_to_md(b)
            if md:
                out.append("")
                out.extend(md)
        return out

    if index is not None:
        chapters_el = index.find("chapters")
        for ch in _ordered(chapters_el, "id-"):
            lines.append(f"# {_name(ch)}")
            subs = ch.find("subchapters")
            for sub in _ordered(subs, "id-") if subs is not None else []:
                lines.append(f"## {_name(sub)}")
                pages = sub.find("refpages")
                for pg in _ordered(pages, "id-") if pages is not None else []:
                    lines.append(f"### {_name(pg)}")
                    rec = pg.find("./listlink/recordname")
                    if rec is not None and rec.text:
                        data_id = rec.text.split(".")[-1]
                        lines.extend(blocks_md(data_by_id.get(data_id)))
                    lines.append("")
    return "\n".join(lines) + "\n"


def _ordered_blocks(blocks_el):
    kids = list(blocks_el)
    def okey(c):
        o = c.find("order")
        try:
            return int(o.text)
        except Exception:
            return 9999
    return sorted(kids, key=okey)


def _name(el) -> str:
    n = el.find("name")
    return (n.text or "").strip() if n is not None else ""


def run_import(rules: Rules) -> None:
    print("\n=== Import Sourcebook from Fantasy Grounds ===")
    db = rules.db_path
    if not db.exists():
        print(f"db.xml not found at {db}. Check campaign_folder in rules.json.")
        return
    sb = config.sourcebook_path()
    if sb.exists():
        if input(f"{sb.name} exists - overwrite? (y/N): ").strip().lower() != "y":
            print("Cancelled.")
            return
    tree = ET.parse(db)
    md = build_markdown(tree.getroot())
    if not md.strip():
        print("No <reference> reference-manual content found in db.xml.")
        return
    sb.write_text(md, encoding="utf-8")
    print(f"Wrote {sb.name} from {db.name}.")
