"""Stage 8: orchestration. `run_once` picks whatever the DB state calls for
next (validate an idea, build the next pending task, release a finished
project, scaffold+plan an approved idea, or ideate from scratch) and does it.
There is no separate in-memory state machine to resume: the DB *is* the
state, so restarting the process just re-derives "what's next" from it.

Every network-touching call (claude, gh, git push) is injectable here too,
the same pattern every stage module already uses -- so the exact same
`run_once`/`run_forever` code path is what a dry run (fake claude binary,
local bare git remote instead of GitHub) exercises in tests.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from factory import builder, guard, ideate, log, planner, release, scaffold, validate
from factory.agent import AgentResult, run_agent
from factory.agent import is_rate_limited as _text_is_rate_limited
from factory.config import Config, load_config
from factory.db import connect

DEFAULT_CONFIG_PATH = "config.yaml"
DEFAULT_STATE_DIR = "state"
DEFAULT_DB_PATH = f"{DEFAULT_STATE_DIR}/factory.db"

# Plan section 9: subscription auth has a 5-hour rolling cap. Back off for
# that long rather than retrying in a tight loop; the stop file or a manual
# restart can always cut this short.
RATE_LIMIT_BACKOFF_SEC = 5 * 3600

_CATEGORY_TO_TEMPLATE = {
    "skill": "skill",
    "mcp": "mcp",
    "cli": "python",
    "devtool": "python",
    "data": "python",
    "template": "python",
    "agent-tool": "python",
}


def template_for_category(category: str) -> str:
    """Idea category -> scaffold template. Unknown categories default to
    the plain python template rather than failing the whole pipeline."""
    return _CATEGORY_TO_TEMPLATE.get(category, "python")


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _paused_until_path(state_dir: str | Path) -> Path:
    return Path(state_dir) / "paused_until"


def check_rate_limit_pause(state_dir: str | Path, *, sleep_fn: Callable[[float], None]) -> None:
    """Block until any previously-recorded rate-limit pause has elapsed."""
    path = _paused_until_path(state_dir)
    if not path.exists():
        return
    try:
        until = datetime.fromisoformat(path.read_text(encoding="utf-8").strip())
    except ValueError:
        path.unlink(missing_ok=True)
        return
    remaining = (until - datetime.now(UTC)).total_seconds()
    if remaining > 0:
        sleep_fn(remaining)
    path.unlink(missing_ok=True)


def write_rate_limit_pause(state_dir: str | Path, seconds: float = RATE_LIMIT_BACKOFF_SEC) -> None:
    path = _paused_until_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    until = datetime.now(UTC) + timedelta(seconds=seconds)
    path.write_text(until.isoformat(), encoding="utf-8")


def run_once(
    conn,
    config: Config,
    *,
    workspace_dir: str,
    state_dir: str | Path = DEFAULT_STATE_DIR,
    logger=None,
    claude_bin: str | Sequence[str] = "claude",
    gh_bin: str | Sequence[str] = "gh",
    gitleaks_bin: str | Sequence[str] | None = None,
    run_agent_fn: Callable[..., AgentResult] = run_agent,
    create_remote: bool = True,
    github_create_fn=None,
    check_ci_green_fn=None,
    git_tag_and_push_fn=release.default_git_tag_and_push,
    github_release_create_fn=release.default_github_release_create,
    github_repo_edit_fn=release.default_github_repo_edit,
) -> bool:
    """Do exactly one unit of pipeline work, chosen by priority:
    validate a new idea > build the next pending task > release a finished
    project > scaffold+plan an approved idea > ideate from scratch.
    Returns True if it did something, False if there was nothing to do
    (caller should sleep before checking again)."""
    logger = logger or log.get_logger(state_dir)
    day = _today()

    ok, reason = guard.check_all(conn, config, day)
    if not ok:
        logger.info("guard blocked this tick: %s", reason)
        return False

    idea_row = conn.execute(
        "SELECT * FROM ideas WHERE status = 'new' ORDER BY id LIMIT 1"
    ).fetchone()
    if idea_row is not None:
        idea = dict(idea_row)
        outcome = validate.validate_idea(
            conn, config, idea, workspace_dir=workspace_dir,
            gh_bin=gh_bin, claude_bin=claude_bin, run_agent_fn=run_agent_fn,
        )
        logger.info("validated idea %s: %s", idea["id"], outcome["status"])
        return True

    task_row = conn.execute(
        "SELECT t.* FROM tasks t JOIN projects p ON p.id = t.project_id "
        "WHERE t.status = 'pending' AND p.status != 'shipped' "
        "ORDER BY p.id, t.seq LIMIT 1"
    ).fetchone()
    if task_row is not None:
        task = dict(task_row)
        project = dict(
            conn.execute(
                "SELECT * FROM projects WHERE id = ?", (task["project_id"],)
            ).fetchone()
        )
        if not guard.is_allowlisted_repo(project["repo_name"], config):
            logger.warning("project %s not allowlisted, skipping", project["repo_name"])
            return False
        result = builder.build_task(
            conn, config,
            project_id=project["id"], task=task,
            local_path=project["local_path"], language=project["language"],
            state_dir=state_dir, claude_bin=claude_bin, run_agent_fn=run_agent_fn,
            gitleaks_bin=gitleaks_bin,
        )
        logger.info("task %s (%s): %s", task["id"], task["title"], result.status)
        return True

    for row in conn.execute("SELECT * FROM projects WHERE status != 'shipped'"):
        project = dict(row)
        has_tasks = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE project_id = ?", (project["id"],)
        ).fetchone()[0]
        if has_tasks and release.all_tasks_finished(conn, project["id"]):
            idea = dict(
                conn.execute(
                    "SELECT * FROM ideas WHERE id = ?", (project["idea_id"],)
                ).fetchone()
            )
            release.run_release(
                conn, config,
                project_id=project["id"], local_path=project["local_path"], idea=idea,
                claude_bin=claude_bin, run_agent_fn=run_agent_fn,
                check_ci_green_fn=check_ci_green_fn,
                git_tag_and_push_fn=git_tag_and_push_fn,
                github_release_create_fn=github_release_create_fn,
                github_repo_edit_fn=github_repo_edit_fn,
            )
            logger.info("shipped project %s", project["repo_name"])
            return True

    idea_row = conn.execute(
        "SELECT i.* FROM ideas i LEFT JOIN projects p ON p.idea_id = i.id "
        "WHERE i.status = 'approved' AND p.id IS NULL ORDER BY i.score DESC LIMIT 1"
    ).fetchone()
    if idea_row is not None:
        idea = dict(idea_row)
        template = template_for_category(idea["category"])
        scaffolded = scaffold.scaffold_project(
            conn, config, idea, template, workspace_dir,
            create_remote=create_remote, github_create_fn=github_create_fn,
        )
        planner.run_plan(
            conn, config,
            project_id=scaffolded.project_id, local_path=scaffolded.local_path, idea=idea,
            claude_bin=claude_bin, run_agent_fn=run_agent_fn,
        )
        logger.info("scaffolded and planned project %s", scaffolded.repo_name)
        return True

    ideate.run_ideate(
        conn, config, workspace_dir=workspace_dir,
        claude_bin=claude_bin, run_agent_fn=run_agent_fn,
    )
    logger.info("ideated a fresh batch of ideas")
    return True


def run_forever(
    conn,
    config: Config,
    *,
    workspace_dir: str,
    state_dir: str | Path = DEFAULT_STATE_DIR,
    sleep_fn: Callable[[float], None] = time.sleep,
    max_iterations: int | None = None,
    **run_once_kwargs,
) -> None:
    """The persistent loop `systemd` runs. Exits cleanly (not an error) once
    the stop file appears; `max_iterations` exists only for tests, so they
    don't have to actually run forever."""
    logger = log.get_logger(state_dir)
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        iterations += 1
        if guard.stop_requested(config.paths.stop_file):
            logger.info("stop file present, exiting cleanly")
            return
        check_rate_limit_pause(state_dir, sleep_fn=sleep_fn)
        try:
            did_work = run_once(
                conn, config, workspace_dir=workspace_dir, state_dir=state_dir,
                logger=logger, **run_once_kwargs,
            )
        except (
            ideate.IdeateError,
            validate.ValidateError,
            planner.PlanError,
            release.ReleaseError,
        ) as e:
            if _text_is_rate_limited(str(e)):
                logger.warning("rate limited, pausing %ss: %s", RATE_LIMIT_BACKOFF_SEC, e)
                write_rate_limit_pause(state_dir)
            else:
                logger.warning("stage error, will retry next tick: %s", e)
            did_work = True
        if not did_work:
            sleep_fn(config.limits.min_seconds_between_sessions)


