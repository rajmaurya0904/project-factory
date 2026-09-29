"""Pulls structured JSON out of free-form agent output text: strips markdown
code fences and tolerates prose wrapped around the actual JSON. Shared by
every stage that asks the agent to answer in JSON (ideate, validate, plan)."""

from __future__ import annotations

import json
import re

_CODE_FENCE_START = re.compile(r"^```(?:json)?\s*")
_CODE_FENCE_END = re.compile(r"\s*```$")


class JsonExtractError(ValueError):
    """Raised when no valid JSON of the requested shape could be found."""


def strip_code_fence(text: str) -> str:
    cleaned = text.strip()
    cleaned = _CODE_FENCE_START.sub("", cleaned)
    cleaned = _CODE_FENCE_END.sub("", cleaned)
    return cleaned.strip()


def extract_json(text: str | None, kind: str = "object") -> dict | list:
    """Parse `text` as JSON, tolerating a markdown fence or surrounding prose.
    `kind` is "object" or "array" and controls both which bracket pair is
    searched for as a fallback and the required top-level type."""
    if kind not in ("object", "array"):
        raise ValueError(f"kind must be 'object' or 'array', got {kind!r}")
    if not text:
        raise JsonExtractError("no text to parse")

    open_ch, close_ch, expected_type = (
        ("{", "}", dict) if kind == "object" else ("[", "]", list)
    )
    cleaned = strip_code_fence(text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        pattern = re.escape(open_ch) + r".*" + re.escape(close_ch)
        match = re.search(pattern, cleaned, re.DOTALL)
        if not match:
            raise JsonExtractError(
                f"could not find a JSON {kind} in output: {cleaned[:200]!r}"
            ) from None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as e:
            raise JsonExtractError(f"output was not valid JSON: {e}") from e

    if not isinstance(data, expected_type):
        raise JsonExtractError(f"output JSON was not a {kind}")
    return data
