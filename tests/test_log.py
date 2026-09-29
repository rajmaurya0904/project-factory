"""Tests for factory.log: rotating logger setup and per-session JSON logs."""

import json
import logging
from pathlib import Path

from factory.log import get_logger, session_log_path, write_session_log


def test_get_logger_creates_log_dir_and_file(tmp_path: Path) -> None:
    logger = get_logger(tmp_path)
    logger.info("hello")
    for handler in logger.handlers:
        handler.flush()
    assert (tmp_path / "logs" / "factory.log").exists()
    logging.getLogger("factory").handlers.clear()  # reset for other tests


def test_session_log_path_is_grouped_by_day(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    when = datetime(2026, 1, 2, tzinfo=UTC)
    path = session_log_path(tmp_path, 42, when=when)
    assert path == tmp_path / "logs" / "2026-01-02" / "42.json"


def test_write_session_log_round_trips_json(tmp_path: Path) -> None:
    path = write_session_log(tmp_path, 7, {"stage": "build", "exit_code": 0})
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"stage": "build", "exit_code": 0}
