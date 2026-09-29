"""Safety checks run before every stage transition and every agent session.

Every function here returns (allowed, reason) instead of raising, so callers
can log the reason and exit cleanly rather than crash.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from factory.config import Config
from factory.db import get_daily_counters

_DEFAULT_REPO_PREFIX = "factory-"


def stop_requested(stop_file: str | Path) -> bool:
    """True if the stop file exists. Caller should finish the current task and exit."""
    return Path(stop_file).exists()


def is_allowlisted_repo(repo_name: str, config: Config) -> bool:
    """A repo is touchable only if it matches the configured (or default) prefix."""
    prefix = config.github.repo_prefix or _DEFAULT_REPO_PREFIX
    return repo_name.startswith(prefix)


def check_daily_caps(conn: sqlite3.Connection, config: Config, day: str) -> tuple[bool, str | None]:
    """Check today's counters against config.limits. Returns (ok, reason)."""
    counters = get_daily_counters(conn, day)
    limits = config.limits
    checks = (
        ("commits", counters["commits"], limits.max_commits_per_day),
        ("sessions", counters["sessions"], limits.max_sessions_per_day),
        ("repos", counters["repos"], limits.max_repos_per_day),
    )
    for name, used, cap in checks:
        if used >= cap:
            return False, f"daily cap reached: {name} {used}/{cap}"
    if limits.max_cost_usd_per_day > 0 and counters["cost_usd"] >= limits.max_cost_usd_per_day:
        cost, cap = counters["cost_usd"], limits.max_cost_usd_per_day
        return False, f"daily cap reached: cost_usd {cost}/{cap}"
    return True, None


def count_consecutive_session_failures(conn: sqlite3.Connection) -> int:
    """Count finished sessions with a non-zero exit code, most recent first,
    stopping at the first success. In-progress sessions (ended_at IS NULL)
    are skipped rather than counted as failures."""
    rows = conn.execute(
        "SELECT exit_code FROM sessions WHERE ended_at IS NOT NULL ORDER BY id DESC"
    ).fetchall()
    streak = 0
    for row in rows:
        if row["exit_code"] == 0:
            break
        streak += 1
    return streak


def check_failure_streak(conn: sqlite3.Connection, config: Config) -> tuple[bool, str | None]:
    """Block further sessions once too many failures have happened in a row."""
    streak = count_consecutive_session_failures(conn)
    cap = config.limits.max_consecutive_failures
    if streak >= cap:
        return False, f"consecutive failure streak: {streak}/{cap}"
    return True, None


def check_all(
    conn: sqlite3.Connection, config: Config, day: str, repo_name: str | None = None
) -> tuple[bool, str | None]:
    """Run every guard in order; return the first failure, or (True, None)."""
    if stop_requested(config.paths.stop_file):
        return False, "stop file present"
    if repo_name is not None and not is_allowlisted_repo(repo_name, config):
        return False, f"repo not allowlisted: {repo_name!r}"
    ok, reason = check_daily_caps(conn, config, day)
    if not ok:
        return False, reason
    ok, reason = check_failure_streak(conn, config)
    if not ok:
        return False, reason
    return True, None
