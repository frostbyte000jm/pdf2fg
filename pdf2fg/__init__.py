"""pdf2fg - Convert a TTRPG sourcebook PDF into Markdown, then into a
Fantasy Grounds (FG) reference manual (db.xml), and back again.

Word-for-word faithful: the parser only ever copies text out of the PDF.
No content is rewritten or summarised. The only changes are structural
(formatting tags) required by Fantasy Grounds' limited markup.
"""

__version__ = "1.0.0"
