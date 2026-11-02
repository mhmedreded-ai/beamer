"""Prepare GitHub release notes locally with explicit notification metadata."""

import argparse
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--notes", type=Path, required=True)
    parser.add_argument("--notify", choices=("quiet", "important"))
    args = parser.parse_args()
    lines = args.notes.read_text(encoding="utf-8").splitlines()
    marks = [line.strip() for line in lines if "beamer-notify:" in line]
    valid = {f"<!-- beamer-notify: {kind} -->": kind for kind in ("quiet", "important")}
    if len(marks) > 1 or any(mark not in valid for mark in marks):
        parser.error("notes must contain at most one valid beamer-notify metadata line")
    kind = args.notify or (valid[marks[0]] if marks else "quiet")
    notes = "\n".join(line for line in lines if "beamer-notify:" not in line).strip()
    sys.stdout.write(f"<!-- beamer-notify: {kind} -->\n\n{notes}\n")


if __name__ == "__main__":
    main()
