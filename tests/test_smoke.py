"""Smoke test: package imports cleanly. Replace/extend as modules land."""

import factory


def test_version_is_set() -> None:
    assert factory.__version__
