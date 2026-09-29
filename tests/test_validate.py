"""Tests for factory.validate, driven against fake `gh` and `claude` binaries."""

import sys
from pathlib import Path

import pytest

from factory.config import load_config
from factory.db import connect, insert_idea
from factory.validate import (
    ValidateError,
    build_validate_prompt,
    find_blocking_competitor,
    parse_score_json,
    search_existing_repos,
    validate_idea,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"
FAKE_CLAUDE = [sys.executable, str(FIXTURES / "fake_claude.py")]
FAKE_GH = [sys.executable, str(FIXTURES / "fake_gh.py")]


def _config():
    return load_config(REPO_ROOT / "config.yaml")


def _idea(conn, title, category="cli", pitch="A pitch."):
    idea_id = insert_idea(
        conn, title=title, category=category, pitch=pitch, created_at="2026-01-01"
    )
    return {"id": idea_id, "title": title, "category": category, "pitch": pitch}


def test_search_existing_repos_parses_results() -> None:
    repos = search_existing_repos("HAS_COMPETITOR widget", gh_bin=FAKE_GH)
    assert repos[0]["name"] == "big-existing-tool"


def test_search_existing_repos_empty_results() -> None:
    assert search_existing_repos("nothing special", gh_bin=FAKE_GH) == []


def test_search_existing_repos_gh_failure_raises() -> None:
    with pytest.raises(ValidateError, match="gh search repos failed"):
        search_existing_repos("GH_FAIL please", gh_bin=FAKE_GH)


def test_search_existing_repos_bad_json_raises() -> None:
    with pytest.raises(ValidateError, match="bad JSON"):
        search_existing_repos("GH_BAD_JSON please", gh_bin=FAKE_GH)


def test_find_blocking_competitor_recent_and_popular() -> None:
    repos = search_existing_repos("HAS_COMPETITOR widget", gh_bin=FAKE_GH)
    competitor = find_blocking_competitor(repos, _config())
    assert competitor is not None
    assert competitor["name"] == "big-existing-tool"


def test_find_blocking_competitor_stale_repo_does_not_block() -> None:
    repos = search_existing_repos("STALE_COMPETITOR widget", gh_bin=FAKE_GH)
    competitor = find_blocking_competitor(repos, _config())
    assert competitor is None  # pushed 800 days ago -> outside the lookback window


def test_find_blocking_competitor_no_results() -> None:
    assert find_blocking_competitor([], _config()) is None


def test_build_validate_prompt_substitutes_tokens_and_search_results() -> None:
    idea = {"title": "T", "category": "cli", "pitch": "P"}
    repos = [{"name": "x", "stargazersCount": 5, "pushedAt": "2026-01-01T00:00:00Z"}]
    prompt = build_validate_prompt(REPO_ROOT / "prompts", idea, repos)
    assert "__IDEA_TITLE__" not in prompt
    assert "T" in prompt and "P" in prompt
    assert "x: 5 stars" in prompt


def test_build_validate_prompt_no_results_placeholder() -> None:
    idea = {"title": "T", "category": "cli", "pitch": "P"}
    prompt = build_validate_prompt(REPO_ROOT / "prompts", idea, [])
    assert "(no results)" in prompt


def test_parse_score_json_valid() -> None:
    parsed = parse_score_json('{"score": 8, "rationale": "Good idea."}')
    assert parsed == {"score": 8.0, "rationale": "Good idea."}


def test_parse_score_json_missing_fields_raises() -> None:
    with pytest.raises(ValidateError, match="missing fields"):
        parse_score_json('{"score": 8}')


def test_parse_score_json_out_of_range_raises() -> None:
    with pytest.raises(ValidateError, match="0-10"):
        parse_score_json('{"score": 15, "rationale": "r"}')


def test_parse_score_json_empty_rationale_raises() -> None:
    with pytest.raises(ValidateError, match="empty rationale"):
        parse_score_json('{"score": 8, "rationale": "  "}')


def test_validate_idea_blocked_by_competitor_rejects_without_agent_call(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    idea = _idea(conn, title="HAS_COMPETITOR widget")
    # claude_bin points at a binary that always fails -- proves it was never invoked.
    outcome = validate_idea(
        conn, _config(), idea, workspace_dir=str(tmp_path), gh_bin=FAKE_GH,
        claude_bin=["/nonexistent/should-not-be-called"],
    )
    assert outcome["status"] == "rejected"
    assert "big-existing-tool" in outcome["reject_reason"]
    row = conn.execute("SELECT status, score FROM ideas WHERE id = ?", (idea["id"],)).fetchone()
    assert row["status"] == "rejected"
    assert row["score"] is None


def test_validate_idea_high_score_is_approved(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    idea = _idea(conn, title="VALIDATE_HIGH_SCORE widget")
    outcome = validate_idea(
        conn, _config(), idea, workspace_dir=str(tmp_path), gh_bin=FAKE_GH, claude_bin=FAKE_CLAUDE
    )
    assert outcome["status"] == "approved"
    assert outcome["score"] == 9.0
    row = conn.execute("SELECT status, score FROM ideas WHERE id = ?", (idea["id"],)).fetchone()
    assert row["status"] == "approved"
    assert row["score"] == 9.0


def test_validate_idea_low_score_is_rejected(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    idea = _idea(conn, title="VALIDATE_LOW_SCORE widget")
    outcome = validate_idea(
        conn, _config(), idea, workspace_dir=str(tmp_path), gh_bin=FAKE_GH, claude_bin=FAKE_CLAUDE
    )
    assert outcome["status"] == "rejected"
    assert outcome["score"] == 2.0
    assert outcome["reject_reason"] == "Too vague to be useful on its own."


def test_validate_idea_bad_score_json_raises_and_writes_nothing(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    idea = _idea(conn, title="VALIDATE_BAD_JSON widget")
    with pytest.raises(ValidateError):
        validate_idea(
            conn, _config(), idea, workspace_dir=str(tmp_path),
            gh_bin=FAKE_GH, claude_bin=FAKE_CLAUDE,
        )
    row = conn.execute("SELECT status FROM ideas WHERE id = ?", (idea["id"],)).fetchone()
    assert row["status"] == "new"  # untouched


def test_validate_idea_agent_failure_raises(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    idea = _idea(conn, title="RATE_LIMIT widget")
    with pytest.raises(ValidateError, match="rate limited"):
        validate_idea(
            conn, _config(), idea, workspace_dir=str(tmp_path),
            gh_bin=FAKE_GH, claude_bin=FAKE_CLAUDE,
        )
