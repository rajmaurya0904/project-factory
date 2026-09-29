#!/usr/bin/env python3
"""Fake `claude` binary for testing factory.agent. Behavior is picked by a
magic marker inside the prompt text (the argument right after `-p`), so
tests don't need custom CLI flags this script would have to also accept."""

import json
import sys
import time


def main() -> None:
    argv = sys.argv[1:]
    prompt = argv[argv.index("-p") + 1] if "-p" in argv else ""

    if "SLEEP" in prompt:
        time.sleep(5)

    if "RATE_LIMIT" in prompt:
        print("Error: rate limit exceeded, please try again later", file=sys.stderr)
        sys.exit(1)

    if "BAD_JSON" in prompt:
        print("not json at all")
        sys.exit(0)

    if "FAIL" in prompt:
        print(json.dumps({"result": "partial", "cost_usd": 0.01}))
        sys.exit(2)

    if "IDEATE_OK" in prompt:
        ideas = [
            {
                "title": "CLI Weather Widget",
                "category": "cli",
                "pitch": "A tiny CLI that prints today's weather for your saved city.",
                "source": "hand-picked for tests",
                "est_scope_hours": 6,
            },
            {
                "title": "Markdown Link Checker",
                "category": "devtool",
                "pitch": "Scans a repo's Markdown files for dead links.",
                "source": "hand-picked for tests",
                "est_scope_hours": 8,
            },
        ]
        print(json.dumps({"result": json.dumps(ideas), "cost_usd": 0.1}))
        sys.exit(0)

    if "IDEATE_BAD_JSON" in prompt:
        print(json.dumps({"result": "not a json array at all", "cost_usd": 0.1}))
        sys.exit(0)

    if "IDEATE_MISSING_FIELD" in prompt:
        ideas = [{"title": "Incomplete Idea", "category": "cli", "pitch": "Missing fields."}]
        print(json.dumps({"result": json.dumps(ideas), "cost_usd": 0.1}))
        sys.exit(0)

    if "IDEATE_BAD_CATEGORY" in prompt:
        ideas = [
            {
                "title": "Bad Category Idea",
                "category": "not-a-real-category",
                "pitch": "p",
                "source": "s",
                "est_scope_hours": 5,
            }
        ]
        print(json.dumps({"result": json.dumps(ideas), "cost_usd": 0.1}))
        sys.exit(0)

    print(
        json.dumps(
            {
                "result": "ok",
                "usage": {"input_tokens": 100, "output_tokens": 50},
                "cost_usd": 0.05,
            }
        )
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
