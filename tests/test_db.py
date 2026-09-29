"""Tests for factory.db: schema creation, idempotent migration, table shape."""

from pathlib import Path

from factory.db import connect, current_version, migrate

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
