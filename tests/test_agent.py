"""Tests for factory.agent, driven against a fake `claude` binary (no network,
no real API calls)."""

import sys
from pathlib import Path

from factory.agent import is_rate_limited, run_agent

FAKE_CLAUDE = [sys.executable, str(Path(__file__).parent / "fixtures" / "fake_claude.py")]


def test_success_parses_result_and_usage(tmp_path: Path) -> None:
    result = run_agent(
        "do the thing", model="sonnet", cwd=str(tmp_path), claude_bin=FAKE_CLAUDE
    )
    assert result.success is True
    assert result.exit_code == 0
    assert result.result_text == "ok"
    assert result.input_tokens == 100
    assert result.output_tokens == 50
    assert result.cost_usd == 0.05
    assert result.timed_out is False
    assert result.rate_limited is False


def test_nonzero_exit_is_not_success(tmp_path: Path) -> None:
    result = run_agent("FAIL please", model="haiku", cwd=str(tmp_path), claude_bin=FAKE_CLAUDE)
    assert result.success is False
    assert result.exit_code == 2
    assert result.result_text == "partial"  # still parsed even though it failed


def test_malformed_json_does_not_raise(tmp_path: Path) -> None:
    result = run_agent("BAD_JSON here", model="haiku", cwd=str(tmp_path), claude_bin=FAKE_CLAUDE)
    assert result.success is True  # exit 0, just no parseable JSON
    assert result.result_text is None
    assert result.raw_stdout.strip() == "not json at all"


def test_timeout_sets_timed_out_and_no_exit_code(tmp_path: Path) -> None:
    result = run_agent(
        "SLEEP forever", model="haiku", cwd=str(tmp_path), claude_bin=FAKE_CLAUDE, timeout_sec=1
    )
    assert result.timed_out is True
    assert result.exit_code is None
    assert result.success is False


def test_rate_limit_detected_from_stderr(tmp_path: Path) -> None:
    result = run_agent(
        "RATE_LIMIT me", model="haiku", cwd=str(tmp_path), claude_bin=FAKE_CLAUDE
    )
    assert result.rate_limited is True
    assert result.success is False
    # Rate limit is a non-zero exit in this fake, but success is false purely
    # because rate_limited is true, independent of exit_code.
    assert result.exit_code == 1


def test_is_rate_limited_case_insensitive() -> None:
    assert is_rate_limited("You hit your Usage Limit for today") is True
    assert is_rate_limited("all good, no issues here") is False
