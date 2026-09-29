"""The commit gate: every check a change must pass before builder.py is
allowed to `git add -A && commit`. Checks run in order and stop at the first
failure (no point running the test suite on an empty diff).

Secret scanning requires the real `gitleaks` binary. Per plan section 7.6 this
could fall back to a regex scan when gitleaks is missing -- deliberately not
done here: a silently-weaker check is worse than no check, so a missing
binary fails the gate loudly instead.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field

_COMMENT_PREFIXES = ("#", "//", "/*", "*", "*/", "<!--", "-->", '"""', "'''")

# Fallback defaults only, used when a caller doesn't pass an explicit
# test_cmd/lint_cmd. They point at *this* interpreter (factory's own venv),
# which is wrong for a generated project's own dependencies -- builder.py
# must pass the target repo's venv interpreter explicitly once scaffold.py
# exists. `sys.executable -m X` is used instead of a bare "X" so the default
# still resolves even when a venv's bin/ isn't on PATH.
_LANGUAGE_TEST_CMD = {
    "python": [sys.executable, "-m", "pytest", "-q"],
    "node": ["npm", "test"],
}
_LANGUAGE_LINT_CMD = {
    "python": [sys.executable, "-m", "ruff", "check", "."],
    "node": ["npx", "eslint", "."],
}

DEFAULT_MAX_DIFF_LINES = 800
DEFAULT_MAX_BINARY_BYTES = 500 * 1024
DEFAULT_TIMEOUT_SEC = 300


@dataclass(frozen=True)
class GateResult:
    passed: bool
    failed_check: str | None
    reason: str | None
    checks_run: list[str] = field(default_factory=list)


class GateConfigError(ValueError):
    """Raised for a gate misconfiguration (e.g. unsupported language)."""


def _run(
    cmd: list[str], cwd: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC
) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout_sec)


def _git(args: list[str], repo_path: str) -> subprocess.CompletedProcess:
    return _run(["git", *args], repo_path)


def check_diff_nonempty(repo_path: str) -> tuple[bool, str | None]:
    """Stages everything, then checks there is a non-empty, non-whitespace-only diff."""
    _git(["add", "-A"], repo_path)
    raw = _git(["diff", "--cached", "--numstat"], repo_path)
    if not raw.stdout.strip():
        return False, "diff is empty"
    ignoring_whitespace = _git(["diff", "--cached", "-w", "--numstat"], repo_path)
    if not ignoring_whitespace.stdout.strip():
        return False, "diff only touches whitespace"
    return True, None


def check_not_only_gitignore(repo_path: str) -> tuple[bool, str | None]:
    names = _git(["diff", "--cached", "--name-only"], repo_path).stdout.split()
    if names and set(names) == {".gitignore"}:
        return False, "diff only touches .gitignore"
    return True, None


def check_not_only_comments(repo_path: str) -> tuple[bool, str | None]:
    """Fail if every added/removed line in the diff is a comment or blank."""
    patch = _git(["diff", "--cached"], repo_path).stdout
    changed_lines = [
        line[1:].strip()
        for line in patch.splitlines()
        if (line.startswith("+") or line.startswith("-"))
        and not line.startswith(("+++", "---"))
    ]
    real_lines = [line for line in changed_lines if line]
    if not real_lines:
        return True, None  # nothing but blank lines changed; caught by nonempty check instead
    if all(line.startswith(_COMMENT_PREFIXES) for line in real_lines):
        return False, "diff only touches comments"
    return True, None


def check_diff_size_sane(
    repo_path: str,
    max_lines: int = DEFAULT_MAX_DIFF_LINES,
    max_binary_bytes: int = DEFAULT_MAX_BINARY_BYTES,
) -> tuple[bool, str | None]:
    numstat = _git(["diff", "--cached", "--numstat"], repo_path).stdout
    total_lines = 0
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added, removed, path = parts
        if added == "-" or removed == "-":
            size = _run(["git", "cat-file", "-s", f":0:{path}"], repo_path)
            try:
                if int(size.stdout.strip()) > max_binary_bytes:
                    return False, f"binary file too large: {path}"
            except ValueError:
                pass
            continue
        total_lines += int(added) + int(removed)
    if total_lines > max_lines:
        return False, f"diff too large: {total_lines} changed lines (max {max_lines})"
    return True, None


def check_tests_pass(
    repo_path: str, language: str, test_cmd: list[str] | None = None
) -> tuple[bool, str | None]:
    cmd = test_cmd or _LANGUAGE_TEST_CMD.get(language)
    if cmd is None:
        raise GateConfigError(f"no test command configured for language: {language!r}")
    proc = _run(cmd, repo_path)
    if proc.returncode != 0:
        return False, f"tests failed:\n{(proc.stdout + proc.stderr)[-2000:]}"
    return True, None


