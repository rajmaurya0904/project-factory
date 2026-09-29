"""SQLite access: schema creation, migrations, and a thin connection helper.

Migrations are a plain ordered list of SQL scripts applied once each, tracked
in `schema_migrations`. This is deliberately not a full migration framework:
one project, one schema, applied in-process at startup.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# Each entry is (version, sql). Append-only: never edit or remove a past entry,
# add a new one instead, even for a one-line fix.
_MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE ideas (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            pitch TEXT NOT NULL,
            source TEXT,
            score REAL,
            status TEXT NOT NULL,
            reject_reason TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE projects (
            id INTEGER PRIMARY KEY,
            idea_id INTEGER REFERENCES ideas(id),
            repo_name TEXT UNIQUE NOT NULL,
            repo_url TEXT,
            local_path TEXT,
            language TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY,
            project_id INTEGER REFERENCES projects(id),
            seq INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            acceptance TEXT NOT NULL,
            complexity TEXT NOT NULL,
            model_used TEXT,
            status TEXT NOT NULL,
            attempts INTEGER DEFAULT 0,
            commit_sha TEXT,
            updated_at TEXT
        );

        CREATE TABLE sessions (
            id INTEGER PRIMARY KEY,
            project_id INTEGER,
            task_id INTEGER,
            stage TEXT NOT NULL,
            model TEXT,
            started_at TEXT,
            ended_at TEXT,
            exit_code INTEGER,
            input_tokens INTEGER,
            output_tokens INTEGER,
            cost_usd REAL,
            log_path TEXT
        );

        CREATE TABLE daily_counters (
            day TEXT PRIMARY KEY,
            commits INTEGER DEFAULT 0,
            sessions INTEGER DEFAULT 0,
            repos INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0
        );
        """,
    ),
]


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open (creating parent dirs if needed) and migrate the database."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Apply any migrations not yet recorded in schema_migrations."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)"
    )
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    for version, sql in _MIGRATIONS:
        if version in applied:
            continue
        conn.executescript(sql)
        conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
    conn.commit()


def current_version(conn: sqlite3.Connection) -> int:
    """Highest applied migration version, or 0 if none applied yet."""
    row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return row[0] or 0


def get_daily_counters(conn: sqlite3.Connection, day: str) -> sqlite3.Row:
    """Return the counters row for `day` (YYYY-MM-DD), zeroed if it doesn't exist yet."""
    row = conn.execute("SELECT * FROM daily_counters WHERE day = ?", (day,)).fetchone()
    if row is not None:
        return row
    conn.execute(
        "INSERT INTO daily_counters (day, commits, sessions, repos, cost_usd) "
        "VALUES (?, 0, 0, 0, 0)",
        (day,),
    )
    conn.commit()
    return conn.execute("SELECT * FROM daily_counters WHERE day = ?", (day,)).fetchone()


def increment_daily_counters(
    conn: sqlite3.Connection,
    day: str,
    *,
    commits: int = 0,
    sessions: int = 0,
    repos: int = 0,
    cost_usd: float = 0.0,
) -> None:
    """Add to today's counters, creating the row first if needed."""
    get_daily_counters(conn, day)
    conn.execute(
        "UPDATE daily_counters SET commits = commits + ?, sessions = sessions + ?, "
        "repos = repos + ?, cost_usd = cost_usd + ? WHERE day = ?",
        (commits, sessions, repos, cost_usd, day),
    )
    conn.commit()
