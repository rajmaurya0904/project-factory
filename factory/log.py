"""Logging: a rotating text log for the runner, plus one JSON file per session."""

from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOGGER_NAME = "factory"


def get_logger(state_dir: str | Path) -> logging.Logger:
    """Return the shared runner logger, writing to state/logs/factory.log (rotating)."""
    logger = logging.getLogger(_LOGGER_NAME)
    if logger.handlers:
        return logger  # already configured

    log_dir = Path(state_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "factory.log", maxBytes=10 * 1024 * 1024, backupCount=5
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    logger.addHandler(stream_handler)

    return logger


def session_log_path(state_dir: str | Path, session_id: int, when: datetime | None = None) -> Path:
    """Path for one session's JSON log: state/logs/<date>/<session_id>.json."""
    when = when or datetime.now(timezone.utc)
    day_dir = Path(state_dir) / "logs" / when.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    return day_dir / f"{session_id}.json"


def write_session_log(state_dir: str | Path, session_id: int, data: dict[str, Any]) -> Path:
    """Write one session's structured record as JSON. Returns the path written."""
    path = session_log_path(state_dir, session_id)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return path
