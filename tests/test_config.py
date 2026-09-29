"""Tests for factory.config: happy path plus one failure per validation rule."""

from pathlib import Path

import pytest
import yaml

from factory.config import ConfigError, load_config

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_loads_real_config_yaml() -> None:
    cfg = load_config(REPO_ROOT / "config.yaml")
    assert cfg.agent.driver == "claude"
    assert cfg.agent.stage_models["ideate"] == "sonnet"
    assert cfg.github.visibility == "public"
    assert cfg.limits.max_commits_per_day == 80
    assert cfg.quality.min_tasks_per_project <= cfg.quality.max_tasks_per_project
    assert cfg.paths.workspace


def _write(tmp_path: Path, overrides: dict) -> Path:
    base = yaml.safe_load((REPO_ROOT / "config.yaml").read_text(encoding="utf-8"))
    for section, values in overrides.items():
        base[section].update(values) if isinstance(values, dict) else base.__setitem__(section, values)
    out = tmp_path / "config.yaml"
    out.write_text(yaml.safe_dump(base), encoding="utf-8")
    return out


def test_missing_top_level_key_raises(tmp_path: Path) -> None:
    base = yaml.safe_load((REPO_ROOT / "config.yaml").read_text(encoding="utf-8"))
    del base["limits"]
    out = tmp_path / "config.yaml"
    out.write_text(yaml.safe_dump(base), encoding="utf-8")
    with pytest.raises(ConfigError, match="limits"):
        load_config(out)


def test_invalid_driver_raises(tmp_path: Path) -> None:
    out = _write(tmp_path, {"agent": {"driver": "not-a-driver"}})
    with pytest.raises(ConfigError, match="driver"):
        load_config(out)


def test_invalid_stage_model_alias_raises(tmp_path: Path) -> None:
    out = _write(tmp_path, {"agent": {"stage_models": {"ideate": "opus"}}})
    with pytest.raises(ConfigError, match="stage_models"):
        load_config(out)


def test_invalid_visibility_raises(tmp_path: Path) -> None:
    out = _write(tmp_path, {"github": {"visibility": "hidden"}})
    with pytest.raises(ConfigError, match="visibility"):
        load_config(out)


def test_negative_limit_raises(tmp_path: Path) -> None:
    out = _write(tmp_path, {"limits": {"max_commits_per_day": -1}})
    with pytest.raises(ConfigError, match="max_commits_per_day"):
        load_config(out)


def test_min_tasks_over_max_raises(tmp_path: Path) -> None:
    out = _write(tmp_path, {"quality": {"min_tasks_per_project": 999}})
    with pytest.raises(ConfigError, match="min_tasks_per_project"):
        load_config(out)


def test_unknown_require_entry_raises(tmp_path: Path) -> None:
    out = _write(tmp_path, {"quality": {"require": ["readme", "not-a-thing"]}})
    with pytest.raises(ConfigError, match="require"):
        load_config(out)


def test_empty_workspace_raises(tmp_path: Path) -> None:
    out = _write(tmp_path, {"paths": {"workspace": ""}})
    with pytest.raises(ConfigError, match="workspace"):
        load_config(out)
