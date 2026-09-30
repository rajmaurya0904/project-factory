"""Tests for factory.builder: the per-task build loop, driven against the
fake `claude` binary and a local bare git remote (no network, no GitHub)."""

import dataclasses
import subprocess
import sys
from pathlib import Path

from factory import builder
from factory.builder import BuildResult, build_task, looks_like_shell_command, run_pending_tasks
from factory.config import load_config
from factory.db import connect, insert_idea, insert_project, insert_task

REPO_ROOT = Path(__file__).resolve().parent.parent
FAKE_CLAUDE = [sys.executable, str(Path(__file__).parent / "fixtures" / "fake_claude.py")]
FAKE_GITLEAKS = [sys.executable, str(Path(__file__).parent / "fixtures" / "fake_gitleaks.py")]


def _config(**limits_overrides):
    cfg = load_config(REPO_ROOT / "config.yaml")
    overrides = {"min_seconds_between_sessions": 0, **limits_overrides}
    return dataclasses.replace(cfg, limits=dataclasses.replace(cfg.limits, **overrides))


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _init_repo_with_remote(tmp_path: Path) -> tuple[Path, Path]:
    """A throwaway git repo (extending test_gate.py's _init_repo pattern) whose
    origin is a local bare repo, so a real `git push` needs no network."""
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-q", str(remote)], check=True, capture_output=True, text=True
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "branch", "-m", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("# repo\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-q", "-u", "origin", "main")
    return repo, remote


def _project(conn, local_path: Path) -> int:
    idea_id = insert_idea(
        conn, title="Weather CLI", category="cli", pitch="p", created_at="2026-01-01"
    )
    return insert_project(
        conn, idea_id=idea_id, repo_name="factory-weather-cli", local_path=str(local_path),
        language="python", created_at="2026-01-01",
    )


def _task(
    conn, project_id: int, *, title: str, complexity: str, acceptance: str = "it works"
) -> dict:
    task_id = insert_task(
        conn, project_id=project_id, seq=1, title=title, description="Do the thing.",
        acceptance=acceptance, complexity=complexity, updated_at="2026-01-01",
    )
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return dict(row)


def _remote_log(remote: Path) -> str:
    return subprocess.run(
        ["git", "log", "--oneline", "main"], cwd=remote, capture_output=True, text=True
    ).stdout


# -- looks_like_shell_command --------------------------------------------


def test_looks_like_shell_command_true_for_bare_command() -> None:
    assert looks_like_shell_command("pytest -q") is True


def test_looks_like_shell_command_false_for_prose() -> None:
    assert looks_like_shell_command("pytest tests/test_core.py passes") is False
    assert looks_like_shell_command("the CLI works as expected") is False
    assert looks_like_shell_command("") is False


# -- successful task -------------------------------------------------------


def test_build_task_success_commits_pushes_and_marks_done(tmp_path: Path) -> None:
    repo, remote = _init_repo_with_remote(tmp_path)
    conn = connect(tmp_path / "factory.db")
    project_id = _project(conn, repo)
    task = _task(conn, project_id, title="BUILD_OK: add feature", complexity="complex")

    result = build_task(
        conn, _config(), project_id=project_id, task=task, local_path=str(repo),
        language="python", gitleaks_bin=FAKE_GITLEAKS, claude_bin=FAKE_CLAUDE,
        state_dir=str(tmp_path / "state"),
    )

    sonnet_model = _config().agent.models["sonnet"]

    assert isinstance(result, BuildResult)
    assert result.status == "done"
    assert result.model_used == sonnet_model
    assert result.commit_sha

    row = conn.execute(
        "SELECT status, commit_sha, model_used, attempts FROM tasks WHERE id = ?", (task["id"],)
    ).fetchone()
    assert row["status"] == "done"
    assert row["commit_sha"] == result.commit_sha
    assert row["model_used"] == sonnet_model
    assert row["attempts"] == 1

    assert "BUILD_OK: add feature" in _remote_log(remote)

    counters = conn.execute(
        "SELECT commits, sessions FROM daily_counters"
    ).fetchall()
    assert sum(r["commits"] for r in counters) == 1
    assert sum(r["sessions"] for r in counters) == 1

    sessions = conn.execute("SELECT model, task_id, log_path FROM sessions").fetchall()
    assert len(sessions) == 1
    assert sessions[0]["model"] == sonnet_model
    assert sessions[0]["task_id"] == task["id"]
    assert Path(sessions[0]["log_path"]).exists()


# -- gate fails twice, no escalation possible ------------------------------


def test_build_task_fails_twice_no_escalation_resets_and_marks_failed(tmp_path: Path) -> None:
    repo, remote = _init_repo_with_remote(tmp_path)
    before_log = _remote_log(remote)
    conn = connect(tmp_path / "factory.db")
    project_id = _project(conn, repo)
    # complex -> sonnet, so should_escalate is never true regardless of config
    task = _task(conn, project_id, title="BUILD_BAD: broken feature", complexity="complex")

    result = build_task(
        conn, _config(), project_id=project_id, task=task, local_path=str(repo),
        language="python", gitleaks_bin=FAKE_GITLEAKS, claude_bin=FAKE_CLAUDE,
        state_dir=str(tmp_path / "state"),
    )

    assert result.status == "failed"
    assert result.commit_sha is None

    row = conn.execute("SELECT status, attempts FROM tasks WHERE id = ?", (task["id"],)).fetchone()
    assert row["status"] == "failed"
    assert row["attempts"] == 1

    # working tree is clean -- reset --hard + clean -fd left no mess behind
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert status.strip() == ""
    assert not (repo / "test_bad.py").exists()

    # nothing was pushed
    assert _remote_log(remote) == before_log

    sessions = conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()
    assert sessions["n"] == 2  # first session + one fix session, both on sonnet


