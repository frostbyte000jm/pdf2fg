"""Interactive command-line menu for pdf2fg.

Run from inside a ruleset folder (a copy of the templateTemplate) that
contains your PDF. On first run, with no complete Rules File, it walks you
through Setup. Once rules.json points at a campaign folder + PDF and lists
fonts, it shows the main menu.
"""

from __future__ import annotations

import sys

from . import config
from . import setup as setup_mod
from . import toc as toc_mod
from . import content as content_mod
from . import md_to_fg, fg_to_md
from . import normalize as normalize_mod

BANNER = r"""
============================================================
  pdf2fg  -  PDF  ->  Markdown  ->  Fantasy Grounds
============================================================
  Faithful, word-for-word conversion of a sourcebook PDF
  into a Fantasy Grounds reference manual (db.xml).
  The only changes made are structural formatting tags.
------------------------------------------------------------
"""


def main(argv=None) -> int:
    print(BANNER)
    rules = config.load_rules()

    if not config.rules_is_complete(rules):
        if rules is None:
            print("No rules.json found in this folder.")
        else:
            print("rules.json is incomplete (needs campaign folder, PDF & fonts).")
        print("Starting Setup...\n")
        setup_mod.run_setup(rules)
        print("\nRe-run the CLI once your rules.json is ready.")
        return 0

    print(f"PDF       : {rules.data.get('pdf_file')}")
    print(f"Campaign  : {rules.data.get('campaign_folder')}")
    while True:
        print("\n------------------------------------------------------------")
        print(" 1) Create Table of Contents for sourcebook.md")
        print(" 2) Add Content")
        print(" 3) Import Sourcebook from FG  (db.xml -> sourcebook.md)")
        print(" 4) Export Sourcebook to FG    (sourcebook.md -> db.xml)")
        print("    (4 deletes & replaces the <reference> manual in FG)")
        print(" 5) Fix structure for FG       (every Page under a Subchapter, etc.)")
        print(" 6) Re-run Setup / rescan fonts")
        print(" 0) Quit")
        choice = input("Choose: ").strip()

        try:
            if choice == "1":
                toc_mod.run_create_toc(rules)
            elif choice == "2":
                content_mod.run_add_content(rules)
            elif choice == "3":
                fg_to_md.run_import(rules)
            elif choice == "4":
                md_to_fg.run_export(rules)
            elif choice == "5":
                normalize_mod.run_normalize(rules)
            elif choice == "6":
                setup_mod.run_setup(rules)
                rules = config.load_rules()
            elif choice in ("0", "q", "quit", "exit"):
                print("Bye.")
                return 0
            else:
                print("Unknown option.")
        except KeyboardInterrupt:
            print("\nInterrupted.")
        except Exception as e:
            import traceback
            print(f"\nError: {e}")
            traceback.print_exc()


if __name__ == "__main__":
    sys.exit(main())
