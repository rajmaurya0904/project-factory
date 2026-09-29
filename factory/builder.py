"""Stage 5: the per-task build loop (plan section 7.5-7.6).

One task = one fresh agent session, gated (factory.gate), then committed and
pushed on pass, or discarded and retried (with one in-place fix session, then
optional escalation to Sonnet) on failure. `build_task` is the tested, atomic
unit; `run_pending_tasks` is a thin convenience loop over a project's pending
tasks -- the real cross-project orchestrator lives in main.py, not here.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from factory.agent import AgentResult, run_agent
from factory.config import Config
from factory.db import (
    increment_daily_counters,
    insert_session,
    update_session_log_path,
    update_task_status,
)
from factory.gate import run_gate
from factory.log import write_session_log
from factory.router import alias_for_task, escalated_model, model_for_task, should_escalate

DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
BUILD_ALLOWED_TOOLS = "Read,Write,Edit,Bash,Grep,Glob"

# ponytail: a heuristic, not a parser -- good enough to decide whether a task's
# free-text acceptance field doubles as a runnable shell check. A false
# negative just means the gate skips a bonus check it would otherwise run;
# refine only if the planner starts writing acceptance text this misreads a lot.
_COMMAND_PREFIXES = (
    "pytest", "python", "python3", "npm", "npx", "node", "sh", "bash", "make",
    "ruff", "eslint", "tsc", "mypy", "go", "cargo", "./",
)
_PROSE_MARKERS = (" should", " must", " works", " passes", " is ", " are ", " when ")


def looks_like_shell_command(acceptance: str) -> bool:
    text = acceptance.strip()
    if not text:
        return False
    padded = f" {text.lower()} "
    if any(marker in padded for marker in _PROSE_MARKERS):
        return False
    return text.startswith(_COMMAND_PREFIXES)


@dataclass(frozen=True)
class BuildResult:
    status: str  # "done" | "failed"
    commit_sha: str | None
    model_used: str
    reason: str | None = None


def build_task_prompt(
    prompts_dir: str | Path, task: dict, *, fix_reason: str | None = None
) -> str:
    base = (Path(prompts_dir) / "build_task.md").read_text(encoding="utf-8")
    tokens = {
        "__TASK_TITLE__": task["title"],
        "__TASK_DESCRIPTION__": task["description"],
        "__TASK_ACCEPTANCE__": task["acceptance"],
    }
    for token, value in tokens.items():
        base = base.replace(token, value)
    if fix_reason:
        base += (
            "\n\n## Previous attempt failed the gate\n\n"
            "Fix the following issue, then stop -- do not touch unrelated files:\n\n"
            f"{fix_reason}\n"
        )
    return base


def _git(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )


def _commit_and_push(local_path: str, task: dict) -> str:
    _git(["add", "-A"], local_path)
    message = f"{task['title']}\n\nAcceptance: {task['acceptance']}"
    _git(["commit", "-q", "-m", message], local_path)
    _git(["push", "origin", "HEAD"], local_path)
    return _git(["rev-parse", "HEAD"], local_path).stdout.strip()


def _reset_hard(local_path: str) -> None:
    _git(["reset", "--hard", "HEAD"], local_path)
    _git(["clean", "-fd"], local_path)


def _agent_failure_reason(result: AgentResult) -> str:
    if result.rate_limited:
        return "agent session was rate limited"
    if result.timed_out:
        return "agent session timed out"
    return f"agent session exited with code {result.exit_code}"


def _run_session(
    *,
    conn,
    config: Config,
    project_id: int,
    task_id: int,
    model: str,
    prompt: str,
    local_path: str,
    state_dir: str,
    claude_bin: str | Sequence[str],
    run_agent_fn: Callable[..., AgentResult],
) -> AgentResult:
    """Run one fresh agent session and record it (sessions row + JSON log +
    daily session/cost counters), regardless of whether it succeeds."""
    started_at = datetime.now(UTC).isoformat()
    result = run_agent_fn(
        prompt,
        model=model,
        cwd=local_path,
        max_turns=config.agent.max_turns_per_task,
        timeout_sec=config.agent.task_timeout_sec,
        allowed_tools=BUILD_ALLOWED_TOOLS,
        claude_bin=claude_bin,
    )
    ended_at = datetime.now(UTC).isoformat()

    session_id = insert_session(
        conn,
        project_id=project_id,
        task_id=task_id,
        stage="build",
        model=model,
        started_at=started_at,
        ended_at=ended_at,
        exit_code=result.exit_code,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=result.cost_usd,
    )
    log_path = write_session_log(
        state_dir,
        session_id,
        {
            "session_id": session_id,
            "project_id": project_id,
            "task_id": task_id,
            "stage": "build",
            "model": model,
            "started_at": started_at,
            "ended_at": ended_at,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "rate_limited": result.rate_limited,
            "duration_sec": result.duration_sec,
            "result_text": result.result_text,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "cost_usd": result.cost_usd,
        },
    )
    update_session_log_path(conn, session_id, str(log_path))

    day = datetime.now(UTC).strftime("%Y-%m-%d")
    increment_daily_counters(conn, day, sessions=1, cost_usd=result.cost_usd or 0.0)
    return result


def _attempt_task(
    *,
    conn,
    config: Config,
    project_id: int,
    task: dict,
    model: str,
    local_path: str,
    language: str,
    test_cmd: list[str] | None,
    lint_cmd: list[str] | None,
    type_check_cmd: list[str] | None,
    gitleaks_bin,
    prompts_dir: str | Path,
    state_dir: str,
    claude_bin: str | Sequence[str],
    run_agent_fn: Callable[..., AgentResult],
    sleep_fn: Callable[[float], None],
) -> tuple[bool, str | None]:
    """One full attempt on a single model: a fresh session, the gate, and (on
    failure) one fix session plus a re-gate. Returns (passed, failure_reason).
    An agent session failure skips the gate for that round but otherwise
    follows the same fix-and-retry path as a gate failure."""
    fix_reason: str | None = None
    for _round in range(2):
        prompt = build_task_prompt(prompts_dir, task, fix_reason=fix_reason)
        result = _run_session(
            conn=conn, config=config, project_id=project_id, task_id=task["id"],
            model=model, prompt=prompt, local_path=local_path, state_dir=state_dir,
            claude_bin=claude_bin, run_agent_fn=run_agent_fn,
        )
        if result.success:
            gate = run_gate(
                local_path,
                language=language,
                test_cmd=test_cmd,
                lint_cmd=lint_cmd,
                type_check_cmd=type_check_cmd,
                acceptance_cmd=(
                    task["acceptance"] if looks_like_shell_command(task["acceptance"]) else None
                ),
                gitleaks_bin=gitleaks_bin,
            )
            if gate.passed:
                return True, None
            fix_reason = f"{gate.failed_check}: {gate.reason}"
        else:
            fix_reason = _agent_failure_reason(result)
        sleep_fn(config.limits.min_seconds_between_sessions)
    return False, fix_reason


def build_task(
    conn,
    config: Config,
    *,
    project_id: int,
    task: dict,
    local_path: str,
    language: str,
    test_cmd: list[str] | None = None,
    lint_cmd: list[str] | None = None,
    type_check_cmd: list[str] | None = None,
    gitleaks_bin: str | Sequence[str] | None = None,
    prompts_dir: str | Path | None = None,
    state_dir: str = "state",
    claude_bin: str | Sequence[str] = "claude",
    run_agent_fn: Callable[..., AgentResult] = run_agent,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> BuildResult:
    """Execute one task (dict with id/title/description/acceptance/complexity)
    to completion: build, gate, commit+push on pass; one fix-and-retry, then
    escalate-or-fail on repeated failure. Always leaves the working tree clean
    (either a fresh commit, or reset back to the prior HEAD)."""
    prompts_dir = prompts_dir or DEFAULT_PROMPTS_DIR
    task_id = task["id"]
    alias = alias_for_task(config.agent, task["complexity"])
    model = model_for_task(config.agent, task["complexity"])

    now = datetime.now(UTC).isoformat()
    update_task_status(conn, task_id, status="in_progress", updated_at=now, bump_attempts=True)

    kwargs = dict(
        conn=conn, config=config, project_id=project_id, task=task,
        local_path=local_path, language=language, test_cmd=test_cmd, lint_cmd=lint_cmd,
        type_check_cmd=type_check_cmd, gitleaks_bin=gitleaks_bin, prompts_dir=prompts_dir,
        state_dir=state_dir, claude_bin=claude_bin, run_agent_fn=run_agent_fn, sleep_fn=sleep_fn,
    )
    passed, reason = _attempt_task(model=model, **kwargs)

    if not passed:
        _reset_hard(local_path)
        if should_escalate(config.agent, alias, gate_failed_twice=True):
            model = escalated_model(config.agent)
            now = datetime.now(UTC).isoformat()
            update_task_status(
                conn, task_id, status="in_progress", updated_at=now, bump_attempts=True
            )
            passed, reason = _attempt_task(model=model, **kwargs)
            if not passed:
                _reset_hard(local_path)

    now = datetime.now(UTC).isoformat()
    if passed:
        sha = _commit_and_push(local_path, task)
        update_task_status(
            conn, task_id, status="done", updated_at=now, model_used=model, commit_sha=sha
        )
        increment_daily_counters(conn, now[:10], commits=1)
        return BuildResult(status="done", commit_sha=sha, model_used=model)

    update_task_status(conn, task_id, status="failed", updated_at=now, model_used=model)
    return BuildResult(status="failed", commit_sha=None, model_used=model, reason=reason)


def run_pending_tasks(
    conn,
    config: Config,
    *,
    project: dict,
    **build_task_kwargs,
) -> list[BuildResult]:
    """Convenience wrapper: run build_task for every pending task of `project`
    (dict with id/local_path/language), in `seq` order, until none remain.
    The real multi-project scheduler belongs in main.py, not here."""
    results = []
    while True:
        row = conn.execute(
            "SELECT * FROM tasks WHERE project_id = ? AND status = 'pending' "
            "ORDER BY seq LIMIT 1",
            (project["id"],),
        ).fetchone()
        if row is None:
            break
        result = build_task(
            conn,
            config,
            project_id=project["id"],
            task=dict(row),
            local_path=project["local_path"],
            language=project["language"],
            **build_task_kwargs,
        )
        results.append(result)
    return results
