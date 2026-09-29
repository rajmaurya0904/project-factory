"""Maps a pipeline stage or task complexity to a concrete model name, and
decides when a failing Haiku task should escalate to Sonnet."""

from __future__ import annotations

from factory.config import AgentConfig

_COMPLEXITY_TO_STAGE = {"simple": "build_simple", "complex": "build_complex"}


def model_for_stage(agent_cfg: AgentConfig, stage: str) -> str:
    """Resolve a pipeline stage (e.g. "ideate", "plan") to a real model name."""
    if stage not in agent_cfg.stage_models:
        raise ValueError(f"unknown stage: {stage!r}")
    alias = agent_cfg.stage_models[stage]
    return agent_cfg.models[alias]


def alias_for_task(agent_cfg: AgentConfig, complexity: str) -> str:
    """Resolve a task's complexity to the model *alias* ("haiku"/"sonnet")
    configured for it, without resolving to a real model name."""
    if complexity not in _COMPLEXITY_TO_STAGE:
        raise ValueError(f"unknown task complexity: {complexity!r}")
    return agent_cfg.stage_models[_COMPLEXITY_TO_STAGE[complexity]]


def model_for_task(agent_cfg: AgentConfig, complexity: str) -> str:
    """Resolve a task's complexity ("simple"/"complex") to a real model name."""
    alias = alias_for_task(agent_cfg, complexity)
    return agent_cfg.models[alias]


def should_escalate(agent_cfg: AgentConfig, alias_used: str, gate_failed_twice: bool) -> bool:
    """True if a Haiku task that failed the gate twice should get one Sonnet retry."""
    return agent_cfg.escalate_on_failure and alias_used == "haiku" and gate_failed_twice


def escalated_model(agent_cfg: AgentConfig) -> str:
    """The model to retry with when should_escalate() is true."""
    return agent_cfg.models["sonnet"]