# -- gate fails twice on haiku, escalates to sonnet and succeeds ----------


def test_build_task_escalates_haiku_to_sonnet_after_two_failures(tmp_path: Path) -> None:
    repo, remote = _init_repo_with_remote(tmp_path)
    conn = connect(tmp_path / "factory.db")
    project_id = _project(conn, repo)
    # simple -> haiku; MODEL_GATE marker fails on haiku, succeeds on sonnet
    task = _task(conn, project_id, title="MODEL_GATE: escalation test", complexity="simple")

    result = build_task(
        conn, _config(), project_id=project_id, task=task, local_path=str(repo),
        language="python", gitleaks_bin=FAKE_GITLEAKS, claude_bin=FAKE_CLAUDE,
        state_dir=str(tmp_path / "state"),
    )

    cfg_models = _config().agent.models

    assert result.status == "done"
    # proves the escalated attempt is what succeeded
    assert result.model_used == cfg_models["sonnet"]

    row = conn.execute(
        "SELECT status, model_used, attempts FROM tasks WHERE id = ?", (task["id"],)
    ).fetchone()
    assert row["status"] == "done"
    assert row["model_used"] == cfg_models["sonnet"]
    assert row["attempts"] == 2  # one attempt on haiku, one escalated attempt on sonnet

    assert "MODEL_GATE: escalation test" in _remote_log(remote)

    sessions = conn.execute("SELECT model FROM sessions ORDER BY id").fetchall()
    models_used = [s["model"] for s in sessions]
    # two failed haiku rounds, then one successful sonnet round
    assert models_used == [cfg_models["haiku"], cfg_models["haiku"], cfg_models["sonnet"]]


def test_build_task_no_escalation_when_disabled(tmp_path: Path) -> None:
    repo, remote = _init_repo_with_remote(tmp_path)
    before_log = _remote_log(remote)
    conn = connect(tmp_path / "factory.db")
    project_id = _project(conn, repo)
    task = _task(conn, project_id, title="MODEL_GATE: no escalation", complexity="simple")
    cfg = _config()
    cfg = dataclasses.replace(cfg, agent=dataclasses.replace(cfg.agent, escalate_on_failure=False))

    result = build_task(
        conn, cfg, project_id=project_id, task=task, local_path=str(repo),
        language="python", gitleaks_bin=FAKE_GITLEAKS, claude_bin=FAKE_CLAUDE,
        state_dir=str(tmp_path / "state"),
    )

    assert result.status == "failed"
    assert result.model_used == "haiku"
    assert _remote_log(remote) == before_log
    sessions = conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()
    assert sessions["n"] == 2


# -- agent session itself fails, never reaches the gate --------------------


def test_build_task_agent_failure_handled_like_gate_failure(tmp_path: Path) -> None:
    repo, remote = _init_repo_with_remote(tmp_path)
    before_log = _remote_log(remote)
    conn = connect(tmp_path / "factory.db")
    project_id = _project(conn, repo)
    task = _task(conn, project_id, title="RATE_LIMIT: agent outage", complexity="complex")

    result = build_task(
        conn, _config(), project_id=project_id, task=task, local_path=str(repo),
        language="python", gitleaks_bin=FAKE_GITLEAKS, claude_bin=FAKE_CLAUDE,
        state_dir=str(tmp_path / "state"),
    )

    assert result.status == "rate_limited"
    assert result.reason == "agent session was rate limited"
    assert _remote_log(remote) == before_log

    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert status.strip() == ""

    # Reset to pending, not failed -- a rate limit isn't the task's fault, and
    # marking it failed would burn one of its two attempts for no reason.
    row = conn.execute("SELECT status FROM tasks WHERE id = ?", (task["id"],)).fetchone()
    assert row["status"] == "pending"

    sessions = conn.execute("SELECT exit_code FROM sessions").fetchall()
    assert len(sessions) == 2
    # Recorded as the rate-limit sentinel, not a raw nonzero exit code, so
    # guard.py's failure-streak count doesn't treat a rate limit as a failure.
    assert all(s["exit_code"] == builder.RATE_LIMITED_EXIT_CODE for s in sessions)


# -- run_pending_tasks convenience wrapper ---------------------------------


def test_run_pending_tasks_runs_every_pending_task_in_order(tmp_path: Path) -> None:
    repo, remote = _init_repo_with_remote(tmp_path)
    conn = connect(tmp_path / "factory.db")
    project_id = _project(conn, repo)
    insert_task(
        conn, project_id=project_id, seq=1, title="BUILD_OK: first", description="d",
        acceptance="it works", complexity="complex", updated_at="2026-01-01",
    )
    insert_task(
        conn, project_id=project_id, seq=2, title="BUILD_BAD: second", description="d",
        acceptance="it works", complexity="complex", updated_at="2026-01-01",
    )

    project = {"id": project_id, "local_path": str(repo), "language": "python"}
    results = run_pending_tasks(
        conn, _config(), project=project, gitleaks_bin=FAKE_GITLEAKS, claude_bin=FAKE_CLAUDE,
        state_dir=str(tmp_path / "state"),
    )

    assert [r.status for r in results] == ["done", "failed"]
    remaining = conn.execute(
        "SELECT COUNT(*) AS n FROM tasks WHERE status = 'pending'"
    ).fetchone()
    assert remaining["n"] == 0
