#!/usr/bin/env python3
"""Fake `gitleaks` binary: reports a leak if any file under --source contains
the marker string PLANT_SECRET, mimicking `gitleaks detect --exit-code 1`."""

import sys
from pathlib import Path


def main() -> None:
    argv = sys.argv[1:]
    source = argv[argv.index("--source") + 1] if "--source" in argv else "."
    for path in Path(source).rglob("*"):
        if path.is_file() and "PLANT_SECRET" in path.read_text(errors="ignore"):
            print(f"leak: {path}")
            sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
