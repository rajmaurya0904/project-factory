"""Tests for factory.main: the state-machine dispatch (run_once/run_forever)
and its CLI. Each stage's own internals are already covered by that stage's
own test file (test_ideate.py, test_validate.py, ...); these tests only prove
run_once picks the *right* stage in the right priority order, and that the
whole thing runs end to end with a fake agent and a local bare git remote --
no GitHub, no real network. That combination (fake claude binary + local bare
remote, driven through the real run_once/run_forever code path) is this
project's dry-run mode: there is no separate dry-run flag, every
network-touching call here is already injectable.
"""

import dataclasses
import subprocess
import sys
from pathlib import Path

import pytest

from factory.config import load_config
from factory.db import connect, insert_idea, insert_project, insert_task
from factory.ideate import IdeateError
from factory.main import (
    build_parser,
    check_rate_limit_pause,
    run_forever,
    run_once,
    template_for_category,
    write_rate_limit_pause,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FAKE_CLAUDE = [sys.executable, str(Path(__file__).parent / "fixtures" / "fake_claude.py")]
FAKE_GH = [sys.executable, str(Path(__file__).parent / "fixtures" / "fake_gh.py")]
FAKE_GITLEAKS = [sys.executable, str(Path(__file__).parent / "fixtures" / "fake_gitleaks.py")]


def _config(**limits_overrides):
    cfg = load_config(REPO_ROOT / "config.yaml")
    overrides = {"min_seconds_between_sessions": 0, **limits_overrides}
    return dataclasses.replace(cfg, limits=dataclasses.replace(cfg.limits, **overrides))


def _config_with_task_range(min_tasks: int, max_tasks: int):
    cfg = _config()
    return dataclasses.replace(
        cfg,
        quality=dataclasses.replace(
            cfg.quality, min_tasks_per_project=min_tasks, max_tasks_per_project=max_tasks
        ),
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _init_repo_with_remote(tmp_path: Path, name: str = "repo") -> Path:
    remote = tmp_path / f"{name}.git"
    subprocess.run(
        ["git", "init", "--bare", "-q", str(remote)], check=True, capture_output=True, text=True
    )
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "branch", "-m", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text(
        "# repo\n\n## Install\n\n## Usage\n\n## Example\n", encoding="utf-8"
    )
    (repo / "LICENSE").write_text("MIT\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-q", "-u", "origin", "main")
    return repo


def test_template_for_category_known_and_unknown() -> None:
    assert template_for_category("skill") == "skill"
    assert template_for_category("mcp") == "mcp"
    assert template_for_category("cli") == "python"
    assert template_for_category("something-new") == "python"


def test_run_once_validates_new_idea_before_anything_else(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    insert_idea(
        conn, title="VALIDATE_HIGH_SCORE Weather CLI", category="cli",
        pitch="p", created_at="2026-01-01",
    )
    did_work = run_once(
        conn, _config(), workspace_dir=str(tmp_path), state_dir=tmp_path,
        claude_bin=FAKE_CLAUDE, gh_bin=FAKE_GH,
    )
    assert did_work is True
    row = conn.execute("SELECT status, score FROM ideas").fetchone()
    assert row["status"] == "approved"
    assert row["score"] == 9.0


def test_run_once_builds_pending_task_before_scaffolding_or_ideating(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    repo = _init_repo_with_remote(tmp_path)
    idea_id = insert_idea(
        conn, title="Weather CLI", category="cli", pitch="p",
        status="approved", score=9.0, created_at="2026-01-01",
    )
    project_id = insert_project(
        conn, idea_id=idea_id, repo_name="factory-weather-cli", local_path=str(repo),
        language="python", created_at="2026-01-01",
    )
    insert_task(
        conn, project_id=project_id, seq=1, title="BUILD_OK add feature",
        description="Do the thing.", acceptance="it works", complexity="simple",
        updated_at="2026-01-01",
    )
    did_work = run_once(
        conn, _config(), workspace_dir=str(tmp_path), state_dir=tmp_path,
        claude_bin=FAKE_CLAUDE, gh_bin=FAKE_GH, gitleaks_bin=FAKE_GITLEAKS,
    )
    assert did_work is True
    task = conn.execute("SELECT status, commit_sha FROM tasks").fetchone()
    assert task["status"] == "done"
    assert task["commit_sha"] is not None
    # No idea or project work happened this tick -- the pending task won.
    assert conn.execute("SELECT status FROM ideas").fetchone()["status"] == "approved"


def test_run_once_releases_project_once_all_tasks_finished(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    repo = _init_repo_with_remote(tmp_path)
    idea_id = insert_idea(
        conn, title="RELEASE_OK Weather CLI", category="cli", pitch="p",
        status="approved", score=9.0, created_at="2026-01-01",
    )
    project_id = insert_project(
        conn, idea_id=idea_id, repo_name="factory-weather-cli", local_path=str(repo),
        language="python", created_at="2026-01-01",
    )
    insert_task(
        conn, project_id=project_id, seq=1, title="Add feature", description="d",
        acceptance="a", complexity="simple", updated_at="2026-01-01", status="done",
    )
    did_work = run_once(
        conn, _config(), workspace_dir=str(tmp_path), state_dir=tmp_path,
        claude_bin=FAKE_CLAUDE, gh_bin=FAKE_GH,
        check_ci_green_fn=lambda local_path: True,
        github_release_create_fn=lambda local_path, tag: None,
        github_repo_edit_fn=lambda local_path, description, topics: None,
    )
    assert did_work is True
    project = conn.execute("SELECT status FROM projects").fetchone()
    assert project["status"] == "shipped"
    assert (repo / "CHANGELOG.md").exists()
    tags = subprocess.run(
        ["git", "tag"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    assert "v0.1.0" in tags


def test_run_once_scaffolds_and_plans_approved_idea_with_no_project(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    insert_idea(
        conn, title="PLAN_OK Weather CLI", category="cli", pitch="p",
        status="approved", score=9.0, created_at="2026-01-01",
    )
    did_work = run_once(
        conn, _config_with_task_range(1, 5), workspace_dir=str(tmp_path), state_dir=tmp_path,
        claude_bin=FAKE_CLAUDE, gh_bin=FAKE_GH, create_remote=False,
    )
    assert did_work is True
    project = conn.execute("SELECT * FROM projects").fetchone()
    assert project is not None
    assert project["repo_name"].startswith("factory-")
    tasks = conn.execute("SELECT COUNT(*) FROM tasks WHERE project_id = ?", (project["id"],))
    assert tasks.fetchone()[0] == 2
    assert (Path(project["local_path"]) / "TASKS.md").exists()


def test_run_once_attempts_ideate_when_nothing_else_to_do(tmp_path: Path) -> None:
    """With no ideas/tasks/projects at all, run_once falls through to ideate.
    The fake claude binary's generic response isn't a JSON idea array, so the
    stage raises -- proving run_once actually reached the ideate branch
    (run_forever is what turns this into a logged retry, tested separately)."""
    conn = connect(tmp_path / "factory.db")
    with pytest.raises(IdeateError):
        run_once(
            conn, _config(), workspace_dir=str(tmp_path), state_dir=tmp_path,
            claude_bin=FAKE_CLAUDE, gh_bin=FAKE_GH,
        )
    assert conn.execute("SELECT COUNT(*) FROM ideas").fetchone()[0] == 0


def test_run_once_returns_false_when_guard_blocks(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    stop_file = tmp_path / "STOP"
    stop_file.touch()
    config = dataclasses.replace(
        _config(), paths=dataclasses.replace(_config().paths, stop_file=str(stop_file))
    )
    did_work = run_once(conn, config, workspace_dir=str(tmp_path), state_dir=tmp_path)
    assert did_work is False


def test_run_forever_stops_at_stop_file(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    stop_file = tmp_path / "STOP"
    # max_commits_per_day=0 makes the guard block every tick (did_work=False),
    # so sleep_fn actually gets called without needing a real/fake agent.
    base = _config(max_commits_per_day=0)
    config = dataclasses.replace(
        base, paths=dataclasses.replace(base.paths, stop_file=str(stop_file))
    )
    calls = []

    def fake_sleep(seconds: float) -> None:
        calls.append(seconds)
        stop_file.touch()  # simulate an operator pausing mid-loop

    run_forever(
        conn, config, workspace_dir=str(tmp_path), state_dir=tmp_path,
        sleep_fn=fake_sleep, max_iterations=50,
    )
    assert stop_file.exists()
    assert len(calls) >= 1


def test_rate_limit_pause_round_trip(tmp_path: Path) -> None:
    write_rate_limit_pause(tmp_path, seconds=100)
    assert (tmp_path / "paused_until").exists()
    slept = []
    check_rate_limit_pause(tmp_path, sleep_fn=slept.append)
    assert len(slept) == 1
    assert slept[0] > 0
    assert not (tmp_path / "paused_until").exists()


def test_rate_limit_pause_noop_when_already_elapsed(tmp_path: Path) -> None:
    write_rate_limit_pause(tmp_path, seconds=-10)
    slept = []
    check_rate_limit_pause(tmp_path, sleep_fn=slept.append)
    assert slept == []
    assert not (tmp_path / "paused_until").exists()


def test_run_forever_pauses_on_rate_limited_stage_error(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    insert_idea(
        conn, title="RATE_LIMIT Weather CLI", category="cli", pitch="p", created_at="2026-01-01"
    )
    stop_file = tmp_path / "STOP"
    config = dataclasses.replace(
        _config(), paths=dataclasses.replace(_config().paths, stop_file=str(stop_file))
    )

    sleeps = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        stop_file.touch()

    run_forever(
        conn, config, workspace_dir=str(tmp_path), state_dir=tmp_path,
        claude_bin=FAKE_CLAUDE, gh_bin=FAKE_GH,
        sleep_fn=fake_sleep, max_iterations=5,
    )
    # The idea stays permanently rate-limited in this contrived test (nothing
    # ever marks it non-"new"), so it just re-triggers the pause each retry --
    # what matters is that at least one sleep used the 5-hour backoff, not the
    # ordinary between-session interval.
    assert len(sleeps) >= 1
    assert sleeps[0] > 60


def test_cli_status_and_report_run_without_error(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "factory.db"
    connect(db_path)  # create an empty, migrated db
    config_path = REPO_ROOT / "config.yaml"
    parser = build_parser()

    args = parser.parse_args(["--config", str(config_path), "--db", str(db_path), "status"])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "Day:" in out
    assert "Current project: none" in out

    args = parser.parse_args(["--config", str(config_path), "--db", str(db_path), "report"])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "project" in out
    assert "Total cost so far" in out


def test_cli_pause_and_resume_toggle_stop_file(tmp_path: Path) -> None:
    stop_file = tmp_path / "STOP"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        (REPO_ROOT / "config.yaml").read_text(encoding="utf-8").replace(
            "stop_file: ./STOP", f"stop_file: {stop_file}"
        ),
        encoding="utf-8",
    )
    parser = build_parser()

    args = parser.parse_args(["--config", str(config_path), "pause"])
    assert args.func(args) == 0
    assert stop_file.exists()

    args = parser.parse_args(["--config", str(config_path), "resume"])
    assert args.func(args) == 0
    assert not stop_file.exists()
