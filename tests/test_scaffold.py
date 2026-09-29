"""Tests for factory.scaffold. github_create_fn is always faked -- these
tests never touch the network or a real GitHub account."""

from pathlib import Path

import pytest

from factory.config import load_config
from factory.db import connect, insert_idea
from factory.scaffold import (
    ScaffoldError,
    pkg_name,
    scaffold_project,
    slugify,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _config():
    return load_config(REPO_ROOT / "config.yaml")


def _idea(conn, title="My Cool Tool", category="cli", pitch="Does a cool thing."):
    idea_id = insert_idea(
        conn, title=title, category=category, pitch=pitch, created_at="2026-01-01"
    )
    return {"id": idea_id, "title": title, "category": category, "pitch": pitch}


def _fake_github_create(calls: list):
    def fn(repo_name, config, dest_dir):
        calls.append((repo_name, dest_dir))
        return f"https://github.com/{config.github.owner}/{repo_name}"

    return fn


def test_slugify_basic() -> None:
    assert slugify("My Cool Tool!") == "my-cool-tool"
    assert slugify("  spaces   everywhere  ") == "spaces-everywhere"
    assert slugify("") == "project"


def test_pkg_name_replaces_dashes_and_handles_leading_digit() -> None:
    assert pkg_name("my-cool-tool") == "my_cool_tool"
    assert pkg_name("3d-viewer") == "p_3d_viewer"


def test_scaffold_python_project_writes_expected_files(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    idea = _idea(conn)
    calls: list = []
    result = scaffold_project(
        conn, _config(), idea, "python", str(tmp_path / "ws"),
        github_create_fn=_fake_github_create(calls),
    )

    dest = Path(result.local_path)
    assert dest.exists()
    assert result.repo_name == "factory-my-cool-tool"
    assert result.language == "python"
    assert result.repo_url == f"https://github.com/rajmaurya0904/{result.repo_name}"
    assert calls == [(result.repo_name, dest)]

    assert (dest / "README.md").exists()
    assert (dest / "CLAUDE.md").exists()
    assert (dest / "LICENSE").exists()
    assert (dest / "pyproject.toml").exists()
    assert (dest / "factory_my_cool_tool" / "__init__.py").exists()
    assert (dest / "tests" / "test_smoke.py").exists()

    pyproject = (dest / "pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "factory-my-cool-tool"' in pyproject
    assert "__PROJECT_SLUG__" not in pyproject  # token fully substituted

    pkg_init = (dest / "factory_my_cool_tool" / "__init__.py").read_text(encoding="utf-8")
    assert "My Cool Tool" in pkg_init
    assert "Does a cool thing." in pkg_init


def test_scaffold_records_project_in_db(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    idea = _idea(conn)
    result = scaffold_project(
        conn, _config(), idea, "python", str(tmp_path / "ws"),
        github_create_fn=_fake_github_create([]),
    )
    row = conn.execute(
        "SELECT idea_id, repo_name, status, language FROM projects WHERE id = ?",
        (result.project_id,),
    ).fetchone()
    assert row["idea_id"] == idea["id"]
    assert row["repo_name"] == result.repo_name
    assert row["status"] == "scaffolded"
    assert row["language"] == "python"


def test_scaffold_makes_initial_git_commit(tmp_path: Path) -> None:
    import subprocess

    conn = connect(tmp_path / "factory.db")
    idea = _idea(conn)
    result = scaffold_project(
        conn, _config(), idea, "python", str(tmp_path / "ws"),
        github_create_fn=_fake_github_create([]),
    )
    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=result.local_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "chore: scaffold project" in log.stdout


def test_scaffold_skip_remote_leaves_repo_url_none(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    idea = _idea(conn)
    result = scaffold_project(
        conn, _config(), idea, "python", str(tmp_path / "ws"), create_remote=False
    )
    assert result.repo_url is None


def test_scaffold_node_project(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    idea = _idea(conn, title="Web Widget", pitch="A widget for the web.")
    result = scaffold_project(
        conn, _config(), idea, "node", str(tmp_path / "ws"), create_remote=False
    )
    dest = Path(result.local_path)
    assert result.language == "node"
    assert (dest / "package.json").exists()
    package_json = (dest / "package.json").read_text(encoding="utf-8")
    assert '"name": "factory-web-widget"' in package_json


def test_scaffold_skill_project_has_skill_md(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    idea = _idea(conn, title="Handy Skill", category="skill", pitch="Helps with a thing.")
    result = scaffold_project(
        conn, _config(), idea, "skill", str(tmp_path / "ws"), create_remote=False
    )
    dest = Path(result.local_path)
    assert (dest / "SKILL.md").exists()
    skill_md = (dest / "SKILL.md").read_text(encoding="utf-8")
    assert "name: factory-handy-skill" in skill_md
    assert (dest / "pyproject.toml").exists()  # still has runnable tests/CI


def test_scaffold_mcp_project_has_server_stub(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    idea = _idea(conn, title="Weather MCP", category="mcp", pitch="Exposes weather data.")
    result = scaffold_project(
        conn, _config(), idea, "mcp", str(tmp_path / "ws"), create_remote=False
    )
    dest = Path(result.local_path)
    assert (dest / "factory_weather_mcp" / "server.py").exists()
    server = (dest / "factory_weather_mcp" / "server.py").read_text(encoding="utf-8")
    assert "Weather MCP" in server


def test_scaffold_unknown_template_raises(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    idea = _idea(conn)
    with pytest.raises(ScaffoldError, match="unknown template"):
        scaffold_project(conn, _config(), idea, "rust", str(tmp_path / "ws"), create_remote=False)


def test_scaffold_duplicate_title_gets_unique_repo_name(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    idea1 = _idea(conn, title="Same Name")
    idea2 = _idea(conn, title="Same Name")
    ws = str(tmp_path / "ws")
    r1 = scaffold_project(conn, _config(), idea1, "python", ws, create_remote=False)
    r2 = scaffold_project(conn, _config(), idea2, "python", ws, create_remote=False)
    assert r1.repo_name == "factory-same-name"
    assert r2.repo_name == "factory-same-name-2"


def test_scaffold_existing_dest_dir_raises(tmp_path: Path) -> None:
    conn = connect(tmp_path / "factory.db")
    idea = _idea(conn)
    ws = tmp_path / "ws"
    (ws / "factory-my-cool-tool").mkdir(parents=True)
    with pytest.raises(ScaffoldError, match="already exists"):
        scaffold_project(conn, _config(), idea, "python", str(ws), create_remote=False)
