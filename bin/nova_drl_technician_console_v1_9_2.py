#!/usr/bin/env python3
"""
NOVA DRL Technician Console v1.9.2

Thin workflow layer over frozen NOVA components:
  - /opt/nova-drl/bin/nova-drl          -> frozen v1.5.16 search/report
  - /opt/nova-drl/bin/nova-drl-docx     -> editable DOCX generator v1.9.1
  - /opt/nova-drl/bin/nova-drl-docx-review -> DOCX reviewer v1.9.1

This file contains no repair knowledge and does not alter search semantics.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

NOVA = Path("/opt/nova-drl/bin/nova-drl")
DOCX = Path("/opt/nova-drl/bin/nova-drl-docx")
REVIEW = Path("/opt/nova-drl/bin/nova-drl-docx-review")
REPORT_DIR = Path("/mnt/drl-reports")


def run_capture(cmd):
    p = subprocess.run([str(x) for x in cmd], text=True, capture_output=True)
    return p.returncode, p.stdout, p.stderr


def slug_for_report(query: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", query.strip())
    s = s.strip("._-") or "REPORT"
    return s


def default_report_path(query: str) -> Path:
    return REPORT_DIR / f"NOVA_{slug_for_report(query)}_Repair_Reference.docx"


def search(query: str) -> int:
    rc, out, err = run_capture([NOVA, "--search", query])
    if out:
        # Replace only the old action hint. The underlying v1.5.16 output is unchanged.
        out = out.replace(
            "Actions: :pdf create/open printable PDF   :print send current report to printer",
            "Actions: :docx create/editable Word report   :review review saved edits   :new new search"
        )
        print(out.rstrip())
    if err:
        print(err.rstrip(), file=sys.stderr)
    return rc


def create_docx(query: str) -> int:
    rc, out, err = run_capture([DOCX, "--search", query])
    if out:
        print(out.rstrip())
    if err:
        print(err.rstrip(), file=sys.stderr)
    if rc == 0:
        print()
        print("Windows location:")
        print(r"  \\192.168.86.25\Public\NOVA_REPORTS")
        print("Open/edit the DOCX in Word. Technician Notes remain report-only.")
    return rc


def review_docx(query: str) -> int:
    path = default_report_path(query)
    if not path.exists():
        print(f"No default DOCX found for {query}:")
        print(f"  {path}")
        print("Create one first with :docx.")
        return 1
    # Reviewer may prompt interactively, so do not capture.
    return subprocess.call([str(REVIEW), str(path)])


def print_help():
    print()
    print("Commands")
    print("--------")
    print(":docx    Create/update editable Word report in NOVA_REPORTS")
    print(":review  Review knowledge-area edits in that Word report")
    print(":new     Search another DRL Part #")
    print(":help    Show commands")
    print(":quit    Exit")
    print()


def interactive(initial: Optional[str] = None) -> int:
    print("=" * 84)
    print("NOVA DRL TECHNICIAN CONSOLE  |  v1.9.2")
    print("Frozen search: v1.5.16  |  Editable DOCX: v1.9.1")
    print("=" * 84)

    current = initial.strip() if initial else ""

    while True:
        if not current:
            try:
                current = input("\nDRL Part # [or :quit]: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not current:
                continue
            if current.lower() in {":quit", ":q", "quit", "exit"}:
                return 0

        rc = search(current)
        if rc != 0:
            current = ""
            continue

        while current:
            try:
                choice = input("\nAction [:docx / :review / :new / :quit] [Enter=:new]: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0

            if not choice or choice.lower() in {":new", "new"}:
                current = ""
                break
            c = choice.lower()
            if c in {":quit", ":q", "quit", "exit"}:
                return 0
            if c in {":help", "help", "?"}:
                print_help()
                continue
            if c in {":docx", "docx"}:
                create_docx(current)
                continue
            if c in {":review", "review"}:
                review_docx(current)
                continue

            print("Unknown action. Use :docx, :review, :new, :help, or :quit.")

    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="NOVA DRL technician workflow console")
    ap.add_argument("--search", help="Start with a DRL Part #")
    ap.add_argument("--docx", metavar="DRL_PART", help="Create DOCX directly and exit")
    ap.add_argument("--review", metavar="DRL_PART", help="Review default DOCX directly and exit")
    args = ap.parse_args()

    if args.docx:
        return create_docx(args.docx)
    if args.review:
        return review_docx(args.review)
    return interactive(args.search)


if __name__ == "__main__":
    raise SystemExit(main())
