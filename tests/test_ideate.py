"""Tests for factory.ideate, driven against the fake `claude` binary."""

import sys
from pathlib import Path

import pytest

from factory.config import load_config
from factory.db import connect
from factory.ideate import (
    IdeateError,
    build_ideate_prompt,
    parse_ideas_json,
    run_ideate,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FAKE_CLAUDE = [sys.executable, str(Path(__file__).parent / "fixtures" / "fake_claude.py")]


def _config():
    return load_config(REPO_ROOT / "config.yaml")


def test_build_ideate_prompt_loads_real_prompt_file() -> None:
    prompt = build_ideate_prompt(REPO_ROOT / "prompts", None)
    assert "JSON array" in prompt
    assert "est_scope_hours" in prompt


def test_build_ideate_prompt_without_pain_points(tmp_path: Path) -> None:
    (tmp_path / "ideate.md").write_text("BASE PROMPT")
    prompt = build_ideate_prompt(tmp_path, None)
    assert prompt == "BASE PROMPT"


def test_build_ideate_prompt_appends_pain_points_when_present(tmp_path: Path) -> None:
    (tmp_path / "ideate.md").write_text("BASE PROMPT")
    pains = tmp_path / "user_pain_points.md"
    pains.write_text("I hate doing X by hand every week.")
    prompt = build_ideate_prompt(tmp_path, pains)
    assert "BASE PROMPT" in prompt
    assert "I hate doing X by hand every week." in prompt


def test_build_ideate_prompt_skips_missing_pain_points_file(tmp_path: Path) -> None:
    (tmp_path / "ideate.md").write_text("BASE PROMPT")
    prompt = build_ideate_prompt(tmp_path, tmp_path / "does_not_exist.md")
    assert prompt == "BASE PROMPT"


def test_parse_ideas_json_valid_list() -> None:
    raw = (
        '[{"title": "T", "category": "cli", "pitch": "P", "source": "S", '
        '"est_scope_hours": 5}]'
    )
    ideas = parse_ideas_json(raw)
    assert ideas[0]["title"] == "T"


def test_parse_ideas_json_strips_markdown_fence() -> None:
    raw = (
        '```json\n[{"title": "T", "category": "cli", "pitch": "P", '
        '"source": "S", "est_scope_hours": 5}]\n```'
    )
    ideas = parse_ideas_json(raw)
    assert len(ideas) == 1


def test_parse_ideas_json_extracts_array_from_surrounding_prose() -> None:
    raw = (
        'Here are my ideas:\n[{"title": "T", "category": "cli", "pitch": "P", '
        '"source": "S", "est_scope_hours": 5}]\nHope that helps!'
    )
    ideas = parse_ideas_json(raw)
    assert len(ideas) == 1


def test_parse_ideas_json_none_raises() -> None:
    with pytest.raises(IdeateError, match="no result text"):
        parse_ideas_json(None)


def test_parse_ideas_json_not_json_raises() -> None:
    with pytest.raises(IdeateError, match="not valid JSON|could not find"):
        parse_ideas_json("this is not json and has no brackets")


def test_parse_ideas_json_not_a_list_raises() -> None:
    with pytest.raises(IdeateError, match="not a list"):
        parse_ideas_json('{"title": "T"}')


def test_parse_ideas_json_missing_field_raises() -> None:
    with pytest.raises(IdeateError, match="missing fields"):
        parse_ideas_json('[{"title": "T", "category": "cli"}]')


def test_parse_ideas_json_bad_category_raises() -> None:
    raw = (
        '[{"title": "T", "category": "nope", "pitch": "P", "source": "S", '
        '"est_scope_hours": 5}]'
    )
    with pytest.raises(IdeateError, match="invalid category"):
        parse_ideas_json(raw)


def test_parse_ideas_json_bad_scope_hours_raises() -> None:
    raw = (
        '[{"title": "T", "category": "cli", "pitch": "P", "source": "S", '
        '"est_scope_hours": -1}]'
    )
    with pytest.raises(IdeateError, match="invalid est_scope_hours"):
        parse_ideas_json(raw)


def test_parse_ideas_json_empty_title_raises() -> None:
    raw = (
        '[{"title": "  ", "category": "cli", "pitch": "P", "source": "S", '
        '"est_scope_hours": 5}]'
    )
    with pytest.raises(IdeateError, match="empty title or pitch"):
        parse_ideas_json(raw)


def test_run_ideate_with_marker_prompt_inserts_two_ideas(tmp_path: Path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "ideate.md").write_text("IDEATE_OK: produce the test ideas")
    conn = connect(tmp_path / "factory.db")
    ids = run_ideate(
        conn,
        _config(),
        workspace_dir=str(tmp_path),
        prompts_dir=prompts_dir,
        claude_bin=FAKE_CLAUDE,
    )
    assert len(ids) == 2
    rows = conn.execute("SELECT title, status FROM ideas ORDER BY id").fetchall()
    assert rows[0]["title"] == "CLI Weather Widget"
    assert rows[0]["status"] == "new"
    assert rows[1]["title"] == "Markdown Link Checker"


def test_run_ideate_raises_and_writes_nothing_on_bad_json(tmp_path: Path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "ideate.md").write_text("IDEATE_BAD_JSON: break it")
    conn = connect(tmp_path / "factory.db")
    with pytest.raises(IdeateError):
        run_ideate(
            conn,
            _config(),
            workspace_dir=str(tmp_path),
            prompts_dir=prompts_dir,
            claude_bin=FAKE_CLAUDE,
        )
    assert conn.execute("SELECT COUNT(*) FROM ideas").fetchone()[0] == 0


def test_run_ideate_raises_on_missing_field(tmp_path: Path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "ideate.md").write_text("IDEATE_MISSING_FIELD: break it")
    conn = connect(tmp_path / "factory.db")
    with pytest.raises(IdeateError, match="missing fields"):
        run_ideate(
            conn,
            _config(),
            workspace_dir=str(tmp_path),
            prompts_dir=prompts_dir,
            claude_bin=FAKE_CLAUDE,
        )


def test_run_ideate_raises_on_agent_failure(tmp_path: Path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "ideate.md").write_text("RATE_LIMIT please")
    conn = connect(tmp_path / "factory.db")
    with pytest.raises(IdeateError, match="rate limited"):
        run_ideate(
            conn,
            _config(),
            workspace_dir=str(tmp_path),
            prompts_dir=prompts_dir,
            claude_bin=FAKE_CLAUDE,
        )
