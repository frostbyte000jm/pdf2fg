"""Shared PDF reading helpers built on pdfplumber (text/geometry) and
pypdfium2 (rendering image crops).

The central idea: turn a PDF page into an ordered list of "lines", where each
line is a list of formatted "runs" (text + bold/italic/underline flags). From
that intermediate representation both the TOC parser and the content parser do
their work, and the Markdown writer turns runs into inline markup.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Run:
    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    size: float = 0.0


@dataclass
class Line:
    runs: list[Run] = field(default_factory=list)
    x0: float = 0.0
    x1: float = 0.0
    top: float = 0.0
    bottom: float = 0.0
    size: float = 0.0          # dominant (max) font size on the line
    font: str = ""             # font of the largest word (subset prefix stripped)
    lead_font: str = ""        # font of the left-most word (for bullet markers)
    column: int = 0            # 0 = left/single, 1 = right

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs).strip()

    @property
    def is_blank(self) -> bool:
        return not self.text


@dataclass
class ImageRegion:
    bbox: tuple[float, float, float, float]   # (x0, top, x1, bottom) in PDF pts
    page_index: int                            # 0-based


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------

class PdfReader:
    def __init__(self, pdf_path: str | Path):
        self.path = str(pdf_path)
        self.plumber = pdfplumber.open(self.path)
        self._fp = pdfium.PdfDocument(self.path)

    def close(self):
        try:
            self.plumber.close()
        except Exception:
            pass

    @property
    def page_count(self) -> int:
        return len(self.plumber.pages)

    # -- geometry / underline helpers --------------------------------------

    @staticmethod
    def _horizontal_edges(page) -> list[dict]:
        edges = []
        for r in page.rects:
            if abs(r["top"] - r["bottom"]) <= 2.5 and (r["x1"] - r["x0"]) > 3:
                edges.append(r)
        for ln in page.lines:
            if abs(ln["top"] - ln["bottom"]) <= 2.5 and abs(ln["x1"] - ln["x0"]) > 3:
                edges.append({"x0": min(ln["x0"], ln["x1"]),
                              "x1": max(ln["x0"], ln["x1"]),
                              "top": ln["top"], "bottom": ln["bottom"]})
        return edges

    @staticmethod
    def _is_underlined(char, edges, max_gap: float) -> bool:
        cx0, cx1, cb = char["x0"], char["x1"], char["bottom"]
        for e in edges:
            if e["top"] >= cb - 1 and e["top"] <= cb + max_gap:
                if e["x0"] <= cx1 and e["x1"] >= cx0:
                    return True
        return False

    # -- column detection --------------------------------------------------

    @staticmethod
    def detect_gutter(words, page_width, forced=None) -> float | None:
        """Return an x coordinate splitting two columns, or None if 1 column.

        Tries several candidate split lines around the page centre and accepts
        the first where few words cross it and both sides hold real text. This
        is robust on dense pages where a strict empty-gutter test fails.
        """
        if forced:
            return forced
        if len(words) < 12:
            return None
        max_cross = max(2, int(0.03 * len(words)))
        best = None
        for frac in (0.50, 0.475, 0.525, 0.45, 0.55):
            cand = page_width * frac
            left = [w for w in words if (w["x0"] + w["x1"]) / 2 < cand]
            right = [w for w in words if (w["x0"] + w["x1"]) / 2 >= cand]
            if len(left) < 5 or len(right) < 5:
                continue
            cross = sum(1 for w in words if w["x0"] < cand - 2 and w["x1"] > cand + 2)
            if cross <= max_cross:
                return cand
            if best is None or cross < best[1]:
                best = (cand, cross)
        return None

    # -- main extraction ---------------------------------------------------

    def extract_lines(self, page_index: int, rules, force_single_column=False) -> list[Line]:
        page = self.plumber.pages[page_index]
        rounding = int(rules.data.get("size_rounding", 1))
        ul_cfg = rules.data.get("underline", {})
        detect_ul = bool(ul_cfg.get("detect", True))
        max_gap = float(ul_cfg.get("max_gap", 3.0))
        edges = self._horizontal_edges(page) if detect_ul else []

        # Drop rotated/vertical text (e.g. the "OVERVIEW // 1" margin tabs many
        # game books print up the page edge). Such glyphs are flagged
        # upright=False, so we filter them before reading words.
        src = page
        if rules.data.get("ignore_rotated_text", True):
            src = page.filter(lambda o: o.get("upright", True))
        words = src.extract_words(use_text_flow=False, keep_blank_chars=False,
                                  extra_attrs=["fontname", "size"])
        gutter = None
        if not force_single_column and rules.data.get("columns", {}).get("mode") != "single":
            gutter = self.detect_gutter(words, page.width,
                                        rules.data.get("columns", {}).get("gutter_x"))

        # group WORDS into visual lines by (column, rounded top). Working at
        # word granularity preserves inter-word spaces (page.chars are glyphs
        # only) and lets pdfplumber split words at font changes for us.
        def col_of(x_center):
            if gutter is None:
                return 0
            return 0 if x_center < gutter else 1

        def word_underlined(w):
            if not detect_ul:
                return False
            for e in edges:
                if e["top"] >= w["bottom"] - 1 and e["top"] <= w["bottom"] + max_gap:
                    if e["x0"] <= w["x1"] and e["x1"] >= w["x0"]:
                        return True
            return False

        # Cluster words into visual lines PER COLUMN using a size-relative
        # vertical tolerance, so a single visual line whose glyphs sit on
        # slightly different baselines (a bullet marker, small-caps label and
        # the body text often differ by 1-2pt) is kept as ONE line instead of
        # being split - and then re-sorting by x0 restores the reading order.
        from collections import defaultdict
        cols: dict[int, list] = defaultdict(list)
        for w in words:
            if not w["text"].strip():
                continue
            cols[col_of((w["x0"] + w["x1"]) / 2)].append(w)

        groups: list[tuple[int, list]] = []
        for col, cw in cols.items():
            cw.sort(key=lambda w: (w["top"], w["x0"]))
            cur: list = []
            anchor: float | None = None
            for w in cw:
                tol = max(3.0, 0.55 * float(w.get("size", 10)))
                if cur and anchor is not None and (w["top"] - anchor) <= tol:
                    cur.append(w)
                else:
                    if cur:
                        groups.append((col, cur))
                    cur = [w]
                    anchor = w["top"]
            if cur:
                groups.append((col, cur))

        _NO_SPACE_BEFORE = set(",.:;!?)]}")
        lines: list[Line] = []
        for col, ws in groups:
            ws.sort(key=lambda w: w["x0"])
            # build (text, role, underline) tokens with explicit spaces
            tokens: list[tuple[str, str, bool]] = []
            for idx, w in enumerate(ws):
                role = rules.font_role(w.get("fontname", ""))
                ul = word_underlined(w)
                tokens.append((w["text"], role, ul))
                if idx < len(ws) - 1 and ws[idx + 1]["text"][:1] not in _NO_SPACE_BEFORE:
                    tokens.append((" ", role, ul))   # space inherits left style
            runs: list[Run] = []
            cur_run: Run | None = None
            for text, role, ul in tokens:
                b = role in ("bold", "bold_italic")
                i = role in ("italic", "bold_italic")
                if cur_run and cur_run.bold == b and cur_run.italic == i and cur_run.underline == ul:
                    cur_run.text += text
                else:
                    if cur_run:
                        runs.append(cur_run)
                    cur_run = Run(text=text, bold=b, italic=i, underline=ul)
            if cur_run:
                runs.append(cur_run)
            if not runs:
                continue
            bigword = max(ws, key=lambda w: float(w.get("size", 0)))
            ln = Line(runs=runs,
                      x0=min(w["x0"] for w in ws),
                      x1=max(w["x1"] for w in ws),
                      top=min(w["top"] for w in ws),
                      bottom=max(w["bottom"] for w in ws),
                      size=round(max(float(w.get("size", 0)) for w in ws), rounding),
                      font=bigword.get("fontname", "").split("+", 1)[-1],
                      lead_font=ws[0].get("fontname", "").split("+", 1)[-1],
                      column=col)
            lines.append(ln)

        # reading order: column first, then top
        lines.sort(key=lambda l: (l.column, l.top))
        return lines

    def extract_tables(self, page_index: int) -> list[list[list[str]]]:
        page = self.plumber.pages[page_index]
        out = []
        try:
            for t in page.extract_tables():
                cleaned = [[(cell or "").strip().replace("\n", " ") for cell in row]
                           for row in t]
                if cleaned and any(any(c for c in row) for row in cleaned):
                    out.append(cleaned)
        except Exception:
            pass
        return out

    def find_images(self, page_index: int) -> list[ImageRegion]:
        page = self.plumber.pages[page_index]
        regions = []
        for im in page.images:
            bbox = (float(im["x0"]), float(im["top"]), float(im["x1"]), float(im["bottom"]))
            # skip tiny/decorative slivers
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]
            if w < 20 or h < 20:
                continue
            regions.append(ImageRegion(bbox=bbox, page_index=page_index))
        return self._merge_regions(regions)

    @staticmethod
    def _merge_regions(regions: list[ImageRegion]) -> list[ImageRegion]:
        """Merge overlapping/adjacent image tiles into single regions."""
        if not regions:
            return []
        boxes = [list(r.bbox) for r in regions]
        merged = True
        while merged:
            merged = False
            out = []
            used = [False] * len(boxes)
            for i in range(len(boxes)):
                if used[i]:
                    continue
                a = boxes[i]
                for j in range(i + 1, len(boxes)):
                    if used[j]:
                        continue
                    b = boxes[j]
                    if (a[0] <= b[2] + 6 and b[0] <= a[2] + 6 and
                            a[1] <= b[3] + 6 and b[1] <= a[3] + 6):
                        a = [min(a[0], b[0]), min(a[1], b[1]),
                             max(a[2], b[2]), max(a[3], b[3])]
                        used[j] = True
                        merged = True
                used[i] = True
                out.append(a)
            boxes = out
        pi = regions[0].page_index
        return [ImageRegion(bbox=tuple(b), page_index=pi) for b in boxes]

    def crop_image(self, region: ImageRegion, out_path: Path, scale: float = 3.0) -> None:
        """Render the page at `scale` and crop the region's bbox to a PNG."""
        page = self.plumber.pages[region.page_index]
        ph = float(page.height)
        fp_page = self._fp[region.page_index]
        bitmap = fp_page.render(scale=scale)
        pil = bitmap.to_pil().convert("RGB")
        x0, top, x1, bottom = region.bbox
        # pdfplumber top is distance from top; pypdfium render origin also top-left
        left = int(x0 * scale)
        upper = int(top * scale)
        right = int(x1 * scale)
        lower = int(bottom * scale)
        left = max(0, left); upper = max(0, upper)
        right = min(pil.width, right); lower = min(pil.height, lower)
        if right <= left or lower <= upper:
            return
        crop = pil.crop((left, upper, right, lower))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        crop.save(out_path, "PNG")

    # -- raw scan stats ----------------------------------------------------

    def font_size_stats(self, page_indices: list[int]) -> dict:
        from collections import Counter
        font_counter: Counter = Counter()
        size_counter: Counter = Counter()
        combo_counter: Counter = Counter()
        samples: dict[str, str] = {}
        rotated = 0
        # per-font word stats, to flag dingbat / bullet-marker fonts:
        word_total: Counter = Counter()
        word_single: Counter = Counter()      # words that are a single glyph
        lead_marker: Counter = Counter()      # times a font starts a line as 1-glyph
        for pi in page_indices:
            page = self.plumber.pages[pi]
            for c in page.chars:
                if not c.get("upright", True):
                    rotated += 1
                    continue
                fn = c.get("fontname", "?").split("+", 1)[-1]
                sz = round(float(c.get("size", 0)), 1)
                font_counter[fn] += 1
                size_counter[sz] += 1
                combo_counter[(fn, sz)] += 1
                key = f"{fn}@{sz}"
                if key not in samples and c["text"].strip():
                    samples[key] = ""
            words = page.extract_words(extra_attrs=["fontname", "size"])
            words_sorted = sorted(words, key=lambda w: (round(w["top"]), w["x0"]))
            prev_top = None
            for w in words_sorted:
                fn = w.get("fontname", "?").split("+", 1)[-1]
                sz = round(float(w.get("size", 0)), 1)
                key = f"{fn}@{sz}"
                if key in samples and not samples[key]:
                    samples[key] = w["text"][:40]
                word_total[fn] += 1
                if len(w["text"].strip()) == 1:
                    word_single[fn] += 1
                top = round(w["top"])
                if top != prev_top and len(w["text"].strip()) == 1:
                    lead_marker[fn] += 1   # 1-glyph word at the start of a line
                prev_top = top

        # marker-font candidates: name looks like a symbol font, OR the font is
        # used almost entirely as single-glyph line-leading words (bullets).
        marker_fonts = []
        for fn, tot in word_total.items():
            name_hit = any(k in fn.lower() for k in
                           ("wingding", "dingbat", "symbol", "webding", "marlett"))
            single_ratio = word_single[fn] / tot if tot else 0
            leads = lead_marker[fn]
            if name_hit or (single_ratio > 0.8 and leads >= 2):
                marker_fonts.append(fn)

        return {
            "fonts": font_counter,
            "sizes": size_counter,
            "combos": combo_counter,
            "samples": samples,
            "rotated": rotated,
            "marker_fonts": sorted(set(marker_fonts)),
        }
