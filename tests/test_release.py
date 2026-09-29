"""Tests for factory.release. All GitHub- and CI-touching calls are faked --
these tests never touch the network."""

import subprocess
import sys
from pathlib import Path

import pytest

from factory.config import load_config
from factory.db import connect, insert_idea, insert_project, insert_task
from factory.release import (
    ReleaseError,
    all_tasks_finished,
    build_release_prompt,
    preflight_check,
    run_release,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"
FAKE_CLAUDE = [sys.executable, str(FIXTURES / "fake_claude.py")]


def _config():
    return load_config(REPO_ROOT / "config.yaml")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _init_repo(tmp_path: Path, *, with_readme: bool = True, with_license: bool = True) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    if with_readme:
        (repo / "README.md").write_text(
            "# Title\n\n## Install\n\n## Usage\n\n## Example\n", encoding="utf-8"
        )
    if with_license:
        (repo / "LICENSE").write_text("MIT\n", encoding="utf-8")
    (repo / "TASKS.md").write_text("# Tasks\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "baseline")
    return repo


def _project_with_idea(conn, tmp_path: Path, repo: Path):
    idea_id = insert_idea(
        conn, title="Cool Tool", category="cli", pitch="Does a cool thing.",
        created_at="2026-01-01",
    )
    project_id = insert_project(
        conn, idea_id=idea_id, repo_name="factory-cool-tool", local_path=str(repo),
        language="python", created_at="2026-01-01",
    )
    idea = {"id": idea_id, "title": "Cool Tool", "category": "cli", "pitch": "Does a cool thing."}
    return project_id, idea


def _ci_green(local_path: str) -> bool:
    return True


def _ci_red(local_path: str) -> bool:
    return False


def _noop_tag(local_path: str, tag: str) -> None:
    pass


def _noop_release(local_path: str, tag: str) -> None:
    pass


def _noop_repo_edit(local_path: str, description: str, topics: list) -> None:
    pass


# --- all_tasks_finished ------------------------------------------------


def test_all_tasks_finished_true_with_no_tasks(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    idea_id = insert_idea(conn, title="T", category="cli", pitch="P", created_at="2026-01-01")
    project_id = insert_project(
        conn, idea_id=idea_id, repo_name="r", local_path="/tmp/r", language="python",
        created_at="2026-01-01",
    )
    assert all_tasks_finished(conn, project_id) is True


def test_all_tasks_finished_true_when_done_and_skipped(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    idea_id = insert_idea(conn, title="T", category="cli", pitch="P", created_at="2026-01-01")
    project_id = insert_project(
        conn, idea_id=idea_id, repo_name="r", local_path="/tmp/r", language="python",
        created_at="2026-01-01",
    )
    insert_task(
        conn, project_id=project_id, seq=1, title="a", description="d", acceptance="a",
        complexity="simple", updated_at="2026-01-01", status="done",
    )
    insert_task(
        conn, project_id=project_id, seq=2, title="b", description="d", acceptance="a",
        complexity="simple", updated_at="2026-01-01", status="skipped",
    )
    assert all_tasks_finished(conn, project_id) is True


@pytest.mark.parametrize("status", ["pending", "in_progress", "failed"])
def test_all_tasks_finished_false_with_unfinished_task(tmp_path: Path, status: str) -> None:
    conn = connect(tmp_path / "factory.db")
    idea_id = insert_idea(conn, title="T", category="cli", pitch="P", created_at="2026-01-01")
    project_id = insert_project(
        conn, idea_id=idea_id, repo_name="r", local_path="/tmp/r", language="python",
        created_at="2026-01-01",
    )
    insert_task(
        conn, project_id=project_id, seq=1, title="a", description="d", acceptance="a",
        complexity="simple", updated_at="2026-01-01", status=status,
    )
    assert all_tasks_finished(conn, project_id) is False


# --- preflight_check -----------------------------------------------------


def test_preflight_check_passes(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    ok, problems = preflight_check(str(repo), check_ci_green_fn=_ci_green)
    assert (ok, problems) == (True, [])


def test_preflight_check_fails_missing_readme_sections(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("# Title\n\nno sections here\n", encoding="utf-8")
    ok, problems = preflight_check(str(repo), check_ci_green_fn=_ci_green)
    assert ok is False
    assert any("Install" in p for p in problems)
    assert any("Usage" in p for p in problems)
    assert any("Example" in p for p in problems)


def test_preflight_check_fails_missing_license(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path, with_license=False)
    ok, problems = preflight_check(str(repo), check_ci_green_fn=_ci_green)
    assert ok is False
    assert any("LICENSE" in p for p in problems)


def test_preflight_check_fails_ci_not_green(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    ok, problems = preflight_check(str(repo), check_ci_green_fn=_ci_red)
    assert ok is False
    assert any("CI" in p for p in problems)


# --- build_release_prompt -------------------------------------------------


def test_build_release_prompt_substitutes_tokens() -> None:
    idea = {"title": "Cool Tool", "pitch": "Does a cool thing."}
    prompt = build_release_prompt(REPO_ROOT / "prompts", idea)
    assert "__PROJECT_TITLE__" not in prompt
    assert "Cool Tool" in prompt
    assert "Does a cool thing." in prompt


# --- run_release -----------------------------------------------------------


def test_run_release_raises_when_tasks_unfinished(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    repo = _init_repo(tmp_path)
    project_id, idea = _project_with_idea(conn, tmp_path, repo)
    insert_task(
        conn, project_id=project_id, seq=1, title="a", description="d", acceptance="a",
        complexity="simple", updated_at="2026-01-01", status="pending",
    )
    with pytest.raises(ReleaseError, match="not all tasks"):
        run_release(
            conn, _config(), project_id=project_id, local_path=str(repo), idea=idea,
            claude_bin=FAKE_CLAUDE, check_ci_green_fn=_ci_green,
            git_tag_and_push_fn=_noop_tag, github_release_create_fn=_noop_release,
            github_repo_edit_fn=_noop_repo_edit,
        )
    row = conn.execute("SELECT status FROM projects WHERE id = ?", (project_id,)).fetchone()
    assert row["status"] == "scaffolded"


def test_run_release_raises_when_preflight_fails(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    repo = _init_repo(tmp_path, with_license=False)
    project_id, idea = _project_with_idea(conn, tmp_path, repo)
    with pytest.raises(ReleaseError, match="pre-flight"):
        run_release(
            conn, _config(), project_id=project_id, local_path=str(repo), idea=idea,
            claude_bin=FAKE_CLAUDE, check_ci_green_fn=_ci_green,
            git_tag_and_push_fn=_noop_tag, github_release_create_fn=_noop_release,
            github_repo_edit_fn=_noop_repo_edit,
        )
    row = conn.execute("SELECT status FROM projects WHERE id = ?", (project_id,)).fetchone()
    assert row["status"] == "scaffolded"


def test_run_release_raises_when_agent_session_fails(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    repo = _init_repo(tmp_path)
    project_id, idea = _project_with_idea(conn, tmp_path, repo)
    idea = {**idea, "pitch": "RATE_LIMIT please"}
    with pytest.raises(ReleaseError, match="rate limited"):
        run_release(
            conn, _config(), project_id=project_id, local_path=str(repo), idea=idea,
            claude_bin=FAKE_CLAUDE, check_ci_green_fn=_ci_green,
            git_tag_and_push_fn=_noop_tag, github_release_create_fn=_noop_release,
            github_repo_edit_fn=_noop_repo_edit,
        )
    row = conn.execute("SELECT status FROM projects WHERE id = ?", (project_id,)).fetchone()
    assert row["status"] == "scaffolded"


def test_run_release_happy_path_marks_shipped(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    repo = _init_repo(tmp_path)
    project_id, idea = _project_with_idea(conn, tmp_path, repo)
    idea = {**idea, "pitch": "RELEASE_OK please"}
    calls: dict = {"tag": None, "release": None, "edit": None}

    def tag_fn(local_path, tag):
        calls["tag"] = (local_path, tag)

    def release_fn(local_path, tag):
        calls["release"] = (local_path, tag)

    def edit_fn(local_path, description, topics):
        calls["edit"] = (local_path, description, topics)

    run_release(
        conn, _config(), project_id=project_id, local_path=str(repo), idea=idea,
        claude_bin=FAKE_CLAUDE, check_ci_green_fn=_ci_green,
        git_tag_and_push_fn=tag_fn, github_release_create_fn=release_fn,
        github_repo_edit_fn=edit_fn,
    )

    row = conn.execute("SELECT status FROM projects WHERE id = ?", (project_id,)).fetchone()
    assert row["status"] == "shipped"
    assert (repo / "CHANGELOG.md").exists()
    assert calls["tag"] == (str(repo), "v0.1.0")
    assert calls["release"] == (str(repo), "v0.1.0")
    assert calls["edit"] is not None


def test_run_release_repo_edit_failure_is_ignored(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    repo = _init_repo(tmp_path)
    project_id, idea = _project_with_idea(conn, tmp_path, repo)
    idea = {**idea, "pitch": "RELEASE_OK please"}

    def failing_edit(local_path, description, topics):
        raise RuntimeError("gh repo edit boom")

    run_release(
        conn, _config(), project_id=project_id, local_path=str(repo), idea=idea,
        claude_bin=FAKE_CLAUDE, check_ci_green_fn=_ci_green,
        git_tag_and_push_fn=_noop_tag, github_release_create_fn=_noop_release,
        github_repo_edit_fn=failing_edit,
    )
    row = conn.execute("SELECT status FROM projects WHERE id = ?", (project_id,)).fetchone()
    assert row["status"] == "shipped"
