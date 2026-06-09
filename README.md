# pdf2fg — PDF → Markdown → Fantasy Grounds

A CLI that helps you convert a tabletop sourcebook **PDF** into a **Fantasy
Grounds** reference manual (`db.xml`), going through an editable **Markdown**
file in the middle so you stay in full control of the text.

It is built for **faithful, word‑for‑word** conversion. The parser only ever
*copies* text out of the PDF — it never rewrites, summarises or invents
anything. The only changes are the structural formatting tags Fantasy Grounds
needs. Nothing is run through an AI model during conversion, so there are no
hallucinations to worry about when you sell the ruleset.

This folder is the **template**. Copy the whole folder to start a new ruleset,
drop the new PDF inside, and run the CLI from there.

---

## 1. Install (once)

You need Python 3.9+ installed. Then, from inside this folder:

```
pip install -r requirements.txt
```

## 2. Run

```
python pdf2fg.py
```

or just double‑click **`run.bat`** on Windows.

The CLI works from **the folder it is launched in**. Everything for one
ruleset lives together:

```
<your ruleset folder>/
├─ pdf2fg/                 the tool (don't edit)
├─ pdf2fg.py , run.bat     launchers
├─ YourBook.pdf            the PDF you are converting   ← you add this
├─ rules.json             created by Setup; tells the tool how the PDF is laid out
├─ raw_scan.md            created by Setup; paste into an AI chat to refine rules.json
└─ sourcebook.md          the working Markdown (created by the tool)
```

The Fantasy Grounds **campaign folder** (which holds `db.xml` and `images/`)
lives wherever FG keeps it; its path is stored in `rules.json`, e.g.
`C:\Users\you\...\FG_Unity\campaigns\My Ruleset`.

---

## 3. Workflow / Menu

### Setup (runs automatically until `rules.json` is complete)
Asks for the FG campaign folder and a small page range, scans it, and writes:
- `rules.json` — a **best‑guess** Rules File (already usable).
- `raw_scan.md` — font/size statistics plus AI instructions. If the automatic
  guesses look wrong, paste the whole file into Claude/ChatGPT and it will hand
  back a corrected `rules.json`. PDFs are wildly inconsistent, so the Rules File
  is the anchor the tool uses to recognise bold/italic/headings/etc.

### Main menu
1. **Create Table of Contents** — give the PDF page range of the printed TOC.
   The tool detects the heading layers and writes `#`/`##`/`###` into
   `sourcebook.md` (see "How TOC detection works" below). Then open that file
   and fix any mis‑leveled lines by hand.
2. **Add Content** — pick a `###` Page from the list, give a PDF page range, and
   the tool parses text + images into Markdown *under that page*. Images are
   cropped to PNGs in the campaign `images/` folder.
3. **Import Sourcebook from FG** — rebuild `sourcebook.md` from `db.xml` (so
   edits you made inside FG aren't lost).
4. **Export Sourcebook to FG** — rebuild the `<reference>` manual inside
   `db.xml` from `sourcebook.md`. **Close Fantasy Grounds first** or it will
   overwrite the file on exit. A timestamped backup (`db.backup_*.xml`) is made
   automatically, the structure is normalized (see option 5), and the result is
   validated as XML before saving.
5. **Fix structure for FG** — rewrites `sourcebook.md` so every Page sits under
   a Subchapter and every Subchapter under a Chapter, inserting the missing
   parents/children by duplicating titles (e.g. a Chapter `THE GAME` with no
   Subchapter gets `## THE GAME` + `### THE GAME`). A backup
   (`sourcebook.prenormalize.md`) is saved first. Export runs this automatically,
   so you only need it if you want to see/edit the normalized file yourself.
6. **Re‑run Setup / rescan fonts.**

### How TOC detection works
TOCs vary wildly, so the tool only claims what it can see and biases toward
**Page (`###`)** so you have less to demote:

- It tries to tell **three** layers apart and maps them to `#` / `##` / `###`.
  Chapters are the entries in the most distinctive (largest) heading font;
  Subchapters are ALL‑CAPS entries; Pages are everything else.
- If only **two** layers are distinguishable, it uses `##` and `###` (no
  chapters) and you promote chapters by hand.
- If only **one**, everything becomes `###` and you build the structure
  yourself.

You can force the chapter font by listing it under `toc.chapter_fonts` in
`rules.json`. Expect to correct a handful of lines — wrapped titles, all‑caps
page names, etc. — that's the intended workflow. Then run option 5 (or just
Export) to make it FG‑valid.

---

## 4. The Rules File (`rules.json`)

