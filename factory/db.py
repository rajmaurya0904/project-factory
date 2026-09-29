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


def insert_idea(
    conn: sqlite3.Connection,
    *,
    title: str,
    category: str,
    pitch: str,
    created_at: str,
    source: str | None = None,
    score: float | None = None,
    status: str = "new",
) -> int:
    """Insert a new idea row. Returns its id."""
    cur = conn.execute(
        "INSERT INTO ideas (title, category, pitch, source, score, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (title, category, pitch, source, score, status, created_at),
    )
    conn.commit()
    return cur.lastrowid


def update_idea_status(
    conn: sqlite3.Connection,
    idea_id: int,
    *,
    status: str,
    score: float | None = None,
    reject_reason: str | None = None,
) -> None:
    """Set an idea's status (and optionally score/reject_reason) after validation."""
    conn.execute(
        "UPDATE ideas SET status = ?, score = ?, reject_reason = ? WHERE id = ?",
        (status, score, reject_reason, idea_id),
    )
    conn.commit()


def insert_project(
    conn: sqlite3.Connection,
    *,
    idea_id: int,
    repo_name: str,
    local_path: str,
    language: str,
    created_at: str,
    repo_url: str | None = None,
    status: str = "scaffolded",
) -> int:
    """Insert a new project row. Returns its id."""
    cur = conn.execute(
        "INSERT INTO projects "
        "(idea_id, repo_name, repo_url, local_path, language, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (idea_id, repo_name, repo_url, local_path, language, status, created_at),
    )
    conn.commit()
    return cur.lastrowid


def update_project_status(conn: sqlite3.Connection, project_id: int, *, status: str) -> None:
    """Set a project's status (e.g. "shipped") once a pipeline stage completes."""
    conn.execute("UPDATE projects SET status = ? WHERE id = ?", (status, project_id))
    conn.commit()


def insert_task(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    seq: int,
    title: str,
    description: str,
    acceptance: str,
    complexity: str,
    updated_at: str,
    status: str = "pending",
) -> int:
    """Insert a new task row. Returns its id."""
    cur = conn.execute(
        "INSERT INTO tasks "
        "(project_id, seq, title, description, acceptance, complexity, status, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (project_id, seq, title, description, acceptance, complexity, status, updated_at),
    )
    conn.commit()
    return cur.lastrowid


def update_task_status(
    conn: sqlite3.Connection,
    task_id: int,
    *,
    status: str,
    updated_at: str,
    model_used: str | None = None,
    commit_sha: str | None = None,
    bump_attempts: bool = False,
) -> None:
    """Set a task's status (and optionally model_used/commit_sha) after a
    build attempt. `bump_attempts` increments `attempts` by one; existing
    model_used/commit_sha are kept when the new value is None."""
    conn.execute(
        "UPDATE tasks SET status = ?, "
        "model_used = COALESCE(?, model_used), "
        "commit_sha = COALESCE(?, commit_sha), "
        "attempts = attempts + ?, "
        "updated_at = ? WHERE id = ?",
        (status, model_used, commit_sha, 1 if bump_attempts else 0, updated_at, task_id),
    )
    conn.commit()


def insert_session(
    conn: sqlite3.Connection,
    *,
    project_id: int | None,
    task_id: int | None,
    stage: str,
    model: str | None,
    started_at: str,
    ended_at: str | None = None,
    exit_code: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: float | None = None,
    log_path: str | None = None,
) -> int:
    """Insert a new session row. Returns its id."""
    cur = conn.execute(
        "INSERT INTO sessions "
        "(project_id, task_id, stage, model, started_at, ended_at, exit_code, "
        "input_tokens, output_tokens, cost_usd, log_path) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            project_id, task_id, stage, model, started_at, ended_at, exit_code,
            input_tokens, output_tokens, cost_usd, log_path,
        ),
    )
    conn.commit()
    return cur.lastrowid


def update_session_log_path(conn: sqlite3.Connection, session_id: int, log_path: str) -> None:
    """Record the log file path for a session after it's been written."""
    conn.execute("UPDATE sessions SET log_path = ? WHERE id = ?", (log_path, session_id))
    conn.commit()


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
