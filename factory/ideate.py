"""Stage 1: ideate. Runs one headless agent session with web search enabled,
parses its JSON idea list, validates it, and inserts each idea into the DB
with status "new". Validation is stage 2's job (validate.py).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from factory.agent import AgentResult, run_agent
from factory.config import Config
from factory.db import insert_idea
from factory.jsonutil import JsonExtractError, extract_json
from factory.router import model_for_stage

DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

_REQUIRED_IDEA_FIELDS = {"title", "category", "pitch", "source", "est_scope_hours"}
_VALID_CATEGORIES = {"skill", "mcp", "cli", "devtool", "data", "template", "agent-tool"}
_IDEATE_ALLOWED_TOOLS = "WebSearch,WebFetch"


class IdeateError(RuntimeError):
    """Raised when the ideate session fails, or its output can't be trusted."""


def build_ideate_prompt(prompts_dir: str | Path, user_pain_points_path: str | Path | None) -> str:
    """Read prompts/ideate.md and append the owner's pain-points file, if any."""
    base = (Path(prompts_dir) / "ideate.md").read_text(encoding="utf-8")
    if user_pain_points_path is None:
        return base
    pains_path = Path(user_pain_points_path)
    if not pains_path.exists():
        return base
    pains = pains_path.read_text(encoding="utf-8").strip()
    if not pains:
        return base
    return f"{base}\n\n## Owner's recurring manual pain points\n\n{pains}"


def parse_ideas_json(text: str | None) -> list[dict]:
    """Parse and validate the agent's idea list. Raises IdeateError on anything
    that isn't a clean, complete list of ideas -- callers must not insert
    partial or malformed data."""
    try:
        data = extract_json(text, kind="array")
    except JsonExtractError as e:
        raise IdeateError(str(e)) from e

    validated = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise IdeateError(f"idea #{i} is not an object")
        missing = _REQUIRED_IDEA_FIELDS - item.keys()
        if missing:
            raise IdeateError(f"idea #{i} is missing fields: {sorted(missing)}")
        if item["category"] not in _VALID_CATEGORIES:
            raise IdeateError(f"idea #{i} has invalid category: {item['category']!r}")
        if not isinstance(item["est_scope_hours"], int | float) or item["est_scope_hours"] <= 0:
            raise IdeateError(
                f"idea #{i} has invalid est_scope_hours: {item.get('est_scope_hours')!r}"
            )
        if not str(item["title"]).strip() or not str(item["pitch"]).strip():
            raise IdeateError(f"idea #{i} has an empty title or pitch")
        validated.append(item)
    return validated


def run_ideate(
    conn,
    config: Config,
    *,
    workspace_dir: str,
    prompts_dir: str | Path | None = None,
    user_pain_points_path: str | Path | None = None,
    claude_bin: str | Sequence[str] = "claude",
    run_agent_fn: Callable[..., AgentResult] = run_agent,
) -> list[int]:
    """Run one ideate session and insert every returned idea. Returns the new
    ideas' row ids. Raises IdeateError without writing anything on failure."""
    prompt = build_ideate_prompt(prompts_dir or DEFAULT_PROMPTS_DIR, user_pain_points_path)
    model = model_for_stage(config.agent, "ideate")

    result = run_agent_fn(
        prompt,
        model=model,
        cwd=workspace_dir,
        max_turns=config.agent.max_turns_per_task,
        timeout_sec=config.agent.task_timeout_sec,
        allowed_tools=_IDEATE_ALLOWED_TOOLS,
        claude_bin=claude_bin,
    )
    if not result.success:
        if result.rate_limited:
            reason = "rate limited"
        elif result.timed_out:
            reason = "timed out"
        else:
            reason = f"exit code {result.exit_code}"
        raise IdeateError(f"ideate session failed: {reason}")

    ideas = parse_ideas_json(result.result_text)

    now = datetime.now(UTC).isoformat()
    idea_ids = [
        insert_idea(
            conn,
            title=idea["title"],
            category=idea["category"],
            pitch=idea["pitch"],
            source=idea.get("source"),
            created_at=now,
        )
        for idea in ideas
    ]
    return idea_ids
