from __future__ import annotations

import random
import subprocess
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import pytest

from busy_profile.git import StageCallback, assert_writable
from busy_profile.plan import PlannedCommit, WriteFile, plan_commits
from busy_profile.rewrite import rewrite_history


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def plan_for(
    days: int,
    count: int,
    *,
    seed: int = 0,
    now: datetime | None = None,
) -> list[PlannedCommit]:
    return plan_commits(
        days,
        count,
        now=now or datetime.now().astimezone(),
        rng=random.Random(seed),
    )


def rewrite_repo(
    repo: Path,
    planned: list[PlannedCommit],
    *,
    on_stage: StageCallback | None = None,
) -> None:
    """Check the repo then rewrite it, as the CLI does."""
    rewrite_history(repo, assert_writable(repo), planned, on_stage=on_stage)


def commit_dates(repo: Path) -> list[tuple[datetime, datetime]]:
    """The (author, committer) date of every commit, oldest first.

    Parsed rather than compared as text: git renders a zero UTC offset as ``Z``
    where Python's ``isoformat`` writes ``+00:00``, so comparing the strings
    would pass in a machine's local timezone and fail on a UTC CI runner.
    Comparing datetimes compares instants, which holds in any timezone.
    """
    log = git(repo, "log", "--reverse", "--format=%aI|%cI")
    return [
        (datetime.fromisoformat(author), datetime.fromisoformat(committer))
        for author, committer in (line.split("|") for line in log.splitlines())
    ]


def author_dates(repo: Path) -> list[datetime]:
    return [author for author, _ in commit_dates(repo)]


def replay(
    commits: Sequence[PlannedCommit], tree: dict[str, str] | None = None
) -> dict[str, str]:
    """Apply a plan to a ``{path: content}`` tree, checking every step.

    A write must not clobber an existing path, a move must have something to
    move, and nothing may already sit where a move lands. ``tree`` is the
    starting point, empty unless given, and is not modified.
    """
    tree = dict(tree or {})

    def under(prefix: str) -> list[str]:
        return [p for p in tree if p == prefix or p.startswith(prefix + "/")]

    for commit in commits:
        for change in commit.changes:
            if isinstance(change, WriteFile):
                assert change.path not in tree, change
                tree[change.path] = change.content
            else:
                moved = under(change.source)
                assert moved, change
                assert not under(change.destination), change
                for path in moved:
                    new_path = change.destination + path[len(change.source) :]
                    tree[new_path] = tree.pop(path)
    return tree


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
    del isolated_git_config
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
