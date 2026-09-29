"""Tests for factory.planner, driven against the fake `claude` binary."""

import dataclasses
import sys
from pathlib import Path

import pytest

from factory.config import load_config
from factory.db import connect, insert_idea, insert_project
from factory.planner import (
    PlanError,
    build_plan_prompt,
    parse_tasks_json,
    run_plan,
    write_tasks_md,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FAKE_CLAUDE = [sys.executable, str(Path(__file__).parent / "fixtures" / "fake_claude.py")]


def _config(**quality_overrides):
    cfg = load_config(REPO_ROOT / "config.yaml")
    if quality_overrides:
        new_quality = dataclasses.replace(cfg.quality, **quality_overrides)
        cfg = dataclasses.replace(cfg, quality=new_quality)
    return cfg


def _idea():
    return {"title": "Weather CLI", "category": "cli", "pitch": "Prints today's weather."}


def _project(conn):
    idea_id = insert_idea(
        conn, title="Weather CLI", category="cli", pitch="Prints today's weather.",
        created_at="2026-01-01",
    )
    project_id = insert_project(
        conn, idea_id=idea_id, repo_name="factory-weather-cli", local_path="/tmp/w",
        language="python", created_at="2026-01-01",
    )
    return project_id


def test_build_plan_prompt_substitutes_tokens() -> None:
    prompt = build_plan_prompt(REPO_ROOT / "prompts", _idea(), _config())
    assert "__PROJECT_TITLE__" not in prompt
    assert "Weather CLI" in prompt
    assert "Prints today's weather." in prompt
    assert str(_config().quality.min_tasks_per_project) in prompt


def test_parse_tasks_json_valid_within_range() -> None:
    raw = (
        '[{"title": "T", "description": "d", "acceptance": "a", "complexity": "simple"}]'
    )
    tasks = parse_tasks_json(raw, _config(min_tasks_per_project=1, max_tasks_per_project=5))
    assert len(tasks) == 1


def test_parse_tasks_json_too_few_raises() -> None:
    raw = '[{"title": "T", "description": "d", "acceptance": "a", "complexity": "simple"}]'
    with pytest.raises(PlanError, match="expected"):
        parse_tasks_json(raw, _config(min_tasks_per_project=5, max_tasks_per_project=10))


def test_parse_tasks_json_too_many_raises() -> None:
    raw = "[" + ",".join(
        '{"title": "T", "description": "d", "acceptance": "a", "complexity": "simple"}'
        for _ in range(3)
    ) + "]"
    with pytest.raises(PlanError, match="expected"):
        parse_tasks_json(raw, _config(min_tasks_per_project=1, max_tasks_per_project=2))


def test_parse_tasks_json_missing_field_raises() -> None:
    raw = '[{"title": "T", "description": "d"}]'
    with pytest.raises(PlanError, match="missing fields"):
        parse_tasks_json(raw, _config(min_tasks_per_project=1, max_tasks_per_project=5))


def test_parse_tasks_json_bad_complexity_raises() -> None:
    raw = '[{"title": "T", "description": "d", "acceptance": "a", "complexity": "medium"}]'
    with pytest.raises(PlanError, match="invalid complexity"):
        parse_tasks_json(raw, _config(min_tasks_per_project=1, max_tasks_per_project=5))


def test_parse_tasks_json_empty_acceptance_raises() -> None:
    raw = '[{"title": "T", "description": "d", "acceptance": "  ", "complexity": "simple"}]'
    with pytest.raises(PlanError, match="empty acceptance"):
        parse_tasks_json(raw, _config(min_tasks_per_project=1, max_tasks_per_project=5))


def test_write_tasks_md(tmp_path: Path) -> None:
    tasks = [
        {"title": "Add X", "description": "Do X.", "acceptance": "X works", "complexity": "simple"},
    ]
    write_tasks_md(tmp_path, tasks)
    content = (tmp_path / "TASKS.md").read_text(encoding="utf-8")
    assert "1. Add X (simple)" in content
    assert "Do X." in content
    assert "X works" in content


def test_run_plan_inserts_tasks_and_writes_tasks_md(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    project_id = _project(conn)
    idea = dict(_idea())
    idea["title"] = "PLAN_OK Weather CLI"  # marker for the fake binary
    ids = run_plan(
        conn,
        _config(min_tasks_per_project=1, max_tasks_per_project=5),
        project_id=project_id,
        local_path=str(tmp_path),
        idea=idea,
        claude_bin=FAKE_CLAUDE,
    )
    assert len(ids) == 2
    rows = conn.execute(
        "SELECT seq, title, complexity, status FROM tasks ORDER BY seq"
    ).fetchall()
    assert rows[0]["seq"] == 1
    assert rows[0]["title"] == "Add core function"
    assert rows[0]["complexity"] == "complex"
    assert rows[0]["status"] == "pending"
    assert rows[1]["title"] == "Add test for core function"
    assert (tmp_path / "TASKS.md").exists()


def test_run_plan_raises_and_writes_nothing_on_bad_json(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    project_id = _project(conn)
    idea = dict(_idea())
    idea["title"] = "PLAN_BAD_JSON Weather CLI"
    with pytest.raises(PlanError):
        run_plan(
            conn,
            _config(min_tasks_per_project=1, max_tasks_per_project=5),
            project_id=project_id,
            local_path=str(tmp_path),
            idea=idea,
            claude_bin=FAKE_CLAUDE,
        )
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    assert not (tmp_path / "TASKS.md").exists()


def test_run_plan_raises_on_missing_field(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    project_id = _project(conn)
    idea = dict(_idea())
    idea["title"] = "PLAN_MISSING_FIELD Weather CLI"
    with pytest.raises(PlanError, match="missing fields"):
        run_plan(
            conn,
            _config(min_tasks_per_project=1, max_tasks_per_project=5),
            project_id=project_id,
            local_path=str(tmp_path),
            idea=idea,
            claude_bin=FAKE_CLAUDE,
        )


def test_run_plan_raises_on_agent_failure(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    project_id = _project(conn)
    idea = dict(_idea())
    idea["title"] = "RATE_LIMIT Weather CLI"
    with pytest.raises(PlanError, match="rate limited"):
        run_plan(
            conn,
            _config(min_tasks_per_project=1, max_tasks_per_project=5),
            project_id=project_id,
            local_path=str(tmp_path),
            idea=idea,
            claude_bin=FAKE_CLAUDE,
        )
