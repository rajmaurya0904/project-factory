"""Rate-limited build sessions: task returns to pending, guard ignores them."""
import sqlite3

from factory import builder, guard
from factory.agent import is_rate_limited


def test_session_limit_message_detected():
    assert is_rate_limited("You've hit your session limit \u00b7 resets 3:50pm (UTC)")
    assert not is_rate_limited("all tests passed")


def test_guard_skips_rate_limited_sessions():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY, exit_code INT, ended_at TEXT)")
    conn.execute("INSERT INTO sessions (exit_code, ended_at) VALUES (0,'t'),(1,'t'),"
        "(75,'t'),(75,'t')")
    assert guard.count_consecutive_session_failures(conn) == 1


def test_rate_limited_constant():
    assert builder.RATE_LIMITED_EXIT_CODE == 75
