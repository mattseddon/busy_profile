from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.fixture
def isolated_git_config(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stop the developer's real git config from leaking into a test."""
    config = tmp_path_factory.mktemp("gitconfig") / "config"
    config.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(config))


@pytest.fixture
def wide_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop rich truncating long paths in captured output.

    Without a real terminal rich falls back to 80 columns, which is narrower
    than the temporary paths these tests use.
    """
    monkeypatch.setenv("COLUMNS", "200")


@pytest.fixture
def repo(tmp_path: Path, isolated_git_config: None) -> Path:
    """A repository with one real commit and one untracked file."""
    del isolated_git_config  # requested for its side effects only
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "--initial-branch", "main")
    git(path, "config", "user.name", "Test User")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "commit.gpgsign", "false")

    (path / "README.md").write_text("# original\n")
    git(path, "add", "README.md")
    git(path, "commit", "--message", "Original commit")

    (path / "untracked.txt").write_text("staged by the rewrite\n")
    return path