def check_lint_passes(
    repo_path: str, language: str, lint_cmd: list[str] | None = None
) -> tuple[bool, str | None]:
    cmd = lint_cmd or _LANGUAGE_LINT_CMD.get(language)
    if cmd is None:
        raise GateConfigError(f"no lint command configured for language: {language!r}")
    proc = _run(cmd, repo_path)
    if proc.returncode != 0:
        return False, f"lint failed:\n{(proc.stdout + proc.stderr)[-2000:]}"
    return True, None


def check_type_check_passes(
    repo_path: str, type_check_cmd: list[str] | None
) -> tuple[bool, str | None]:
    """Skipped (passes) when the project has no type checker configured."""
    if type_check_cmd is None:
        return True, None
    proc = _run(type_check_cmd, repo_path)
    if proc.returncode != 0:
        return False, f"type check failed:\n{(proc.stdout + proc.stderr)[-2000:]}"
    return True, None


def check_secret_scan_clean(
    repo_path: str, gitleaks_bin: str | Sequence[str] | None = None
) -> tuple[bool, str | None]:
    """`gitleaks_bin` is normally "gitleaks" (or omitted, resolved via PATH), but
    tests pass a command prefix (e.g. [sys.executable, "fake_gitleaks.py"])."""
    if gitleaks_bin is None:
        resolved = shutil.which("gitleaks")
        if resolved is None:
            return False, "gitleaks is not installed; refusing to fall back to a weaker scan"
        base_cmd = [resolved]
    else:
        base_cmd = [gitleaks_bin] if isinstance(gitleaks_bin, str) else list(gitleaks_bin)
    try:
        proc = _run(
            [*base_cmd, "detect", "--no-git", "--source", repo_path, "--exit-code", "1"], repo_path
        )
    except FileNotFoundError as e:
        return False, f"gitleaks binary not found: {e}"
    if proc.returncode == 0:
        return True, None
    if proc.returncode == 1:
        return False, f"secret scan found potential leaks:\n{proc.stdout[-2000:]}"
    return False, f"secret scan errored (exit {proc.returncode}): {proc.stderr[-500:]}"


def check_acceptance(repo_path: str, acceptance_cmd: str | None) -> tuple[bool, str | None]:
    """Skipped (passes) when the task has no acceptance command of its own."""
    if not acceptance_cmd:
        return True, None
    proc = subprocess.run(
        acceptance_cmd, shell=True, cwd=repo_path, capture_output=True, text=True,
        timeout=DEFAULT_TIMEOUT_SEC,
    )
    if proc.returncode != 0:
        return False, f"acceptance criteria failed:\n{(proc.stdout + proc.stderr)[-2000:]}"
    return True, None


def run_gate(
    repo_path: str,
    *,
    language: str,
    test_cmd: list[str] | None = None,
    lint_cmd: list[str] | None = None,
    type_check_cmd: list[str] | None = None,
    acceptance_cmd: str | None = None,
    gitleaks_bin: str | Sequence[str] | None = None,
    max_diff_lines: int = DEFAULT_MAX_DIFF_LINES,
    max_binary_bytes: int = DEFAULT_MAX_BINARY_BYTES,
) -> GateResult:
    """Run every check in order; stop and report the first failure."""
    checks_run: list[str] = []

    def step(name: str, fn) -> GateResult | None:
        checks_run.append(name)
        ok, reason = fn()
        if not ok:
            return GateResult(passed=False, failed_check=name, reason=reason, checks_run=checks_run)
        return None

    steps = [
        ("diff_nonempty", lambda: check_diff_nonempty(repo_path)),
        ("not_only_gitignore", lambda: check_not_only_gitignore(repo_path)),
        ("not_only_comments", lambda: check_not_only_comments(repo_path)),
        ("tests_pass", lambda: check_tests_pass(repo_path, language, test_cmd)),
        ("lint_passes", lambda: check_lint_passes(repo_path, language, lint_cmd)),
        ("type_check_passes", lambda: check_type_check_passes(repo_path, type_check_cmd)),
        ("secret_scan_clean", lambda: check_secret_scan_clean(repo_path, gitleaks_bin)),
        (
            "diff_size_sane",
            lambda: check_diff_size_sane(repo_path, max_diff_lines, max_binary_bytes),
        ),
        ("acceptance", lambda: check_acceptance(repo_path, acceptance_cmd)),
    ]
    for name, fn in steps:
        failure = step(name, fn)
        if failure is not None:
            return failure

    return GateResult(passed=True, failed_check=None, reason=None, checks_run=checks_run)
