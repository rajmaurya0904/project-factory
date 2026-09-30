"""Tests for factory.guard: stop file, allowlist, daily caps, failure streak."""

import dataclasses
from datetime import UTC, datetime, timedelta
from pathlib import Path

from factory.config import load_config
from factory.db import connect, increment_daily_counters
from factory.guard import (
    check_all,
    check_daily_caps,
    check_failure_streak,
    count_consecutive_session_failures,
    is_allowlisted_repo,
    stop_requested,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _config(tmp_path: Path, **limit_overrides):
    cfg = load_config(REPO_ROOT / "config.yaml")
    stop_path = str(tmp_path / "STOP")
    cfg = dataclasses.replace(cfg, paths=dataclasses.replace(cfg.paths, stop_file=stop_path))
    if limit_overrides:
        cfg = dataclasses.replace(cfg, limits=dataclasses.replace(cfg.limits, **limit_overrides))
    return cfg


def test_stop_requested_false_when_absent(tmp_path: Path) -> None:
    assert stop_requested(tmp_path / "STOP") is False


def test_stop_requested_true_when_present(tmp_path: Path) -> None:
    stop = tmp_path / "STOP"
    stop.write_text("")
    assert stop_requested(stop) is True


def test_is_allowlisted_repo_default_prefix(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg = dataclasses.replace(cfg, github=dataclasses.replace(cfg.github, repo_prefix=""))
    assert is_allowlisted_repo("factory-widget", cfg) is True
    assert is_allowlisted_repo("my-personal-repo", cfg) is False


def test_is_allowlisted_repo_custom_prefix(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg = dataclasses.replace(cfg, github=dataclasses.replace(cfg.github, repo_prefix="fx-"))
    assert is_allowlisted_repo("fx-widget", cfg) is True
    assert is_allowlisted_repo("factory-widget", cfg) is False


def test_check_daily_caps_ok_when_under_limit(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    cfg = _config(tmp_path, max_commits_per_day=10)
    ok, reason = check_daily_caps(conn, cfg, "2026-01-01")
    assert (ok, reason) == (True, None)
    conn.close()


def test_check_daily_caps_blocks_at_limit(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    cfg = _config(tmp_path, max_commits_per_day=3)
    increment_daily_counters(conn, "2026-01-01", commits=3)
    ok, reason = check_daily_caps(conn, cfg, "2026-01-01")
    assert ok is False
    assert "commits" in reason
    conn.close()


def test_count_consecutive_session_failures(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    conn.executemany(
        "INSERT INTO sessions (stage, exit_code, ended_at) VALUES (?, ?, ?)",
        [("build", 0, "t1"), ("build", 1, "t2"), ("build", 1, "t3")],
    )
    conn.commit()
    # Oldest succeeded, the two most recent failed -> streak of 2.
    assert count_consecutive_session_failures(conn) == 2
    conn.close()


def test_check_failure_streak_blocks_when_exceeded(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    cfg = _config(tmp_path, max_consecutive_failures=2)
    conn.executemany(
        "INSERT INTO sessions (stage, exit_code, ended_at) VALUES (?, ?, ?)",
        [("build", 1, "t1"), ("build", 1, "t2")],
    )
    conn.commit()
    ok, reason = check_failure_streak(conn, cfg)
    assert ok is False
    assert "streak" in reason
    conn.close()


def test_count_consecutive_session_failures_ignores_rate_limited(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    conn.executemany(
        "INSERT INTO sessions (stage, exit_code, ended_at) VALUES (?, ?, ?)",
        [("build", 0, "t1"), ("build", 75, "t2"), ("build", 1, "t3")],
    )
    conn.commit()
    # Oldest succeeded, a rate limit in between doesn't count -> streak of 1.
    assert count_consecutive_session_failures(conn) == 1
    conn.close()


def test_count_consecutive_session_failures_expires_after_cooldown(tmp_path: Path) -> None:
    """A failure streak old enough to be stale stops blocking on its own --
    otherwise hitting the cap is a permanent deadlock: the block itself
    prevents any new session from running, so nothing could ever clear it."""
    conn = connect(tmp_path / "factory.db")
    stale = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    conn.executemany(
        "INSERT INTO sessions (stage, exit_code, ended_at) VALUES (?, ?, ?)",
        [("build", 1, stale), ("build", 1, stale)],
    )
    conn.commit()
    assert count_consecutive_session_failures(conn) == 0
    conn.close()


def test_check_all_stop_file_wins_first(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    cfg = _config(tmp_path)
    Path(cfg.paths.stop_file).write_text("")
    ok, reason = check_all(conn, cfg, "2026-01-01", repo_name="not-allowlisted")
    assert ok is False
    assert reason == "stop file present"
    conn.close()


def test_check_all_passes_when_clean(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    cfg = _config(tmp_path)
    ok, reason = check_all(conn, cfg, "2026-01-01", repo_name="factory-widget")
    assert (ok, reason) == (True, None)
    conn.close()
