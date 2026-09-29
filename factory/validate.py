"""Stage 2: validate. For each new idea, checks GitHub for an existing
maintained competitor, then (if none blocks it) asks the agent to score its
usefulness 0-10. Approves at score >= 7, rejects everything else, and always
records a reason so the ideas table shows why.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from factory.agent import AgentResult, run_agent
from factory.config import Config
from factory.db import update_idea_status
from factory.jsonutil import JsonExtractError, extract_json
from factory.router import model_for_stage

DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
APPROVAL_SCORE_THRESHOLD = 7
_COMPETITOR_LOOKBACK_DAYS = 365
_VALIDATE_ALLOWED_TOOLS = ""  # scoring needs no tools, just the idea + search results


class ValidateError(RuntimeError):
    """Raised when the GitHub search or the scoring session itself fails
    (not when an idea is legitimately rejected -- that's a normal outcome)."""


def search_existing_repos(
    keywords: str, *, limit: int = 10, gh_bin: str | Sequence[str] = "gh"
) -> list[dict]:
    """`gh search repos <keywords> --json name,stargazersCount,pushedAt,description`."""
    base_cmd = [gh_bin] if isinstance(gh_bin, str) else list(gh_bin)
    cmd = [
        *base_cmd,
        "search",
        "repos",
        keywords,
        "--limit",
        str(limit),
        "--json",
        "name,stargazersCount,pushedAt,description",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError as e:
        raise ValidateError(f"gh binary not found: {e}") from e
    if proc.returncode != 0:
        raise ValidateError(f"gh search repos failed: {proc.stderr[-500:]}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise ValidateError(f"gh search repos returned bad JSON: {e}") from e


def find_blocking_competitor(repos: list[dict], config: Config) -> dict | None:
    """The first repo pushed within the lookback window with more stars than
    the configured threshold, or None if nothing blocks the idea."""
    cutoff = datetime.now(UTC) - timedelta(days=_COMPETITOR_LOOKBACK_DAYS)
    for repo in repos:
        pushed_at = repo.get("pushedAt")
        if not pushed_at:
            continue
        try:
            pushed = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        stars = repo.get("stargazersCount", 0) or 0
        if pushed >= cutoff and stars > config.quality.reject_if_existing_repo_stars_over:
            return repo
    return None


def build_validate_prompt(
    prompts_dir: str | Path, idea: dict, competing_repos: list[dict]
) -> str:
    base = (Path(prompts_dir) / "validate.md").read_text(encoding="utf-8")
    if competing_repos:
        lines = [
            f"- {r.get('name')}: {r.get('stargazersCount', 0)} stars, "
            f"pushed {r.get('pushedAt', 'unknown')}"
            for r in competing_repos
        ]
        search_summary = "\n".join(lines)
    else:
        search_summary = "(no results)"
    tokens = {
        "__IDEA_TITLE__": idea["title"],
        "__IDEA_CATEGORY__": idea["category"],
        "__IDEA_PITCH__": idea["pitch"],
        "__SEARCH_RESULTS__": search_summary,
    }
    for token, value in tokens.items():
        base = base.replace(token, value)
    return base


def parse_score_json(text: str | None) -> dict:
    """Parse {"score": 0-10, "rationale": "..."}. Raises ValidateError on any
    shape that isn't trustworthy enough to act on."""
    try:
        data = extract_json(text, kind="object")
    except JsonExtractError as e:
        raise ValidateError(str(e)) from e

    if "score" not in data or "rationale" not in data:
        raise ValidateError(f"score response is missing fields: {data!r}")
    score = data["score"]
    if not isinstance(score, int | float) or not (0 <= score <= 10):
        raise ValidateError(f"score must be a number 0-10, got {score!r}")
    if not str(data["rationale"]).strip():
        raise ValidateError("score response has an empty rationale")
    return {"score": float(score), "rationale": str(data["rationale"])}


def validate_idea(
    conn,
    config: Config,
    idea: dict,
    *,
    workspace_dir: str,
    prompts_dir: str | Path | None = None,
    gh_bin: str | Sequence[str] = "gh",
    claude_bin: str | Sequence[str] = "claude",
    run_agent_fn: Callable[..., AgentResult] = run_agent,
) -> dict:
    """Validate one idea (dict with id/title/category/pitch) and persist the
    outcome. Returns {"status", "score", "reject_reason"}. Raises
    ValidateError (writing nothing) if the search or scoring session itself
    fails -- a legitimate low score is not an error, it's rejected normally.
    """
    repos = search_existing_repos(idea["title"], gh_bin=gh_bin)
    competitor = find_blocking_competitor(repos, config)
    if competitor is not None:
        reason = (
            f"existing maintained repo {competitor.get('name')} "
            f"({competitor.get('stargazersCount', 0)} stars) already does this"
        )
        update_idea_status(conn, idea["id"], status="rejected", score=None, reject_reason=reason)
        return {"status": "rejected", "score": None, "reject_reason": reason}

    prompt = build_validate_prompt(prompts_dir or DEFAULT_PROMPTS_DIR, idea, repos)
    model = model_for_stage(config.agent, "validate")
    result = run_agent_fn(
        prompt,
        model=model,
        cwd=workspace_dir,
        max_turns=config.agent.max_turns_per_task,
        timeout_sec=config.agent.task_timeout_sec,
        allowed_tools=_VALIDATE_ALLOWED_TOOLS,
        claude_bin=claude_bin,
    )
    if not result.success:
        if result.rate_limited:
            reason = "rate limited"
        elif result.timed_out:
            reason = "timed out"
        else:
            reason = f"exit code {result.exit_code}"
        raise ValidateError(f"validate session failed: {reason}")

    scored = parse_score_json(result.result_text)
    status = "approved" if scored["score"] >= APPROVAL_SCORE_THRESHOLD else "rejected"
    reject_reason = None if status == "approved" else scored["rationale"]
    update_idea_status(
        conn, idea["id"], status=status, score=scored["score"], reject_reason=reject_reason
    )
    return {"status": status, "score": scored["score"], "reject_reason": reject_reason}
