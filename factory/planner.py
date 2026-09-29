"""Stage 4: plan. One session turns an approved idea into an ordered list of
10-20 atomic tasks, each with a testable acceptance criterion and a
complexity label that drives which model builds it later.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from factory.agent import AgentResult, run_agent
from factory.config import Config
from factory.db import insert_task
from factory.jsonutil import JsonExtractError, extract_json
from factory.router import model_for_stage

DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_VALID_COMPLEXITY = {"simple", "complex"}
_REQUIRED_TASK_FIELDS = {"title", "description", "acceptance", "complexity"}
_PLAN_ALLOWED_TOOLS = ""


class PlanError(RuntimeError):
    """Raised when the plan session fails or its task list can't be trusted."""


def build_plan_prompt(
    prompts_dir: str | Path, idea: dict, config: Config
) -> str:
    base = (Path(prompts_dir) / "plan.md").read_text(encoding="utf-8")
    tokens = {
        "__PROJECT_TITLE__": idea["title"],
        "__PROJECT_CATEGORY__": idea["category"],
        "__PROJECT_PITCH__": idea["pitch"],
        "__MIN_TASKS__": str(config.quality.min_tasks_per_project),
        "__MAX_TASKS__": str(config.quality.max_tasks_per_project),
    }
    for token, value in tokens.items():
        base = base.replace(token, value)
    return base


def parse_tasks_json(text: str | None, config: Config) -> list[dict]:
    """Parse and validate the agent's task list. Raises PlanError on anything
    that isn't a clean, in-range, well-typed list of tasks."""
    try:
        data = extract_json(text, kind="array")
    except JsonExtractError as e:
        raise PlanError(str(e)) from e

    min_t, max_t = config.quality.min_tasks_per_project, config.quality.max_tasks_per_project
    if not (min_t <= len(data) <= max_t):
        raise PlanError(
            f"expected {config.quality.min_tasks_per_project}-"
            f"{config.quality.max_tasks_per_project} tasks, got {len(data)}"
        )

    validated = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise PlanError(f"task #{i} is not an object")
        missing = _REQUIRED_TASK_FIELDS - item.keys()
        if missing:
            raise PlanError(f"task #{i} is missing fields: {sorted(missing)}")
        if item["complexity"] not in _VALID_COMPLEXITY:
            raise PlanError(f"task #{i} has invalid complexity: {item['complexity']!r}")
        if not str(item["title"]).strip():
            raise PlanError(f"task #{i} has an empty title")
        if not str(item["acceptance"]).strip():
            raise PlanError(f"task #{i} has an empty acceptance criterion")
        validated.append(item)
    return validated


def write_tasks_md(local_path: str | Path, tasks: list[dict]) -> None:
    lines = ["# Tasks", ""]
    for i, task in enumerate(tasks, start=1):
        lines.append(f"## {i}. {task['title']} ({task['complexity']})")
        lines.append("")
        lines.append(task["description"])
        lines.append("")
        lines.append(f"**Acceptance:** {task['acceptance']}")
        lines.append("")
    (Path(local_path) / "TASKS.md").write_text("\n".join(lines), encoding="utf-8")


def run_plan(
    conn,
    config: Config,
    *,
    project_id: int,
    local_path: str,
    idea: dict,
    prompts_dir: str | Path | None = None,
    claude_bin: str | Sequence[str] = "claude",
    run_agent_fn: Callable[..., AgentResult] = run_agent,
) -> list[int]:
    """Run one plan session for `idea` (title/category/pitch), insert its
    tasks under `project_id`, and write TASKS.md into local_path. Returns the
    new tasks' row ids in order. Raises PlanError (writing nothing) on any
    session or validation failure."""
    prompt = build_plan_prompt(prompts_dir or DEFAULT_PROMPTS_DIR, idea, config)
    model = model_for_stage(config.agent, "plan")

    result = run_agent_fn(
        prompt,
        model=model,
        cwd=local_path,
        max_turns=config.agent.max_turns_per_task,
        timeout_sec=config.agent.task_timeout_sec,
        allowed_tools=_PLAN_ALLOWED_TOOLS,
        claude_bin=claude_bin,
    )
    if not result.success:
        if result.rate_limited:
            reason = "rate limited"
        elif result.timed_out:
            reason = "timed out"
        else:
            reason = f"exit code {result.exit_code}"
        raise PlanError(f"plan session failed: {reason}")

    tasks = parse_tasks_json(result.result_text, config)

    now = datetime.now(UTC).isoformat()
    task_ids = [
        insert_task(
            conn,
            project_id=project_id,
            seq=seq,
            title=task["title"],
            description=task["description"],
            acceptance=task["acceptance"],
            complexity=task["complexity"],
            updated_at=now,
        )
        for seq, task in enumerate(tasks, start=1)
    ]
    write_tasks_md(local_path, tasks)
    return task_ids
