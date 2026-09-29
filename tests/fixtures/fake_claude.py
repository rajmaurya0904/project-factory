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
