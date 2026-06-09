"""Inline formatting bridge.

Three representations of an inline run of styled text:
  1. Run objects (from the PDF)           -> Markdown            (runs_to_md)
  2. Markdown inline string               -> list[Segment]       (parse_inline)
  3. list[Segment]                        -> FG formattedtext    (segments_to_fg)
                                          -> Markdown            (segments_to_md)

Markdown conventions (also documented in README):
  **bold**            <b>
  *italic*            <i>
  <u>underline</u>    <u>
  ***bold italic***   <b><i>
  <u>***all***</u>    <b><i><u>   (underline outermost in MD)
  [label](url)        <link>      (url stored in recordname for round-trip)
  <speak>text</speak> <frame>     (chat bubble)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from xml.sax.saxutils import escape as _xml_escape

from .pdfio import Run


@dataclass
class Segment:
    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    link: str | None = None      # url (presence => this is a link)
    frame: bool = False          # <speak> chat bubble


# ---------------------------------------------------------------------------
# Run -> Markdown
# ---------------------------------------------------------------------------

def runs_to_md(runs: list[Run]) -> str:
    out = []
    for r in runs:
        t = r.text
        if not t:
            continue
        # preserve surrounding whitespace outside the markers
        lead = t[:len(t) - len(t.lstrip())]
        trail = t[len(t.rstrip()):]
        core = t.strip()
        if not core:
            out.append(t)
            continue
        if r.bold and r.italic:
            core = f"***{core}***"
        elif r.bold:
            core = f"**{core}**"
        elif r.italic:
            core = f"*{core}*"
        if r.underline:
            core = f"<u>{core}</u>"
        out.append(f"{lead}{core}{trail}")
    return "".join(out)


# ---------------------------------------------------------------------------
# Markdown -> Segments
# ---------------------------------------------------------------------------

_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_SPEAK_RE = re.compile(r"<speak>(.*?)</speak>", re.DOTALL)


def parse_inline(md: str) -> list[Segment]:
    """Tokenise an inline Markdown string into styled Segments."""
    segments: list[Segment] = []

    # First, peel off <speak> and links as atomic tokens via placeholders so
    # the emphasis scanner does not trip on their contents.
    tokens: list = []  # list of (kind, payload)

    pos = 0
    pattern = re.compile(r"<speak>.*?</speak>|\[[^\]]*\]\([^)]*\)", re.DOTALL)
    for m in pattern.finditer(md):
        if m.start() > pos:
            tokens.append(("text", md[pos:m.start()]))
        chunk = m.group(0)
        sp = _SPEAK_RE.match(chunk)
        if sp:
            tokens.append(("speak", sp.group(1)))
        else:
            lk = _LINK_RE.match(chunk)
            tokens.append(("link", (lk.group(1), lk.group(2))))
        pos = m.end()
    if pos < len(md):
        tokens.append(("text", md[pos:]))

    for kind, payload in tokens:
        if kind == "speak":
            for s in _scan_emphasis(payload):
                s.frame = True
                segments.append(s)
        elif kind == "link":
            label, url = payload
            segments.append(Segment(text=label, link=url))
        else:
            segments.extend(_scan_emphasis(payload))
    return [s for s in segments if s.text or s.link is not None]


def _scan_emphasis(text: str) -> list[Segment]:
    """Scan **/*/<u> emphasis. Underline tags toggle; * and ** toggle."""
    segs: list[Segment] = []
    bold = italic = underline = False
    i = 0
    buf = []

    def flush():
        if buf:
            segs.append(Segment(text="".join(buf), bold=bold, italic=italic,
                                underline=underline))
            buf.clear()

    n = len(text)
    while i < n:
        if text.startswith("<u>", i):
            flush(); underline = True; i += 3; continue
        if text.startswith("</u>", i):
            flush(); underline = False; i += 4; continue
        if text.startswith("***", i):
            flush(); bold = not bold; italic = not italic; i += 3; continue
        if text.startswith("**", i):
            flush(); bold = not bold; i += 2; continue
        if text.startswith("*", i):
            flush(); italic = not italic; i += 1; continue
        buf.append(text[i]); i += 1
    flush()
    return segs


# ---------------------------------------------------------------------------
# Segments -> FG formattedtext inline XML
# ---------------------------------------------------------------------------

def segments_to_fg(segments: list[Segment]) -> str:
    out = []
    for s in segments:
        if s.link is not None:
            # inline link inside prose - preserve verbatim (no content loss)
            out.append(f'<link class="" recordname="{_xml_escape(s.link)}">'
                       f'{_xml_escape(s.text)}</link>')
            continue
        t = _xml_escape(s.text)
        if s.frame:
            out.append(f"<frame>{t}</frame>")
            continue
        if s.underline:
            t = f"<u>{t}</u>"
        if s.italic:
            t = f"<i>{t}</i>"
        if s.bold:
            t = f"<b>{t}</b>"
        out.append(t)
    return "".join(out)


def md_inline_to_fg(md: str) -> str:
    return segments_to_fg(parse_inline(md))


def has_only_links(md: str) -> bool:
    segs = parse_inline(md)
    return bool(segs) and all(s.link is not None for s in segs)


def extract_links(md: str) -> list[tuple[str, str]]:
    return [(s.text, s.link or "") for s in parse_inline(md) if s.link is not None]


# ---------------------------------------------------------------------------
# Segments -> Markdown (for FG -> MD direction)
# ---------------------------------------------------------------------------

def segments_to_md(segments: list[Segment]) -> str:
    out = []
    for s in segments:
        if s.link is not None:
            out.append(f"[{s.text}]({s.link})")
            continue
        core = s.text
        if s.bold and s.italic:
            core = f"***{core}***"
        elif s.bold:
            core = f"**{core}**"
        elif s.italic:
            core = f"*{core}*"
        if s.underline:
            core = f"<u>{core}</u>"
        if s.frame:
            core = f"<speak>{core}</speak>"
        out.append(core)
    return "".join(out)
