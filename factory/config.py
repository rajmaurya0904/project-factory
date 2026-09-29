"""Loads and validates config.yaml into a typed Config object."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_VALID_DRIVERS = {"claude", "codex"}
_VALID_MODEL_ALIASES = {"haiku", "sonnet"}
_VALID_VISIBILITY = {"public", "private"}
_VALID_REQUIRE = {"readme", "license", "tests", "ci"}
_STAGE_MODEL_KEYS = {
    "ideate",
    "validate",
    "scaffold",
    "plan",
    "build_simple",
    "build_complex",
    "fix",
    "commit_message",
    "release",
}


class ConfigError(ValueError):
    """Raised when config.yaml is missing keys or has invalid values."""


@dataclass(frozen=True)
class AgentConfig:
    driver: str
    models: dict[str, str]
    stage_models: dict[str, str]
    escalate_on_failure: bool
    max_turns_per_task: int
    task_timeout_sec: int


@dataclass(frozen=True)
class GithubConfig:
    owner: str
    repo_prefix: str
    visibility: str
    license: str


@dataclass(frozen=True)
class LimitsConfig:
    max_commits_per_day: int
    max_sessions_per_day: int
    max_repos_per_day: int
    max_cost_usd_per_day: float
    max_consecutive_failures: int
    min_seconds_between_sessions: int


@dataclass(frozen=True)
class QualityConfig:
    reject_if_existing_repo_stars_over: int
    min_tasks_per_project: int
    max_tasks_per_project: int
    require: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PathsConfig:
    workspace: str
    stop_file: str


@dataclass(frozen=True)
class Config:
    agent: AgentConfig
    github: GithubConfig
    limits: LimitsConfig
    quality: QualityConfig
    paths: PathsConfig


def _require_keys(section: dict[str, Any], keys: set[str], section_name: str) -> None:
    missing = keys - section.keys()
    if missing:
        raise ConfigError(f"config.{section_name} is missing keys: {sorted(missing)}")


def _build_agent(raw: dict[str, Any]) -> AgentConfig:
    _require_keys(
        raw,
        {
            "driver",
            "models",
            "stage_models",
            "escalate_on_failure",
            "max_turns_per_task",
            "task_timeout_sec",
        },
        "agent",
    )
    if raw["driver"] not in _VALID_DRIVERS:
        raise ConfigError(
            f"config.agent.driver must be one of {_VALID_DRIVERS}, got {raw['driver']!r}"
        )
    stage_models = raw["stage_models"]
    missing_stages = _STAGE_MODEL_KEYS - stage_models.keys()
    if missing_stages:
        raise ConfigError(
            f"config.agent.stage_models is missing stages: {sorted(missing_stages)}"
        )
    for stage, alias in stage_models.items():
        if alias not in _VALID_MODEL_ALIASES:
            raise ConfigError(
                f"config.agent.stage_models.{stage} must be one of "
                f"{_VALID_MODEL_ALIASES}, got {alias!r}"
            )
    if raw["max_turns_per_task"] <= 0:
        raise ConfigError("config.agent.max_turns_per_task must be positive")
    if raw["task_timeout_sec"] <= 0:
        raise ConfigError("config.agent.task_timeout_sec must be positive")
    return AgentConfig(
        driver=raw["driver"],
        models=dict(raw["models"]),
        stage_models=dict(stage_models),
        escalate_on_failure=bool(raw["escalate_on_failure"]),
        max_turns_per_task=int(raw["max_turns_per_task"]),
        task_timeout_sec=int(raw["task_timeout_sec"]),
    )


def _build_github(raw: dict[str, Any]) -> GithubConfig:
    _require_keys(raw, {"owner", "repo_prefix", "visibility", "license"}, "github")
    if not raw["owner"]:
        raise ConfigError("config.github.owner must not be empty")
    if raw["visibility"] not in _VALID_VISIBILITY:
        raise ConfigError(
            f"config.github.visibility must be one of "
            f"{_VALID_VISIBILITY}, got {raw['visibility']!r}"
        )
    return GithubConfig(
        owner=raw["owner"],
        repo_prefix=raw["repo_prefix"] or "",
        visibility=raw["visibility"],
        license=raw["license"],
    )


def _build_limits(raw: dict[str, Any]) -> LimitsConfig:
    _require_keys(
        raw,
        {
            "max_commits_per_day",
            "max_sessions_per_day",
            "max_repos_per_day",
            "max_cost_usd_per_day",
            "max_consecutive_failures",
            "min_seconds_between_sessions",
        },
        "limits",
    )
    for key in (
        "max_commits_per_day",
        "max_sessions_per_day",
        "max_repos_per_day",
        "max_consecutive_failures",
        "min_seconds_between_sessions",
    ):
        if raw[key] < 0:
            raise ConfigError(f"config.limits.{key} must not be negative")
    if raw["max_cost_usd_per_day"] < 0:
        raise ConfigError("config.limits.max_cost_usd_per_day must not be negative")
    return LimitsConfig(
        max_commits_per_day=int(raw["max_commits_per_day"]),
        max_sessions_per_day=int(raw["max_sessions_per_day"]),
        max_repos_per_day=int(raw["max_repos_per_day"]),
        max_cost_usd_per_day=float(raw["max_cost_usd_per_day"]),
        max_consecutive_failures=int(raw["max_consecutive_failures"]),
        min_seconds_between_sessions=int(raw["min_seconds_between_sessions"]),
    )


def _build_quality(raw: dict[str, Any]) -> QualityConfig:
    _require_keys(
        raw,
        {
            "reject_if_existing_repo_stars_over",
            "min_tasks_per_project",
            "max_tasks_per_project",
            "require",
        },
        "quality",
    )
    if raw["min_tasks_per_project"] > raw["max_tasks_per_project"]:
        raise ConfigError("config.quality.min_tasks_per_project must be <= max_tasks_per_project")
    invalid_require = set(raw["require"]) - _VALID_REQUIRE
    if invalid_require:
        raise ConfigError(f"config.quality.require has unknown entries: {sorted(invalid_require)}")
    return QualityConfig(
        reject_if_existing_repo_stars_over=int(raw["reject_if_existing_repo_stars_over"]),
        min_tasks_per_project=int(raw["min_tasks_per_project"]),
        max_tasks_per_project=int(raw["max_tasks_per_project"]),
        require=list(raw["require"]),
    )


def _build_paths(raw: dict[str, Any]) -> PathsConfig:
    _require_keys(raw, {"workspace", "stop_file"}, "paths")
    if not raw["workspace"]:
        raise ConfigError("config.paths.workspace must not be empty")
    return PathsConfig(workspace=raw["workspace"], stop_file=raw["stop_file"])


def load_config(path: str | Path) -> Config:
    """Read config.yaml from `path`, validate it, and return a typed Config."""
    text = Path(path).read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} did not parse to a mapping")
    _require_keys(raw, {"agent", "github", "limits", "quality", "paths"}, "<root>")
    return Config(
        agent=_build_agent(raw["agent"]),
        github=_build_github(raw["github"]),
        limits=_build_limits(raw["limits"]),
        quality=_build_quality(raw["quality"]),
        paths=_build_paths(raw["paths"]),
    )