| Field | Meaning |
|--|--|
| `campaign_folder` | Path to the FG campaign (contains `db.xml` + `images/`). |
| `images_subfolder` | Usually `images`. |
| `pdf_file` | The PDF filename in this folder. |
| `page_offset` | Printed page number minus PDF page index (used in image names). |
| `fonts.bold / italic / bold_italic / regular` | Font names mapped to each style. List the name without the subset prefix, e.g. `Exo2-Bold`. |
| `headings.page_title_min_size` | Font size at/above which a line is a Page title. |
| `headings.heading_min_size` | Size for an in‑page `#### Heading`. |
| `headings.body_size` | Normal paragraph size. |
| `columns.mode` | `auto` or `single`. `gutter_x` forces a split position. |
| `lists.bullet_glyphs` / `bullet_fonts` | Characters / marker fonts that start a bullet. A line whose first glyph is in a `bullet_fonts` font becomes a list item (handles dingbat bullets like the SaV ship "►"). Setup auto‑detects these and lists candidates in `raw_scan.md`. |
| `paragraphs.gap_ratio` | How the parser tells a wrapped **next line** from a real **paragraph break**. Within a paragraph, wrapped lines sit one "line pitch" apart; a new paragraph adds extra space. The parser starts a new paragraph when the gap between two body lines exceeds `gap_ratio` × the normal pitch (default `1.3`). Setup measures the pitch and suggests a value in `raw_scan.md`. |
| `paragraphs.indent_pts` | For books that **indent** new paragraphs instead of spacing them: start a new paragraph when the first line is indented at least this many points past the column margin. `0` disables it. |
| `paragraphs.detect_breaks` | Set `false` to go back to the old behaviour (collapse everything under a heading into one paragraph and split it by hand). |
| `ignore_rotated_text` | `true` drops vertical/rotated text such as the "OVERVIEW // 1" margin tabs many books print up the page edge. |

---

## 5. Markdown ↔ Fantasy Grounds formatting

The conversion is a strict mapping, so you can hand‑author or clean up
`sourcebook.md` and know exactly what you'll get in FG. The parser fills in
what it can detect reliably (bold, italic, underline, headings, lists, tables,
two‑column flow, images); the rest you add by hand using the markup below.

### Structure
| Markdown | Fantasy Grounds |
|--|--|
| `# Title` | Chapter (index) |
| `## Title` | Subchapter (index) |
| `### Title` | Page (a reference page) |

### Inside a page
| Markdown | Fantasy Grounds | Notes |
|--|--|--|
| `#### Heading` | `<h>` | heading inside a text block |
| plain text | `<p>` | a paragraph |
| `**bold**` | `<b>` | |
| `*italic*` | `<i>` | |
| `<u>underline</u>` | `<u>` | |
| `***bold italic***` | `<b><i>` | |
| `<u>***all three***</u>` | `<b><i><u>` | underline is outermost in Markdown |
| `- item` | `<list><li>` | consecutive `-` lines = one list |
| `\| a \| b \|` (table) | `<table>` | standard Markdown pipe table |
| `[label](url)` on its own line(s) | `<linklist>` | consecutive link lines group together; URL stored on the link |
| `[label](url)` inside a sentence | inline `<link>` | preserved verbatim |
| `<speak>text</speak>` | `<frame>` | the “chat bubble” callout |

### The blocks you asked about (my suggested markup)
These have no obvious Markdown equivalent, so pdf2fg defines simple, readable
markers for them. The parser does **not** guess these — add them by hand.

| Markdown | Fantasy Grounds | Meaning |
|--|--|--|
| `##### Center Header` | `header` block | a centred header bar |
| `![caption](images/NAME.png)` | `image` block | a centred image |
| `[[imageright scale=70]]` … `![cap](img)` … text … `[[/image]]` | `imageright` | image on the right, text on the left |
| `[[imageleft scale=70]]` … `![cap](img)` … text … `[[/image]]` | `imageleft` | image on the left, text on the right |
| `[[dual]]` … `[[right]]` … `[[/dual]]` | `dualtext` | two side‑by‑side text columns; everything before `[[right]]` is the left column, everything after is the right (legacy `[[vs]]` is still accepted) |
| `[sidebar]` on its own line **before** a block | `<frame type="string">sidebar</frame>` | wraps the next block in a decorative frame. Use the frame’s FG name, e.g. `[referenceblock-sidebar]`. |

Example:

```md
### Introduction
#### Welcome
Welcome to the World of **THE GAME**, where you’ll learn *lots*.

##### A Centered Header

[sidebar]
This paragraph sits inside a sidebar frame.

[[dual]]
Left column text.
[[right]]
Right column text.
[[/dual]]

![The crew](images/01_01_05_Introduction_00.png)
```

### Image file names
Saved into the campaign `images/` folder as:

```
CC_SC_PN_PageTitle_CT.png
```

`CC` chapter index · `SC` subchapter index · `PN` PDF page number ·
`PageTitle` the page’s name · `CT` a 00‑based counter for multiple images.

---

## 6. Notes & limitations
- **Close Fantasy Grounds before Export (option 4).** A backup is always made.
- Complex layouts (vertical margin tabs, overlapping sidebars, fancy
  example‑of‑play boxes) won’t parse perfectly. Parse what you can, then clean
  up `sourcebook.md` by hand — that’s the intended workflow.
- Tables and images are appended at the end of a parsed page; reorder them in
  the Markdown to match the book.
- Round‑trip (Export → edit in FG → Import) preserves all supported block types.
- The mapping is intentionally lossless for everything in the table above, so
  nothing you write in Markdown is dropped on the way to FG.
```
