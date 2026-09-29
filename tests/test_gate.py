"""Tests for factory.gate: each check function, plus the full run_gate pipeline."""

import subprocess
import sys
from pathlib import Path

import pytest

from factory.gate import (
    GateConfigError,
    check_acceptance,
    check_diff_nonempty,
    check_diff_size_sane,
    check_not_only_comments,
    check_not_only_gitignore,
    check_secret_scan_clean,
    check_tests_pass,
    check_type_check_passes,
    run_gate,
)

FAKE_GITLEAKS = str(Path(__file__).parent / "fixtures" / "fake_gitleaks.py")
_GITLEAKS_CMD = [sys.executable, FAKE_GITLEAKS]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "main.py").write_text("x = 1\n")
    (repo / ".gitignore").write_text("*.pyc\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "baseline")
    return repo


def _stage(repo: Path) -> None:
    _git(repo, "add", "-A")


def test_check_diff_nonempty_fails_when_no_changes(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    ok, reason = check_diff_nonempty(str(repo))
    assert ok is False
    assert reason == "diff is empty"


def test_check_diff_nonempty_fails_when_whitespace_only(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "main.py").write_text("x = 1   \n")
    ok, reason = check_diff_nonempty(str(repo))
    assert ok is False
    assert reason == "diff only touches whitespace"


def test_check_diff_nonempty_passes_with_real_change(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "main.py").write_text("x = 2\n")
    ok, reason = check_diff_nonempty(str(repo))
    assert (ok, reason) == (True, None)


def test_check_not_only_gitignore_fails(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / ".gitignore").write_text("*.pyc\n*.log\n")
    _stage(repo)
    ok, reason = check_not_only_gitignore(str(repo))
    assert ok is False
    assert "gitignore" in reason


def test_check_not_only_gitignore_passes_with_other_files(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "main.py").write_text("x = 2\n")
    _stage(repo)
    ok, reason = check_not_only_gitignore(str(repo))
    assert (ok, reason) == (True, None)


def test_check_not_only_comments_fails(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "main.py").write_text("x = 1\n# just a comment\n")
    _stage(repo)
    ok, reason = check_not_only_comments(str(repo))
    assert ok is False
    assert "comments" in reason


def test_check_not_only_comments_passes_with_real_code(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "main.py").write_text("x = 1\ny = 2\n")
    _stage(repo)
    ok, reason = check_not_only_comments(str(repo))
    assert (ok, reason) == (True, None)


def test_check_diff_size_sane_fails_when_too_large(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "main.py").write_text("\n".join(f"line{i} = {i}" for i in range(500)))
    _stage(repo)
    ok, reason = check_diff_size_sane(str(repo), max_lines=100)
    assert ok is False
    assert "too large" in reason


def test_check_diff_size_sane_passes_when_small(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "main.py").write_text("x = 2\n")
    _stage(repo)
    ok, reason = check_diff_size_sane(str(repo), max_lines=100)
    assert (ok, reason) == (True, None)


def test_check_tests_pass_runs_pytest(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    ok, reason = check_tests_pass(str(repo), "python")
    assert (ok, reason) == (True, None)


def test_check_tests_pass_fails_on_failing_test(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "test_bad.py").write_text("def test_bad():\n    assert False\n")
    ok, reason = check_tests_pass(str(repo), "python")
    assert ok is False
    assert "tests failed" in reason


def test_check_tests_pass_unsupported_language_raises(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    with pytest.raises(GateConfigError, match="ruby"):
        check_tests_pass(str(repo), "ruby")


def test_check_type_check_passes_skipped_when_not_configured(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    ok, reason = check_type_check_passes(str(repo), None)
    assert (ok, reason) == (True, None)


def test_check_type_check_passes_fails_on_nonzero_exit(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    fail_cmd = [sys.executable, "-c", "import sys; sys.exit(1)"]
    ok, reason = check_type_check_passes(str(repo), fail_cmd)
    assert ok is False
    assert "type check failed" in reason


def test_check_secret_scan_clean_fails_loud_when_gitleaks_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: None)
    ok, reason = check_secret_scan_clean(str(repo), gitleaks_bin=None)
    assert ok is False
    assert "not installed" in reason


def test_check_secret_scan_clean_detects_planted_secret(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "leaky.py").write_text("TOKEN = 'PLANT_SECRET'\n")
    ok, reason = check_secret_scan_clean(str(repo), gitleaks_bin=_GITLEAKS_CMD)
    assert ok is False
    assert "leak" in reason


def test_check_secret_scan_clean_passes_when_no_secret(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    ok, reason = check_secret_scan_clean(str(repo), gitleaks_bin=_GITLEAKS_CMD)
    assert (ok, reason) == (True, None)


def test_check_acceptance_skipped_when_no_command(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    ok, reason = check_acceptance(str(repo), None)
    assert (ok, reason) == (True, None)


def test_check_acceptance_fails_on_nonzero_exit(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    ok, reason = check_acceptance(str(repo), "exit 1")
    assert ok is False
    assert "acceptance criteria failed" in reason


def test_run_gate_stops_at_first_failure(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    result = run_gate(str(repo), language="python")
    assert result.passed is False
    assert result.failed_check == "diff_nonempty"
    assert result.checks_run == ["diff_nonempty"]


def test_run_gate_passes_full_pipeline(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "main.py").write_text("x = 1\n\n\ndef add(a, b):\n    return a + b\n")
    (repo / "test_main.py").write_text(
        "from main import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
    )
    result = run_gate(str(repo), language="python", gitleaks_bin=_GITLEAKS_CMD)
    assert result.passed is True
    assert result.checks_run == [
        "diff_nonempty",
        "not_only_gitignore",
        "not_only_comments",
        "tests_pass",
        "lint_passes",
        "type_check_passes",
        "secret_scan_clean",
        "diff_size_sane",
        "acceptance",
    ]


def test_run_gate_missing_gitleaks_fails_loud_not_silently(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "main.py").write_text("x = 2\n")
    (repo / "test_main.py").write_text("def test_ok():\n    assert True\n")
    result = run_gate(str(repo), language="python", gitleaks_bin=["/nonexistent/gitleaks-binary"])
    assert result.passed is False
    assert result.failed_check == "secret_scan_clean"