def _cmd_run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    conn = connect(args.db)
    Path(config.paths.workspace).mkdir(parents=True, exist_ok=True)
    run_forever(conn, config, workspace_dir=config.paths.workspace, state_dir=args.state_dir)
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    conn = connect(args.db)
    day = _today()
    counters = conn.execute(
        "SELECT * FROM daily_counters WHERE day = ?", (day,)
    ).fetchone()
    print(f"Day: {day}")
    if counters is None:
        print("  commits=0 sessions=0 repos=0 cost_usd=0.0")
    else:
        print(
            f"  commits={counters['commits']} sessions={counters['sessions']} "
            f"repos={counters['repos']} cost_usd={counters['cost_usd']}"
        )
    print(f"Paused: {guard.stop_requested(config.paths.stop_file)}")

    project = conn.execute(
        "SELECT * FROM projects WHERE status != 'shipped' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if project is None:
        print("Current project: none")
    else:
        print(f"Current project: {project['repo_name']} ({project['status']})")
        task = conn.execute(
            "SELECT * FROM tasks WHERE project_id = ? AND status = 'in_progress' "
            "ORDER BY seq LIMIT 1",
            (project["id"],),
        ).fetchone()
        if task is None:
            task = conn.execute(
                "SELECT * FROM tasks WHERE project_id = ? AND status = 'pending' "
                "ORDER BY seq LIMIT 1",
                (project["id"],),
            ).fetchone()
        print(f"Current task: {task['title'] if task else 'none'}")
    return 0


def _cmd_pause(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    Path(config.paths.stop_file).touch()
    print(f"Wrote stop file: {config.paths.stop_file}")
    return 0


def _cmd_resume(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    Path(config.paths.stop_file).unlink(missing_ok=True)
    print(f"Removed stop file: {config.paths.stop_file}")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    print(f"{'project':<40} {'status':<12} {'tasks':>6} {'commits':>8}")
    for project in conn.execute("SELECT * FROM projects ORDER BY id"):
        counts = conn.execute(
            "SELECT COUNT(*), SUM(status = 'done') FROM tasks WHERE project_id = ?",
            (project["id"],),
        ).fetchone()
        total_tasks, done_tasks = counts[0], counts[1] or 0
        commits = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE project_id = ? AND commit_sha IS NOT NULL",
            (project["id"],),
        ).fetchone()[0]
        print(
            f"{project['repo_name']:<40} {project['status']:<12} "
            f"{done_tasks}/{total_tasks:<4} {commits:>8}"
        )
    total_cost = conn.execute("SELECT SUM(cost_usd) FROM daily_counters").fetchone()[0] or 0.0
    print(f"\nTotal cost so far: ${total_cost:.2f}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="factory")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--db", default=DEFAULT_DB_PATH)
    parser.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run").set_defaults(func=_cmd_run)
    sub.add_parser("status").set_defaults(func=_cmd_status)
    sub.add_parser("pause").set_defaults(func=_cmd_pause)
    sub.add_parser("resume").set_defaults(func=_cmd_resume)
    sub.add_parser("report").set_defaults(func=_cmd_report)
    return parser


def cli(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(cli())
