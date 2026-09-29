"""Tests for factory.db: schema creation, idempotent migration, table shape."""

import sqlite3
from pathlib import Path

import pytest

from factory.db import connect, current_version, insert_idea, insert_project, migrate

EXPECTED_TABLES = {
    "ideas",
    "projects",
    "tasks",
    "sessions",
    "daily_counters",
    "schema_migrations",
}


def _tables(conn) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r[0] for r in rows}


def test_connect_creates_parent_dir_and_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "factory.db"
    conn = connect(db_path)
    assert db_path.exists()
    assert EXPECTED_TABLES <= _tables(conn)
    assert current_version(conn) == 1
    conn.close()


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "factory.db"
    conn = connect(db_path)
    version_before = current_version(conn)
    migrate(conn)  # re-run: must not error or duplicate tables
    assert current_version(conn) == version_before
    conn.close()


def test_reopening_existing_db_does_not_lose_data(tmp_path: Path) -> None:
    db_path = tmp_path / "factory.db"
    conn = connect(db_path)
    conn.execute(
        "INSERT INTO ideas (title, category, pitch, status, created_at) "
        "VALUES ('t', 'cli', 'p', 'new', '2026-01-01')"
    )
    conn.commit()
    conn.close()

    conn2 = connect(db_path)
    rows = conn2.execute("SELECT title FROM ideas").fetchall()
    assert [r[0] for r in rows] == ["t"]
    conn2.close()


def test_foreign_keys_pragma_enabled(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    row = conn.execute("PRAGMA foreign_keys").fetchone()
    assert row[0] == 1
    conn.close()


def test_insert_idea_returns_id(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    idea_id = insert_idea(
        conn, title="t", category="cli", pitch="p", created_at="2026-01-01"
    )
    assert idea_id == 1
    row = conn.execute("SELECT status FROM ideas WHERE id = ?", (idea_id,)).fetchone()
    assert row["status"] == "new"
    conn.close()


def test_insert_project_requires_existing_idea(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    idea_id = insert_idea(
        conn, title="t", category="cli", pitch="p", created_at="2026-01-01"
    )
    project_id = insert_project(
        conn,
        idea_id=idea_id,
        repo_name="factory-widget",
        local_path="/tmp/factory-widget",
        language="python",
        created_at="2026-01-01",
    )
    row = conn.execute("SELECT status FROM projects WHERE id = ?", (project_id,)).fetchone()
    assert row["status"] == "scaffolded"
    conn.close()


def test_insert_project_with_bad_idea_id_raises(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    with pytest.raises(sqlite3.IntegrityError):
        insert_project(
            conn,
            idea_id=999,
            repo_name="factory-widget",
            local_path="/tmp/factory-widget",
            language="python",
            created_at="2026-01-01",
        )
    conn.close()
