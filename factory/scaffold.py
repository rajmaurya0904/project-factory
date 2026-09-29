"""Turns an approved idea into a scaffolded project: copies the matching
template, writes README/CLAUDE.md/LICENSE, makes the first commit, and
(optionally) creates and pushes the GitHub repo.

GitHub creation is injectable (`github_create_fn`) so tests never touch the
network -- the same pattern agent.py and gate.py use for their fake binaries.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from factory.config import Config
from factory.guard import is_allowlisted_repo

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

_PYTHON_META = {
    "test_cmd": "pytest -q",
    "lint_cmd": "ruff check .",
    "install_cmd": 'pip install -e ".[dev]"',
}
_TEMPLATE_META = {
    "python": _PYTHON_META,
    "skill": _PYTHON_META,
    "mcp": _PYTHON_META,
    "node": {"test_cmd": "npm test", "lint_cmd": "npm run lint", "install_cmd": "npm install"},
}

_LANGUAGE_BY_TEMPLATE = {
    "python": "python",
    "skill": "python",
    "mcp": "python",
    "node": "node",
}


class ScaffoldError(ValueError):
    """Raised for a bad scaffold request (unknown template, existing dest, ...)."""


@dataclass(frozen=True)
class ScaffoldedProject:
    project_id: int
    repo_name: str
    repo_url: str | None
    local_path: str
    language: str


def slugify(title: str) -> str:
    """Lowercase, alnum-and-dash slug. "My Cool Tool!" -> "my-cool-tool"."""
    lowered = title.lower()
    dashed = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return dashed or "project"


def pkg_name(slug: str) -> str:
    """A valid Python identifier for the package dir: dashes -> underscores,
    prefixed if it would otherwise start with a digit."""
    pkg = slug.replace("-", "_")
    if pkg[:1].isdigit():
        pkg = f"p_{pkg}"
    return pkg


def _unique_repo_name(conn, base_name: str) -> str:
    """Append -2, -3, ... if base_name is already taken."""
    name = base_name
    suffix = 2
    while conn.execute("SELECT 1 FROM projects WHERE repo_name = ?", (name,)).fetchone():
        name = f"{base_name}-{suffix}"
        suffix += 1
    return name


def _copy_template(template_dir: Path, dest_dir: Path, tokens: dict[str, str]) -> None:
    """Copy every file from template_dir to dest_dir, substituting tokens in
    both directory/file names and (for text files) file contents."""
    for src in template_dir.rglob("*"):
        rel = src.relative_to(template_dir)
        rel_str = str(rel)
        for token, value in tokens.items():
            rel_str = rel_str.replace(token, value)
        dest = dest_dir / rel_str

        if src.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            continue

        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            text = src.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            shutil.copyfile(src, dest)
            continue
        for token, value in tokens.items():
            text = text.replace(token, value)
        dest.write_text(text, encoding="utf-8")


def _write_readme(dest_dir: Path, title: str, pitch: str, meta: dict[str, str]) -> None:
    (dest_dir / "README.md").write_text(
        f"# {title}\n\n"
        f"{pitch}\n\n"
        f"## Install\n\n```bash\n{meta['install_cmd']}\n```\n\n"
        f"## Usage\n\nTODO: fill in as the build loop lands the core feature.\n\n"
        f"## Example\n\nTODO.\n\n"
        f"## FAQ\n\nTODO.\n\n"
        f"## License\n\nMIT -- see [LICENSE](LICENSE).\n",
        encoding="utf-8",
    )


def _write_claude_md(dest_dir: Path, meta: dict[str, str]) -> None:
    (dest_dir / "CLAUDE.md").write_text(
        "# Project conventions\n\n"
        f"- Install: `{meta['install_cmd']}`\n"
        f"- Test: `{meta['test_cmd']}`\n"
        f"- Lint: `{meta['lint_cmd']}`\n\n"
        "One task = one small, real commit. No empty or whitespace-only diffs.\n"
        "Add or update a test alongside every feature change.\n",
        encoding="utf-8",
    )


def _write_license(dest_dir: Path, owner: str, year: int) -> None:
    (dest_dir / "LICENSE").write_text(
        "MIT License\n\n"
        f"Copyright (c) {year} {owner}\n\n"
        'Permission is hereby granted, free of charge, to any person obtaining a copy\n'
        'of this software and associated documentation files (the "Software"), to deal\n'
        "in the Software without restriction, including without limitation the rights\n"
        "to use, copy, modify, merge, publish, distribute, sublicense, and/or sell\n"
        "copies of the Software, and to permit persons to whom the Software is\n"
        "furnished to do so, subject to the following conditions:\n\n"
        "The above copyright notice and this permission notice shall be included in all\n"
        "copies or substantial portions of the Software.\n\n"
        'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR\n'
        "IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,\n"
        "FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE\n"
        "AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER\n"
        "LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,\n"
        "OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE\n"
        "SOFTWARE.\n",
        encoding="utf-8",
    )


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _init_git_and_commit(dest_dir: Path) -> None:
    _git(["init", "-q"], dest_dir)
    _git(["branch", "-m", "main"], dest_dir)
    # Local (not global) identity: must not depend on the host's git config,
    # and must not touch the operator's own global config either.
    _git(["config", "user.email", "factory@project-factory.local"], dest_dir)
    _git(["config", "user.name", "project-factory bot"], dest_dir)
    _git(["add", "-A"], dest_dir)
    _git(["commit", "-q", "-m", "chore: scaffold project"], dest_dir)


def default_github_create(repo_name: str, config: Config, dest_dir: Path) -> str:
    """Real implementation: create the GitHub repo and push. Not used in tests."""
    full_name = f"{config.github.owner}/{repo_name}"
    subprocess.run(
        [
            "gh",
            "repo",
            "create",
            full_name,
            f"--{config.github.visibility}",
            "--source",
            str(dest_dir),
            "--remote",
            "origin",
            "--push",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return f"https://github.com/{full_name}"


def scaffold_project(
    conn,
    config: Config,
    idea: dict,
    template: str,
    workspace_dir: str,
    *,
    create_remote: bool = True,
    github_create_fn: Callable[[str, Config, Path], str] | None = None,
) -> ScaffoldedProject:
    """Scaffold `idea` (a dict with id/title/category/pitch) using `template`
    ("python"|"node"|"skill"|"mcp"), commit it, and record it in the DB."""
    template_dir = TEMPLATES_DIR / template
    if not template_dir.is_dir():
        raise ScaffoldError(f"unknown template: {template!r}")
    language = _LANGUAGE_BY_TEMPLATE[template]
    meta = _TEMPLATE_META[template]

    base_name = f"{config.github.repo_prefix or 'factory-'}{slugify(idea['title'])}"
    repo_name = _unique_repo_name(conn, base_name)
    if not is_allowlisted_repo(repo_name, config):
        raise ScaffoldError(f"generated repo name is not allowlisted: {repo_name!r}")

    dest_dir = Path(workspace_dir) / repo_name
    if dest_dir.exists():
        raise ScaffoldError(f"destination already exists: {dest_dir}")
    dest_dir.mkdir(parents=True)

    tokens = {
        "__PROJECT_SLUG__": repo_name,
        "__PROJECT_PKG__": pkg_name(repo_name),
        "__PROJECT_TITLE__": idea["title"],
        "__PROJECT_PITCH__": idea["pitch"],
    }
    _copy_template(template_dir, dest_dir, tokens)
    _write_readme(dest_dir, idea["title"], idea["pitch"], meta)
    _write_claude_md(dest_dir, meta)
    _write_license(dest_dir, config.github.owner, datetime.now(UTC).year)
    _init_git_and_commit(dest_dir)

    repo_url = None
    if create_remote:
        create_fn = github_create_fn or default_github_create
        repo_url = create_fn(repo_name, config, dest_dir)

    from factory.db import insert_project  # local import: avoid a hard db<->scaffold cycle

    project_id = insert_project(
        conn,
        idea_id=idea["id"],
        repo_name=repo_name,
        repo_url=repo_url,
        local_path=str(dest_dir),
        language=language,
        created_at=datetime.now(UTC).isoformat(),
    )
    return ScaffoldedProject(
        project_id=project_id,
        repo_name=repo_name,
        repo_url=repo_url,
        local_path=str(dest_dir),
        language=language,
    )
