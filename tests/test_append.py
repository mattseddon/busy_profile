from __future__ import annotations

import random
from datetime import UTC, datetime
from pathlib import Path

import pytest

from busy_profile.append import (
    append_commits,
    assert_appendable,
    last_commit_time,
    tracked_paths,
)
from busy_profile.git import GitError, StageCallback
from busy_profile.gource import plan_appended_commits, plan_gource_commits
from busy_profile.plan import Move, PlannedCommit
from tests.conftest import author_dates, git, plan_for, replay, rewrite_repo

NOW = datetime(2026, 8, 31, 12, 0, 0, tzinfo=UTC)


def grown(repo: Path, commits: int = 14) -> list[str]:
    """Give ``repo`` a gource history and return its commit shas, oldest first."""
    rewrite_repo(repo, plan_gource_commits(30, commits, now=NOW, rng=random.Random(0)))
    return git(repo, "rev-list", "--reverse", "HEAD").splitlines()


def append_repo(
    repo: Path,
    commits: int,
    *,
    on_stage: StageCallback | None = None,
) -> list[PlannedCommit]:
    """Plan and append ``commits`` to ``repo``, as the CLI does."""
    planned = plan_appended_commits(
        commits,
        since=last_commit_time(repo).astimezone(),
        now=datetime.now().astimezone(),
        rng=random.Random(1),
        existing=tracked_paths(repo),
    )
    append_commits(repo, assert_appendable(repo), planned, on_stage=on_stage)
    return planned


def test_appending_keeps_the_existing_commits(repo: Path) -> None:
    original = grown(repo)

    append_repo(repo, 10)

    shas = git(repo, "rev-list", "--reverse", "HEAD").splitlines()
    assert shas[: len(original)] == original
    assert len(shas) == len(original) + 10


def test_appending_grows_the_tree_that_is_already_there(repo: Path) -> None:
    grown(repo)
    before = {
        path: (repo / path).read_text()
        for path in git(repo, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
    }

    planned = append_repo(repo, 10)

    after = set(git(repo, "ls-tree", "-r", "--name-only", "HEAD").splitlines())
    assert after == set(replay(planned, before))
    assert git(repo, "status", "--porcelain") == ""


def test_appended_commits_are_dated_after_the_old_tip(repo: Path) -> None:
    grown(repo)
    tip = last_commit_time(repo).astimezone()

    planned = append_repo(repo, 10)

    dates = author_dates(repo)[-10:]
    assert dates == [commit.timestamp for commit in planned]
    assert all(date > tip for date in dates)


def test_appending_reports_its_stages(repo: Path) -> None:
    grown(repo)
    seen: list[str] = []

    append_repo(repo, 5, on_stage=seen.append)

    assert seen == [
        "reading git identity",
        "building 5 commits",
        "importing 5 commits",
        "advancing main by 5 commits",
    ]


def test_appending_quotes_paths_with_spaces(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repo / "my notes.txt").write_text("hello\n")
    (repo / "also here.txt").write_text("hello\n")
    git(repo, "add", "-A")
    # Dated in the past so that there is a window to append into.
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-01-01T12:00:00")
    git(repo, "commit", "-q", "-m", "spaces")
    monkeypatch.delenv("GIT_COMMITTER_DATE")

    planned = append_repo(repo, 1)

    (folder,) = {
        change.destination.split("/")[0]
        for change in planned[0].changes
        if isinstance(change, Move)
    }
    tracked = git(repo, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
    assert f"{folder}/my notes.txt" in tracked
    assert f"{folder}/also here.txt" in tracked
    assert f"{folder}/README.md" in tracked


def test_last_commit_time_is_the_committer_time_of_head(repo: Path) -> None:
    planned = plan_for(30, 5)
    rewrite_repo(repo, planned)

    assert last_commit_time(repo).astimezone() == planned[-1].timestamp


def test_tracked_paths_lists_the_index(repo: Path) -> None:
    assert tracked_paths(repo) == ["README.md"]


def test_assert_appendable_rejects_a_dirty_tree(repo: Path) -> None:
    with pytest.raises(GitError, match="uncommitted"):
        assert_appendable(repo)


def test_assert_appendable_rejects_an_unborn_branch(tmp_path: Path) -> None:
    git(tmp_path, "init", "--initial-branch", "main")
    with pytest.raises(GitError, match="no commits"):
        assert_appendable(tmp_path)


def test_assert_appendable_returns_the_branch_when_clean(repo: Path) -> None:
    (repo / "untracked.txt").unlink()
    assert assert_appendable(repo) == "main"
