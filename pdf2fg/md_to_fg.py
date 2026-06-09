"""Markdown -> Fantasy Grounds converter (menu option 4).

Parses sourcebook.md and rebuilds the <reference> section of the campaign's
db.xml: a refmanualdata entry (with blocks) per ### Page, plus a refmanualindex
mirroring the chapter/subchapter/page tree.

The rest of db.xml is preserved byte-for-byte except the <reference> element,
which is fully replaced. A timestamped backup is written first, and the result
is validated as XML before saving. FG must be CLOSED or it will overwrite the
file on exit.

Block markup recognised in a page body (see README for the full table):
    #### Heading            -> <h> inside a singletext block
    #####  Center Header     -> 'header' block (centred, plain text)
    paragraph text          -> <p> (inline **bold** *italic* <u>underline</u>)
    - item                  -> <list><li>
    | a | b |               -> <table>
    [label](url) (only)     -> <linklist>
    <speak>text</speak>     -> <frame> chat bubble (inline)
    ![caption](images/x.png)-> centred image block
    [frame:sidebar]         -> decorative frame applied to the NEXT block
    [[dual]]..[[right]]..[[/dual]]         -> dualtext (two columns; legacy [[vs]] still read)
    [[imageright scale=70]] ![..]() text [[/image]]  -> image right, text left
    [[imageleft  scale=70]] ![..]() text [[/image]]  -> image left,  text right
"""

from __future__ import annotations

import datetime as _dt
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape as X

from . import config, mdmodel
from .config import Rules
from . import mdfmt

IND = "\t"


def _id(n: int) -> str:
    return f"id-{n:05d}"


# ---------------------------------------------------------------------------
# Body markdown -> block dicts
# ---------------------------------------------------------------------------

_IMG_RE = re.compile(r"^!\[(.*?)\]\((.*?)\)\s*$")
_FRAME_DIR = re.compile(r"^\[([A-Za-z0-9_\-]+)\]\s*$")
_DUAL_OPEN = re.compile(r"^\[\[dual\]\]\s*$", re.I)
_DUAL_VS = re.compile(r"^\[\[(?:right|vs)\]\]\s*$", re.I)  # [[right]] (legacy: [[vs]])
_DUAL_CLOSE = re.compile(r"^\[\[/dual\]\]\s*$", re.I)
_IMGBLK_OPEN = re.compile(r"^\[\[(imageright|imageleft)(?:\s+scale=(\d+))?\]\]\s*$", re.I)
_IMGBLK_CLOSE = re.compile(r"^\[\[/image\]\]\s*$", re.I)


def parse_blocks(body: list[str]) -> list[dict]:
    blocks: list[dict] = []
    i = 0
    n = len(body)
    pending_frame: str | None = None

    def push(b: dict):
        nonlocal pending_frame
        if pending_frame:
            b["frame"] = pending_frame
            pending_frame = None
        blocks.append(b)

    while i < n:
        raw = body[i]
        line = raw.strip()

        if not line or line.startswith("<!--"):
            i += 1
            continue

        # frame directive
        m = _FRAME_DIR.match(line)
        if m:
            pending_frame = m.group(1)
            i += 1
            continue

        # dual text
        if _DUAL_OPEN.match(line):
            left, right, i = _collect_dual(body, i + 1, n)
            push({"type": "dualtext", "left": left, "right": right})
            continue

        # image-left / image-right block
        m = _IMGBLK_OPEN.match(line)
        if m:
            kind = m.group(1).lower()
            scale = int(m.group(2)) if m.group(2) else (70 if kind == "imageleft" else 100)
            img, caption, text, i = _collect_imageblock(body, i + 1, n)
            push({"type": kind, "image": img, "caption": caption,
                  "text": text, "scale": scale})
            continue

        # standalone image
        m = _IMG_RE.match(line)
        if m:
            push({"type": "image", "image": m.group(2), "caption": m.group(1),
                  "scale": 100})
            i += 1
            continue

        # center header
        if line.startswith("##### "):
            txt = "".join(s.text for s in mdfmt.parse_inline(line[6:].strip()))
            push({"type": "header", "text": txt})
            i += 1
            continue

        # table
        if line.startswith("|"):
            rows, i = _collect_table(body, i, n)
            push({"type": "table", "rows": rows})
            continue

        # bullet list
        if line.startswith("- ") or line == "-":
            items, i = _collect_list(body, i, n)
            push({"type": "list", "items": items})
            continue

        # link-only line(s) -> linklist
        if mdfmt.has_only_links(line):
            links, i = _collect_links(body, i, n)
            push({"type": "linklist", "links": links})
            continue

        # heading + following paragraphs -> a singletext block
        para_lines, i = _collect_text(body, i, n)
        push({"type": "text", "lines": para_lines})

    return blocks


