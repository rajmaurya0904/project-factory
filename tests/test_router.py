"""Tests for factory.router: stage/complexity -> model, and escalation rules."""

from pathlib import Path

import pytest

from factory.config import load_config
from factory.router import (
    alias_for_task,
    escalated_model,
    model_for_stage,
    model_for_task,
    should_escalate,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _agent_cfg():
    return load_config(REPO_ROOT / "config.yaml").agent


def test_model_for_stage_resolves_alias_to_real_model() -> None:
    cfg = _agent_cfg()
    assert model_for_stage(cfg, "ideate") == "sonnet"
    assert model_for_stage(cfg, "validate") == "haiku"


def test_model_for_stage_unknown_stage_raises() -> None:
    with pytest.raises(ValueError, match="unknown stage"):
        model_for_stage(_agent_cfg(), "not-a-stage")


def test_alias_for_task_simple_vs_complex() -> None:
    cfg = _agent_cfg()
    assert alias_for_task(cfg, "simple") == "haiku"
    assert alias_for_task(cfg, "complex") == "sonnet"


def test_model_for_task_resolves_real_model() -> None:
    cfg = _agent_cfg()
    assert model_for_task(cfg, "simple") == "haiku"
    assert model_for_task(cfg, "complex") == "sonnet"


def test_model_for_task_unknown_complexity_raises() -> None:
    with pytest.raises(ValueError, match="unknown task complexity"):
        model_for_task(_agent_cfg(), "medium")


def test_should_escalate_true_only_for_haiku_after_two_failures() -> None:
    cfg = _agent_cfg()
    assert should_escalate(cfg, "haiku", gate_failed_twice=True) is True
    assert should_escalate(cfg, "haiku", gate_failed_twice=False) is False
    assert should_escalate(cfg, "sonnet", gate_failed_twice=True) is False


def test_should_escalate_respects_config_flag() -> None:
    import dataclasses

    cfg = _agent_cfg()
    disabled = dataclasses.replace(cfg, escalate_on_failure=False)
    assert should_escalate(disabled, "haiku", gate_failed_twice=True) is False


def test_escalated_model_is_sonnet() -> None:
    assert escalated_model(_agent_cfg()) == "sonnet"
