"""Stage 7: release (plan section 7.7). Once every task on a project is done
or skipped, this verifies the repo is ship-shape, has an agent session write
a CHANGELOG.md entry, tags v0.1.0, cuts a GitHub release, and marks the
project `shipped`. Every GitHub-touching call is injectable, matching the
`github_create_fn` pattern in scaffold.py, so tests never hit the network.

# TODO: optional npm/PyPI publish, out of scope for now
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from factory.agent import AgentResult, run_agent
from factory.config import Config
from factory.db import update_project_status
from factory.router import model_for_stage

DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_RELEASE_ALLOWED_TOOLS = "Read,Write,Edit,Bash(git log*)"
_REQUIRED_README_HEADINGS = ("## Install", "## Usage", "## Example")
_RELEASE_TAG = "v0.1.0"


class ReleaseError(RuntimeError):
    """Raised on any hard failure in the release pipeline. The project's DB
    status is only ever touched at the very end, once every step succeeded."""


def all_tasks_finished(conn, project_id: int) -> bool:
    """True if every task under project_id is done or skipped. Also true
    (vacuously) when the project has no tasks at all."""
    row = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE project_id = ? AND status NOT IN ('done', 'skipped')",
        (project_id,),
    ).fetchone()
    return row[0] == 0


def default_check_ci_green(local_path: str, gh_bin: str | Sequence[str] = "gh") -> bool:
    """Real implementation: is the latest run on `main` completed+success?"""
    base_cmd = [gh_bin] if isinstance(gh_bin, str) else list(gh_bin)
    try:
        proc = subprocess.run(
            [
                *base_cmd, "run", "list", "--branch", "main", "--limit", "1",
                "--json", "conclusion,status",
            ],
            cwd=local_path, capture_output=True, text=True, timeout=60,
        )
    except FileNotFoundError:
        return False
    if proc.returncode != 0:
        return False
    try:
        runs = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return False
    if not runs:
        return False
    return runs[0].get("status") == "completed" and runs[0].get("conclusion") == "success"


def preflight_check(
    local_path: str, *, check_ci_green_fn: Callable[[str], bool]
) -> tuple[bool, list[str]]:
    """README has install/usage/example sections, LICENSE exists, CI is
    green. Returns (ok, problems) -- problems lists every failing check, not
    just the first, so a caller can report them all at once."""
    problems: list[str] = []

    readme_path = Path(local_path) / "README.md"
    if not readme_path.is_file():
        problems.append("README.md is missing")
    else:
        text = readme_path.read_text(encoding="utf-8")
        for heading in _REQUIRED_README_HEADINGS:
            if heading not in text:
                problems.append(f"README.md is missing a {heading!r} section")

    if not (Path(local_path) / "LICENSE").is_file():
        problems.append("LICENSE is missing")

    if not check_ci_green_fn(local_path):
        problems.append("CI is not green on the default branch")

    return not problems, problems


def build_release_prompt(prompts_dir: str | Path, idea: dict) -> str:
    base = (Path(prompts_dir) / "release.md").read_text(encoding="utf-8")
    tokens = {
        "__PROJECT_TITLE__": idea["title"],
        "__PROJECT_PITCH__": idea["pitch"],
    }
    for token, value in tokens.items():
        base = base.replace(token, value)
    return base


def default_git_tag_and_push(local_path: str, tag: str) -> None:
    """Real implementation: tag HEAD and push the tag to origin."""
    subprocess.run(["git", "tag", tag], cwd=local_path, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "push", "origin", tag], cwd=local_path, check=True, capture_output=True, text=True
    )


def default_github_release_create(local_path: str, tag: str) -> None:
    """Real implementation: `gh release create <tag>`."""
    subprocess.run(
        ["gh", "release", "create", tag, "--generate-notes"],
        cwd=local_path, check=True, capture_output=True, text=True,
    )


def default_github_repo_edit(local_path: str, description: str, topics: list[str]) -> None:
    """Real implementation: set repo description and topics via `gh repo edit`."""
    args = ["gh", "repo", "edit", "--description", description]
    for topic in topics:
        if topic:
            args += ["--add-topic", topic]
    subprocess.run(args, cwd=local_path, check=True, capture_output=True, text=True)


def run_release(
    conn,
    config: Config,
    *,
    project_id: int,
    local_path: str,
    idea: dict,
    prompts_dir: str | Path | None = None,
    claude_bin: str | Sequence[str] = "claude",
    run_agent_fn: Callable[..., AgentResult] = run_agent,
    check_ci_green_fn: Callable[[str], bool] | None = None,
    git_tag_and_push_fn: Callable[[str, str], None] = default_git_tag_and_push,
    github_release_create_fn: Callable[[str, str], None] = default_github_release_create,
    github_repo_edit_fn: Callable[[str, str, list[str]], None] = default_github_repo_edit,
) -> None:
    """Run the full release pipeline for `project_id`. Raises ReleaseError
    (writing nothing to the project's status) on any hard failure. Only marks
    the project `shipped` once every step above has succeeded."""
    if not all_tasks_finished(conn, project_id):
        raise ReleaseError("not all tasks are done or skipped yet")

    ok, problems = preflight_check(
        local_path, check_ci_green_fn=check_ci_green_fn or default_check_ci_green
    )
    if not ok:
        raise ReleaseError(f"pre-flight checks failed: {'; '.join(problems)}")

    prompt = build_release_prompt(prompts_dir or DEFAULT_PROMPTS_DIR, idea)
    model = model_for_stage(config.agent, "release")
    result = run_agent_fn(
        prompt,
        model=model,
        cwd=local_path,
        max_turns=config.agent.max_turns_per_task,
        timeout_sec=config.agent.task_timeout_sec,
        allowed_tools=_RELEASE_ALLOWED_TOOLS,
        claude_bin=claude_bin,
    )
    if not result.success:
        if result.rate_limited:
            reason = "rate limited"
        elif result.timed_out:
            reason = "timed out"
        else:
            reason = f"exit code {result.exit_code}"
        raise ReleaseError(f"release session failed: {reason}")

    if not (Path(local_path) / "CHANGELOG.md").is_file():
        raise ReleaseError("release session did not write CHANGELOG.md")

    git_tag_and_push_fn(local_path, _RELEASE_TAG)
    github_release_create_fn(local_path, _RELEASE_TAG)

    try:
        github_repo_edit_fn(local_path, idea["pitch"], [idea.get("category", "")])
    except Exception:
        pass  # cosmetic only: never block a release over repo metadata

    update_project_status(conn, project_id, status="shipped")