def _collect_text(body, i, n):
    """Collect a run of heading/paragraph lines until a blank line or a line
    that starts a different block type."""
    out = []
    while i < n:
        line = body[i].strip()
        if not line or line.startswith("<!--"):
            break
        if (line.startswith("|") or line.startswith("- ") or line == "-"
                or line.startswith("#####") or _IMG_RE.match(line)
                or _FRAME_DIR.match(line) or _DUAL_OPEN.match(line)
                or _IMGBLK_OPEN.match(line) or mdfmt.has_only_links(line)):
            break
        out.append(line)
        i += 1
    return out, i


def _collect_list(body, i, n):
    items = []
    while i < n:
        line = body[i].strip()
        if line.startswith("- "):
            items.append(line[2:].strip())
        elif line == "-":
            items.append("")
        elif not line:
            break
        else:
            # continuation of previous item
            if items:
                items[-1] = (items[-1] + " " + line).strip()
            else:
                break
        i += 1
    return items, i


def _collect_table(body, i, n):
    rows = []
    while i < n and body[i].strip().startswith("|"):
        cells = [c.strip() for c in body[i].strip().strip("|").split("|")]
        if re.match(r"^[\s:\-]+$", "".join(cells)):
            i += 1
            continue  # separator row
        rows.append(cells)
        i += 1
    return rows, i


def _collect_links(body, i, n):
    links = []
    while i < n and body[i].strip() and mdfmt.has_only_links(body[i].strip()):
        links.extend(mdfmt.extract_links(body[i].strip()))
        i += 1
    return links, i


def _collect_dual(body, i, n):
    left, right, side = [], [], "left"
    while i < n:
        line = body[i].strip()
        if _DUAL_CLOSE.match(line):
            i += 1
            break
        if _DUAL_VS.match(line):
            side = "right"
            i += 1
            continue
        (left if side == "left" else right).append(line)
        i += 1
    return [l for l in left if l], [r for r in right if r], i


def _collect_imageblock(body, i, n):
    img, caption, text = "", "", []
    while i < n:
        line = body[i].strip()
        if _IMGBLK_CLOSE.match(line):
            i += 1
            break
        m = _IMG_RE.match(line)
        if m:
            caption, img = m.group(1), m.group(2)
        elif line:
            text.append(line)
        i += 1
    return img, caption, text, i


# ---------------------------------------------------------------------------
# Block dicts -> FG XML
# ---------------------------------------------------------------------------

def _formattedtext(lines: list[str], indent: str) -> str:
    """Turn paragraph/heading lines into <h>/<p> children (no wrapper)."""
    out = []
    for ln in lines:
        if ln.startswith("####"):
            out.append(f"{indent}<h>{_inline(ln.lstrip('#').strip())}</h>")
        else:
            out.append(f"{indent}<p>{_inline(ln)}</p>")
    return "\n".join(out)


