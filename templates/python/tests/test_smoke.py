"""Smoke test: package imports cleanly. Replace/extend as modules land."""

import __PROJECT_PKG__


def test_version_is_set() -> None:
    assert __PROJECT_PKG__.__version__
