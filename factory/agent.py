"""Subprocess wrapper for one-shot `claude -p` sessions.

Each call is a fresh, independent agent run (per plan section 7.5: one task,
one session). This module owns process invocation, timeout handling, JSON
result parsing, and rate-limit detection. It does not decide which model to
use for which stage -- see router.py for that.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass

# Substrings (checked case-insensitively) that indicate a subscription
# rate/usage limit was hit, rather than a real task failure. Matched against
# combined stdout+stderr since the exact wording varies by CLI version.
_RATE_LIMIT_MARKERS = (
    "rate limit",
    "usage limit",
    "quota exceeded",
    "try again later",
    "5-hour limit",
)


@dataclass(frozen=True)
class AgentResult:
    """Outcome of one `claude -p` invocation."""

    exit_code: int | None  # None if the process timed out before exiting
    timed_out: bool
    rate_limited: bool
    duration_sec: float
    result_text: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    raw_stdout: str
    raw_stderr: str

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.rate_limited


def is_rate_limited(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _RATE_LIMIT_MARKERS)


def _extract_usage(parsed: dict) -> tuple[int | None, int | None, float | None]:
    """Pull token counts and cost out of a parsed result, tolerating schema drift:
    either top-level cost_usd/input_tokens/output_tokens, or nested under "usage"."""
    usage = parsed.get("usage") if isinstance(parsed.get("usage"), dict) else {}
    input_tokens = parsed.get("input_tokens", usage.get("input_tokens"))
    output_tokens = parsed.get("output_tokens", usage.get("output_tokens"))
    cost_usd = parsed.get("cost_usd", usage.get("cost_usd"))
    return input_tokens, output_tokens, cost_usd


def run_agent(
    prompt: str,
    *,
    model: str,
    cwd: str,
    max_turns: int = 25,
    timeout_sec: int = 900,
    permission_mode: str = "acceptEdits",
    allowed_tools: str | None = None,
    disallowed_tools: str | None = None,
    append_system_prompt: str | None = None,
    claude_bin: str | Sequence[str] = "claude",
) -> AgentResult:
    """Run one `claude -p` session and return its parsed outcome.

    `claude_bin` is normally "claude", but tests pass a command prefix (e.g.
    [sys.executable, "fake_claude.py"]) to run a fake binary instead.
    """
    base_cmd = [claude_bin] if isinstance(claude_bin, str) else list(claude_bin)
    cmd = [
        *base_cmd,
        "-p",
        prompt,
        "--output-format",
        "json",
        "--model",
        model,
        "--max-turns",
        str(max_turns),
        "--permission-mode",
        permission_mode,
    ]
    if allowed_tools:
        cmd += ["--allowedTools", allowed_tools]
    if disallowed_tools:
        cmd += ["--disallowedTools", disallowed_tools]
    if append_system_prompt:
        cmd += ["--append-system-prompt", append_system_prompt]

    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout_sec
        )
    except subprocess.TimeoutExpired as e:
        duration = time.monotonic() - start
        stdout = e.stdout or ""
        stderr = e.stderr or ""
        return AgentResult(
            exit_code=None,
            timed_out=True,
            rate_limited=is_rate_limited(stdout + stderr),
            duration_sec=duration,
            result_text=None,
            input_tokens=None,
            output_tokens=None,
            cost_usd=None,
            raw_stdout=stdout,
            raw_stderr=stderr,
        )

    duration = time.monotonic() - start
    rate_limited = is_rate_limited(proc.stdout + proc.stderr)

    parsed: dict = {}
    try:
        loaded = json.loads(proc.stdout)
        if isinstance(loaded, dict):
            parsed = loaded
    except json.JSONDecodeError:
        pass

    input_tokens, output_tokens, cost_usd = _extract_usage(parsed)

    return AgentResult(
        exit_code=proc.returncode,
        timed_out=False,
        rate_limited=rate_limited,
        duration_sec=duration,
        result_text=parsed.get("result"),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        raw_stdout=proc.stdout,
        raw_stderr=proc.stderr,
    )