def _rich_ft(lines: list[str], indent: str) -> str:
    """Build full formatted-text children from markdown lines. A dual-text
    column (or image-block text) is a normal text body, so it supports
    everything: headings, paragraphs with inline **/*/<u>/links/<speak>,
    bullet lists, pipe tables and link lists."""
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        ln = lines[i].strip()
        if not ln:
            i += 1
            continue
        if ln.startswith("####"):
            out.append(f"{indent}<h>{_inline(ln.lstrip('#').strip())}</h>")
            i += 1
        elif mdfmt.has_only_links(ln):
            out.append(f"{indent}<linklist>")
            while i < n and lines[i].strip() and mdfmt.has_only_links(lines[i].strip()):
                for label, url in mdfmt.extract_links(lines[i].strip()):
                    out.append(f'{indent}\t<link class="" recordname="{X(url)}">'
                               f'{X(label)}</link>')
                i += 1
            out.append(f"{indent}</linklist>")
        elif ln.startswith("- ") or ln == "-":
            out.append(f"{indent}<list>")
            while i < n and (lines[i].strip().startswith("- ") or lines[i].strip() == "-"):
                item = lines[i].strip()[1:].strip()
                out.append(f"{indent}\t<li>{_inline(item)}</li>")
                i += 1
            out.append(f"{indent}</list>")
        elif ln.startswith("|"):
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not re.match(r"^[\s:\-]+$", "".join(cells)):
                    rows.append(cells)
                i += 1
            out.append(f"{indent}<table>")
            for r in rows:
                out.append(f"{indent}\t<tr>")
                for c in r:
                    out.append(f"{indent}\t\t<td>{_inline(c)}</td>")
                out.append(f"{indent}\t</tr>")
            out.append(f"{indent}</table>")
        else:
            out.append(f"{indent}<p>{_inline(ln)}</p>")
            i += 1
    return "\n".join(out)


def _inline(md: str) -> str:
    return mdfmt.md_inline_to_fg(md)


def _bitmap_path(img_md_path: str, rules: Rules) -> tuple[str, str]:
    """Return (filename, fg_bitmap_path)."""
    name = Path(img_md_path).name
    return name, f"campaign/images/{name}"


def _image_xml(block: dict, rules: Rules, indent: str) -> str:
    name, bitmap = _bitmap_path(block.get("image", ""), rules)
    s = []
    s.append(f"{indent}<image type=\"image\">")
    s.append(f"{indent}\t<allowplayerdrawing>on</allowplayerdrawing>")
    s.append(f"{indent}\t<color>#FFFFFFFF</color>")
    s.append(f"{indent}\t<layers>")
    s.append(f"{indent}\t\t<layer>")
    s.append(f"{indent}\t\t\t<name>{X(name)}</name>")
    s.append(f"{indent}\t\t\t<id>0</id>")
    s.append(f"{indent}\t\t\t<parentid>-1</parentid>")
    s.append(f"{indent}\t\t\t<type>image</type>")
    s.append(f"{indent}\t\t\t<bitmap>{X(bitmap)}</bitmap>")
    s.append(f"{indent}\t\t</layer>")
    s.append(f"{indent}\t</layers>")
    s.append(f"{indent}</image>")
    return "\n".join(s)


