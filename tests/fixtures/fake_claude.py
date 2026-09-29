#!/usr/bin/env python3
"""Fake `claude` binary for testing factory.agent. Behavior is picked by a
magic marker inside the prompt text (the argument right after `-p`), so
tests don't need custom CLI flags this script would have to also accept."""

import json
import sys
import time
from pathlib import Path


def main() -> None:
    argv = sys.argv[1:]
    prompt = argv[argv.index("-p") + 1] if "-p" in argv else ""
    model = argv[argv.index("--model") + 1] if "--model" in argv else ""

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

    if "VALIDATE_HIGH_SCORE" in prompt:
        score = {"score": 9, "rationale": "Specific, useful, nothing else does this."}
        print(json.dumps({"result": json.dumps(score), "cost_usd": 0.02}))
        sys.exit(0)

    if "VALIDATE_LOW_SCORE" in prompt:
        score = {"score": 2, "rationale": "Too vague to be useful on its own."}
        print(json.dumps({"result": json.dumps(score), "cost_usd": 0.02}))
        sys.exit(0)

    if "VALIDATE_BAD_JSON" in prompt:
        print(json.dumps({"result": "not a json object", "cost_usd": 0.02}))
        sys.exit(0)

    if "PLAN_OK" in prompt:
        tasks = [
            {
                "title": "Add core function",
                "description": "Implement the main feature.",
                "acceptance": "pytest tests/test_core.py passes",
                "complexity": "complex",
            },
            {
                "title": "Add test for core function",
                "description": "Cover the main feature with a test.",
                "acceptance": "pytest -q exits 0",
                "complexity": "simple",
            },
        ]
        print(json.dumps({"result": json.dumps(tasks), "cost_usd": 0.05}))
        sys.exit(0)

    if "PLAN_BAD_JSON" in prompt:
        print(json.dumps({"result": "not a json array", "cost_usd": 0.05}))
        sys.exit(0)

    if "PLAN_MISSING_FIELD" in prompt:
        tasks = [{"title": "Incomplete", "description": "d"}]
        print(json.dumps({"result": json.dumps(tasks), "cost_usd": 0.05}))
        sys.exit(0)

    if "PLAN_BAD_COMPLEXITY" in prompt:
        tasks = [
            {"title": "T", "description": "d", "acceptance": "a", "complexity": "medium"}
        ]
        print(json.dumps({"result": json.dumps(tasks), "cost_usd": 0.05}))
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

    if "RELEASE_OK" in prompt:
        Path("CHANGELOG.md").write_text(
            "# Changelog\n\n## v0.1.0\n\n- Initial release.\n", encoding="utf-8"
        )
        print(json.dumps({"result": "wrote CHANGELOG.md", "cost_usd": 0.01}))
        sys.exit(0)

    if "BUILD_OK" in prompt:
        Path("feature.py").write_text("def add(a, b):\n    return a + b\n")
        Path("test_feature.py").write_text(
            "from feature import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
        )
        print(json.dumps({"result": "ok", "cost_usd": 0.01}))
        sys.exit(0)

    if "BUILD_BAD" in prompt:
        Path("test_bad.py").write_text("def test_bad():\n    assert False\n")
        print(json.dumps({"result": "ok", "cost_usd": 0.01}))
        sys.exit(0)

    if "MODEL_GATE" in prompt:
        # Behavior depends on which --model this session was invoked with,
        # so tests can prove an escalation retry actually ran on Sonnet.
        if "haiku" in model:
            Path("test_bad.py").write_text("def test_bad():\n    assert False\n")
        else:
            Path("good.py").write_text("def add(a, b):\n    return a + b\n")
            Path("test_good.py").write_text(
                "from good import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
            )
        print(json.dumps({"result": "ok", "cost_usd": 0.01}))
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
