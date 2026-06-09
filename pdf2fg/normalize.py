"""Normalize sourcebook.md to Fantasy Grounds' required nesting.

FG requires:
  * every Page (###) sits under a Subchapter (##),
  * every Subchapter (##) sits under a Chapter (#),
  * a Subchapter must contain at least one Page before the next Subchapter,
  * a Chapter must contain at least one Subchapter before the next Chapter.

Where the structure is incomplete, this inserts the missing parent/child by
duplicating the relevant title (e.g. a Chapter "THE GAME" with no Subchapter
gets a "## THE GAME" then "### THE GAME"). Existing page content is preserved.

This is the transformation from a raw parsed TOC into the final FG‑ready file.
Run it from the menu, or it runs automatically during Export.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import config

_H = re.compile(r"^(#{1,3})\s+(.*?)\s*$")


def _parse(text: str):
    preamble: list[str] = []
    items: list[dict] = []
    cur: dict | None = None
    in_code = False
    for ln in text.splitlines():
        if ln.strip().startswith("```"):
            in_code = not in_code
        m = None if in_code else _H.match(ln)
        if m:
            cur = {"level": len(m.group(1)), "title": m.group(2), "body": []}
            items.append(cur)
        else:
            (cur["body"] if cur else preamble).append(ln)
    return preamble, items


def normalize(text: str) -> str:
    preamble, items = _parse(text)
    out: list[tuple[int, str, list[str]]] = []
    cur_ch = {"v": None}
    cur_sub = {"v": None}
    sub_has_page = {"v": False}
    ch_has_sub = {"v": False}

    def flush_sub():
        if cur_sub["v"] is not None and not sub_has_page["v"]:
            out.append((3, cur_sub["v"], []))
            sub_has_page["v"] = True

    def flush_ch():
        flush_sub()
        if cur_ch["v"] is not None and not ch_has_sub["v"]:
            out.append((2, cur_ch["v"], []))
            out.append((3, cur_ch["v"], []))
            ch_has_sub["v"] = True

    for it in items:
        lvl, title, body = it["level"], it["title"], it["body"]
        if lvl == 1:
            flush_ch()
            out.append((1, title, body))
            cur_ch["v"] = title
            cur_sub["v"] = None
            ch_has_sub["v"] = False
            sub_has_page["v"] = False
        elif lvl == 2:
            if cur_ch["v"] is None:
                out.append((1, title, []))
                cur_ch["v"] = title
                ch_has_sub["v"] = False
            flush_sub()
            out.append((2, title, body))
            cur_sub["v"] = title
            ch_has_sub["v"] = True
            sub_has_page["v"] = False
        else:  # page
            if cur_ch["v"] is None:
                out.append((1, title, []))
                cur_ch["v"] = title
                ch_has_sub["v"] = False
            if cur_sub["v"] is None:
                out.append((2, cur_ch["v"], []))
                cur_sub["v"] = cur_ch["v"]
                ch_has_sub["v"] = True
                sub_has_page["v"] = False
            out.append((3, title, body))
            sub_has_page["v"] = True

    flush_ch()

    res: list[str] = list(preamble)
    for lvl, title, body in out:
        res.append("#" * lvl + " " + title)
        res.extend(body)
    return "\n".join(res).rstrip() + "\n"


def run_normalize(rules=None) -> None:
    print("\n=== Fix structure for Fantasy Grounds ===")
    sb = config.sourcebook_path()
    if not sb.exists():
        print("No sourcebook.md to fix.")
        return
    text = sb.read_text(encoding="utf-8")
    fixed = normalize(text)
    if fixed == text:
        print("Structure already valid - nothing to change.")
        return
    backup = sb.with_name("sourcebook.prenormalize.md")
    backup.write_text(text, encoding="utf-8")
    sb.write_text(fixed, encoding="utf-8")
    print(f"Inserted missing Subchapters/Pages so every Page has a parent.")
    print(f"Backup of the previous version: {backup.name}")