def block_to_xml(block: dict, bid: int, order: int, rules: Rules, indent: str) -> str:
    t = block["type"]
    i2 = indent + "\t"
    i3 = i2 + "\t"
    parts = [f"{indent}<{_id(bid)}>"]
    frame_attr = ""
    if block.get("frame"):
        frame_attr = f"{i2}<frame type=\"string\">{X(block['frame'])}</frame>"

    if t in ("text", "list", "linklist", "table"):
        parts.append(f"{i2}<blocktype type=\"string\">singletext</blocktype>")
        if frame_attr:
            parts.append(frame_attr)
        parts.append(f"{i2}<order type=\"number\">{order}</order>")
        parts.append(f"{i2}<text type=\"formattedtext\">")
        if t == "text":
            parts.append(_formattedtext(block["lines"], i3))
        elif t == "list":
            parts.append(f"{i3}<list>")
            for it in block["items"]:
                parts.append(f"{i3}\t<li>{_inline(it)}</li>")
            parts.append(f"{i3}</list>")
        elif t == "linklist":
            parts.append(f"{i3}<linklist>")
            for label, url in block["links"]:
                parts.append(f"{i3}\t<link class=\"\" recordname=\"{X(url)}\">"
                             f"{X(label)}</link>")
            parts.append(f"{i3}</linklist>")
        elif t == "table":
            parts.append(f"{i3}<table>")
            for row in block["rows"]:
                parts.append(f"{i3}\t<tr>")
                for cell in row:
                    parts.append(f"{i3}\t\t<td>{_inline(cell)}</td>")
                parts.append(f"{i3}\t</tr>")
            parts.append(f"{i3}</table>")
        parts.append(f"{i2}</text>")

    elif t == "header":
        parts.append(f"{i2}<blocktype type=\"string\">header</blocktype>")
        if frame_attr:
            parts.append(frame_attr)
        parts.append(f"{i2}<order type=\"number\">{order}</order>")
        parts.append(f"{i2}<text type=\"string\">{X(block['text'])}</text>")

    elif t == "dualtext":
        parts.append(f"{i2}<blocktype type=\"string\">dualtext</blocktype>")
        if frame_attr:
            parts.append(frame_attr)
        parts.append(f"{i2}<order type=\"number\">{order}</order>")
        parts.append(f"{i2}<text type=\"formattedtext\">")
        parts.append(_rich_ft(block["left"], i3))
        parts.append(f"{i2}</text>")
        parts.append(f"{i2}<text2 type=\"formattedtext\">")
        parts.append(_rich_ft(block["right"], i3))
        parts.append(f"{i2}</text2>")

    elif t in ("image", "imageright", "imageleft"):
        parts.append(f"{i2}<blocktype type=\"string\">{t}</blocktype>")
        parts.append(f"{i2}<button_image_scaledown type=\"number\">0</button_image_scaledown>")
        parts.append(f"{i2}<button_image_scaleup type=\"number\">0</button_image_scaleup>")
        parts.append(f"{i2}<caption type=\"string\">{X(block.get('caption',''))}</caption>")
        if frame_attr:
            parts.append(frame_attr)
        parts.append(_image_xml(block, rules, i2))
        parts.append(f"{i2}<imagelink type=\"windowreference\">")
        parts.append(f"{i2}\t<class />")
        parts.append(f"{i2}\t<recordname />")
        parts.append(f"{i2}</imagelink>")
        parts.append(f"{i2}<order type=\"number\">{order}</order>")
        parts.append(f"{i2}<scale type=\"number\">{block.get('scale',100)}</scale>")
        if t in ("imageright", "imageleft"):
            parts.append(f"{i2}<text type=\"formattedtext\">")
            parts.append(_rich_ft(block.get("text", []), i3))
            parts.append(f"{i2}</text>")

    parts.append(f"{indent}</{_id(bid)}>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Assemble reference XML
# ---------------------------------------------------------------------------

def build_reference_xml(doc: mdmodel.Document, rules: Rules) -> str:
    L = ["\t<reference>"]
    # ---- refmanualdata ----
    L.append("\t\t<refmanualdata>")
    page_record: dict[int, str] = {}   # id(flat page index) -> recordname id
    for pidx, page in enumerate(doc.pages, 1):
        pid = _id(pidx)
        page_record[id(page)] = pid
        L.append(f"\t\t\t<{pid}>")
        L.append("\t\t\t\t<blocks>")
        blocks = parse_blocks(page.body)
        for bnum, b in enumerate(blocks, 1):
            L.append(block_to_xml(b, bnum, bnum, rules, "\t\t\t\t\t"))
        L.append("\t\t\t\t</blocks>")
        L.append("\t\t\t\t<locked type=\"number\">0</locked>")
        L.append(f"\t\t\t\t<name type=\"string\">{X(page.title)}</name>")
        L.append(f"\t\t\t</{pid}>")
    L.append("\t\t</refmanualdata>")

    # ---- refmanualindex ----
    L.append("\t\t<refmanualindex>")
    L.append("\t\t\t<chapters>")
    for cnum, ch in enumerate(doc.chapters, 1):
        cid = _id(cnum)
        L.append(f"\t\t\t\t<{cid}>")
        L.append(f"\t\t\t\t\t<name type=\"string\">{X(ch.title)}</name>")
        L.append(f"\t\t\t\t\t<order type=\"number\">{cnum}</order>")
        L.append("\t\t\t\t\t<subchapters>")
        for snum, sub in enumerate(ch.subchapters, 1):
            sid = _id(snum)
            L.append(f"\t\t\t\t\t\t<{sid}>")
            L.append(f"\t\t\t\t\t\t\t<name type=\"string\">{X(sub.title)}</name>")
            L.append(f"\t\t\t\t\t\t\t<order type=\"number\">{snum}</order>")
            L.append("\t\t\t\t\t\t\t<refpages>")
            for pnum, page in enumerate(sub.pages, 1):
                rid = _id(pnum)
                rec = page_record[id(page)]
                L.append(f"\t\t\t\t\t\t\t\t<{rid}>")
                L.append("\t\t\t\t\t\t\t\t\t<listlink type=\"windowreference\">")
                L.append("\t\t\t\t\t\t\t\t\t\t<class>story_book_page_advanced</class>")
                L.append(f"\t\t\t\t\t\t\t\t\t\t<recordname>reference.refmanualdata.{rec}</recordname>")
                L.append("\t\t\t\t\t\t\t\t\t</listlink>")
                L.append(f"\t\t\t\t\t\t\t\t\t<name type=\"string\">{X(page.title)}</name>")
                L.append(f"\t\t\t\t\t\t\t\t\t<order type=\"number\">{pnum}</order>")
                L.append(f"\t\t\t\t\t\t\t\t</{rid}>")
            L.append("\t\t\t\t\t\t\t</refpages>")
            L.append(f"\t\t\t\t\t\t</{sid}>")
        L.append("\t\t\t\t\t</subchapters>")
        L.append(f"\t\t\t\t</{cid}>")
    L.append("\t\t\t</chapters>")
    L.append("\t\t</refmanualindex>")
    L.append("\t</reference>")
    return "\n".join(L)


def replace_reference(db_text: str, new_reference_xml: str) -> str:
    """Replace the <reference>...</reference> element, or insert one if absent."""
    pat = re.compile(r"[\t ]*<reference>.*?</reference>", re.DOTALL)
    if pat.search(db_text):
        return pat.sub(lambda _: new_reference_xml, db_text, count=1)
    # no reference element: insert before </root>
    return db_text.replace("</root>", new_reference_xml + "\n</root>")


def run_export(rules: Rules) -> None:
    print("\n=== Export Sourcebook to Fantasy Grounds ===")
    print("!! WARNING: Fantasy Grounds MUST be closed first, or it will\n"
          "   overwrite db.xml when it exits and your export will be lost.")
    if input("Is Fantasy Grounds closed? (y/N): ").strip().lower() != "y":
        print("Aborted.")
        return

    sb = config.sourcebook_path()
    if not sb.exists():
        print("No sourcebook.md to export.")
        return
    db = rules.db_path
    if not db.exists():
        print(f"db.xml not found at {db}. Check campaign_folder in rules.json.")
        return

    # Always normalize to FG nesting first (in memory), so the Markdown never
    # has to be perfectly nested before export.
    from . import normalize as _normalize
    raw = sb.read_text(encoding="utf-8")
    doc = mdmodel.parse_text(_normalize.normalize(raw))
    ref_xml = build_reference_xml(doc, rules)
    db_text = db.read_text(encoding="utf-8")
    new_text = replace_reference(db_text, ref_xml)

    # validate
    try:
        ET.fromstring(new_text)
    except ET.ParseError as e:
        print(f"ABORTED: generated XML is invalid ({e}). db.xml left unchanged.")
        bad = config.cwd() / "export_debug.xml"
        bad.write_text(new_text, encoding="utf-8")
        print(f"Wrote {bad} for inspection.")
        return

    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = db.with_name(f"db.backup_{stamp}.xml")
    backup.write_text(db_text, encoding="utf-8")
    db.write_text(new_text, encoding="utf-8")

    print(f"\nExported {len(doc.pages)} pages to {db}")
    print(f"Backup saved: {backup.name}")
    print("Re-open Fantasy Grounds to see the updated reference manual.")
