#!/usr/bin/env python3
"""Fake `gh` binary for testing factory.validate's search_existing_repos.
Behavior is picked by a marker inside the search keywords (the idea title)."""

import json
import sys
from datetime import UTC, datetime, timedelta


def main() -> None:
    argv = sys.argv[1:]
    keywords = argv[argv.index("repos") + 1] if "repos" in argv else ""

    if "GH_FAIL" in keywords:
        print("gh: search failed", file=sys.stderr)
        sys.exit(1)

    if "GH_BAD_JSON" in keywords:
        print("not json")
        sys.exit(0)

    now = datetime.now(UTC)
    if "HAS_COMPETITOR" in keywords:
        repos = [
            {
                "name": "big-existing-tool",
                "stargazersCount": 5000,
                "pushedAt": (now - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "description": "Does the same thing already.",
            }
        ]
    elif "STALE_COMPETITOR" in keywords:
        repos = [
            {
                "name": "abandoned-tool",
                "stargazersCount": 9000,
                "pushedAt": (now - timedelta(days=800)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "description": "Used to do this, now abandoned.",
            }
        ]
    else:
        repos = []

    print(json.dumps(repos))
    sys.exit(0)


if __name__ == "__main__":
    main()
