from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from busy_profile import rewrite
from busy_profile.git import GitError
from busy_profile.plan import PlannedCommit, plan_commits
from busy_profile.rewrite import (
    TEMP_BRANCH,
    assert_rewritable,
    current_branch,
    rewrite_history,
)
from busy_profile.text import RANDOM_TEXT_FILE
from tests.conftest import git


def plan_for(
    repo: Path,
    days: int,
    count: int,
    *,
    seed: int = 0,
    now: datetime | None = None,
) -> list[PlannedCommit]:
    """Stage ``repo`` and plan a history for it, as the CLI does."""
    return plan_commits(
        repo,
        days,
        count,
        now=now or datetime.now().astimezone(),
        rng=random.Random(seed),
    )


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


def random_text_at_each_commit(repo: Path) -> list[str]:
    """The full content of ``random_text`` in every commit, oldest first."""
    revisions = git(repo, "rev-list", "--reverse", "HEAD").splitlines()
    return [git(repo, "show", f"{rev}:{RANDOM_TEXT_FILE}") for rev in revisions]


def test_rewrite_produces_the_requested_commits(repo: Path) -> None:
    planned = plan_for(repo, 30, 25, seed=0)

    rewrite_history(repo, planned)

    assert git(repo, "rev-list", "--count", "HEAD") == "25"
    assert current_branch(repo) == "main"


def test_initial_commit_is_dated_days_ago(repo: Path) -> None:
    planned = plan_for(repo, 30, 10, seed=0)

    rewrite_history(repo, planned)

    author, committer = commit_dates(repo)[0]
    assert author == planned[0].timestamp
    assert committer == planned[0].timestamp
    assert author.utcoffset() == planned[0].timestamp.utcoffset()


def test_every_commit_keeps_its_timestamp(repo: Path) -> None:
    planned = plan_for(repo, 60, 20, seed=3)

    rewrite_history(repo, planned)

    assert author_dates(repo) == [commit.timestamp for commit in planned]


def test_timestamps_survive_a_zero_utc_offset(repo: Path) -> None:
    """Pin the case that only shows up on a UTC machine.

    At a zero offset git writes ``Z`` and Python writes ``+00:00``. Forcing UTC
    here rather than relying on the runner's timezone means this is exercised
    everywhere, not just in CI.
    """
    planned = plan_for(repo, 30, 6, seed=0, now=datetime.now(UTC))

    rewrite_history(repo, planned)

    assert author_dates(repo) == [commit.timestamp for commit in planned]
    assert author_dates(repo)[0].utcoffset() == timedelta(0)


def test_timestamps_survive_a_non_utc_offset(repo: Path) -> None:
    """The mirror of the above, for a machine that is not on UTC."""
    planned = plan_for(
        repo, 30, 6, seed=0, now=datetime.now(timezone(timedelta(hours=10)))
    )

    rewrite_history(repo, planned)

    assert author_dates(repo) == [commit.timestamp for commit in planned]
    assert author_dates(repo)[0].utcoffset() == timedelta(hours=10)


def test_initial_commit_snapshots_the_working_tree(repo: Path) -> None:
    planned = plan_for(repo, 10, 5, seed=0)

    rewrite_history(repo, planned)

    root = git(repo, "rev-list", "--max-parents=0", "HEAD")
    tracked = git(repo, "ls-tree", "--name-only", "-r", root).splitlines()
    assert sorted(tracked) == ["README.md", RANDOM_TEXT_FILE, "untracked.txt"]
    assert (repo / "README.md").read_text() == "# original\n"


def test_every_commit_contains_a_random_sentence(repo: Path) -> None:
    planned = plan_for(repo, 30, 15, seed=0)

    rewrite_history(repo, planned)

    contents = random_text_at_each_commit(repo)
    assert len(contents) == 15
    for sentence in contents:
        assert sentence[0].isupper()
        assert sentence.endswith(".")


def test_each_commit_overwrites_rather_than_appends(repo: Path) -> None:
    planned = plan_for(repo, 30, 20, seed=0)

    rewrite_history(repo, planned)

    for sentence in random_text_at_each_commit(repo):
        assert len(sentence.splitlines()) == 1


def test_sentences_change_between_commits(repo: Path) -> None:
    planned = plan_for(repo, 30, 40, seed=0)

    rewrite_history(repo, planned)

    contents = random_text_at_each_commit(repo)
    assert len(set(contents)) > 30


def test_working_tree_matches_the_final_commit(repo: Path) -> None:
    planned = plan_for(repo, 30, 10, seed=0)

    rewrite_history(repo, planned)

    assert (repo / RANDOM_TEXT_FILE).read_text() == git(
        repo, "show", f"HEAD:{RANDOM_TEXT_FILE}"
    ) + "\n"
    assert git(repo, "status", "--porcelain") == ""


def test_same_rng_seed_reproduces_the_same_sentences(
    repo: Path, tmp_path: Path
) -> None:
    """Each repo needs its own plan: entries name blobs in that repo's index."""
    rewrite_history(repo, plan_for(repo, 30, 10, seed=7))
    first = random_text_at_each_commit(repo)

    other = tmp_path / "other"
    git(tmp_path, "init", "--initial-branch", "main", str(other))
    git(other, "config", "user.name", "Test User")
    git(other, "config", "user.email", "test@example.com")
    rewrite_history(other, plan_for(other, 30, 10, seed=7))

    assert random_text_at_each_commit(other) == first


def test_random_text_is_committed_even_when_gitignored(repo: Path) -> None:
    (repo / ".gitignore").write_text(f"{RANDOM_TEXT_FILE}\n")
    planned = plan_for(repo, 30, 5, seed=0)

    rewrite_history(repo, planned)

    assert len(random_text_at_each_commit(repo)) == 5


def test_commit_message_is_exactly_what_the_commit_wrote(repo: Path) -> None:
    planned = plan_for(repo, 30, 25, seed=0)

    rewrite_history(repo, planned)

    revisions = git(repo, "rev-list", "--reverse", "HEAD").splitlines()
    for revision, commit in zip(revisions, planned, strict=True):
        message = git(repo, "log", "-1", "--format=%B", revision).strip()
        assert message == git(repo, "show", f"{revision}:{RANDOM_TEXT_FILE}")
        assert message == commit.message


def test_commit_messages_are_single_line_subjects(repo: Path) -> None:
    planned = plan_for(repo, 30, 20, seed=0)

    rewrite_history(repo, planned)

    for revision in git(repo, "rev-list", "HEAD").splitlines():
        body = git(repo, "log", "-1", "--format=%B", revision).strip()
        assert len(body.splitlines()) == 1
        assert body == git(repo, "log", "-1", "--format=%s", revision)


def test_old_history_is_discarded(repo: Path) -> None:
    original = git(repo, "rev-parse", "HEAD")
    planned = plan_for(repo, 10, 5, seed=0)

    rewrite_history(repo, planned)

    assert original not in git(repo, "rev-list", "HEAD")
    assert git(repo, "branch", "--list", TEMP_BRANCH) == ""


def test_stage_callback_reports_each_stage_in_order(repo: Path) -> None:
    planned = plan_for(repo, 10, 5, seed=0)
    seen: list[str] = []

    rewrite_history(repo, planned, on_stage=seen.append)

    assert seen == [
        "reading git identity",
        "building 5 commits",
        "importing 5 commits",
        "pointing main at the new history",
    ]


def test_history_is_written_as_a_single_packfile(repo: Path) -> None:
    """fast-import packs everything, rather than leaving loose objects behind."""
    planned = plan_for(repo, 30, 50, seed=0)

    rewrite_history(repo, planned)

    counts = dict(
        line.split(": ", 1) for line in git(repo, "count-objects", "-v").splitlines()
    )
    assert int(counts["in-pack"]) > 100
    assert int(counts["packs"]) == 1


def test_history_is_linear_with_a_single_root(repo: Path) -> None:
    planned = plan_for(repo, 30, 20, seed=0)

    rewrite_history(repo, planned)

    assert len(git(repo, "rev-list", "--max-parents=0", "HEAD").splitlines()) == 1
    assert len(git(repo, "rev-list", "--merges", "HEAD").splitlines()) == 0
    assert git(repo, "rev-list", "--count", "HEAD") == "20"


def test_rewrite_works_on_a_repo_with_no_commits(tmp_path: Path) -> None:
    git(tmp_path, "init", "--initial-branch", "main")
    git(tmp_path, "config", "user.name", "Test User")
    git(tmp_path, "config", "user.email", "test@example.com")

    rewrite_history(tmp_path, plan_for(tmp_path, 10, 3))

    assert git(tmp_path, "rev-list", "--count", "HEAD") == "3"


def test_rejects_an_empty_list_of_commits(repo: Path) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        rewrite_history(repo, [])


def test_rejects_detached_head(repo: Path) -> None:
    git(repo, "checkout", "--detach")
    with pytest.raises(GitError, match="detached"):
        assert_rewritable(repo)


def test_rejects_existing_temp_branch(repo: Path) -> None:
    git(repo, "branch", TEMP_BRANCH)
    with pytest.raises(GitError, match="already exists"):
        assert_rewritable(repo)


def test_rejects_a_directory_that_is_not_a_repo(tmp_path: Path) -> None:
    with pytest.raises(GitError):
        assert_rewritable(tmp_path)


def test_missing_git_identity_fails_before_anything_is_touched(repo: Path) -> None:
    original = git(repo, "rev-parse", "HEAD")
    planned = plan_for(repo, 10, 5, seed=0)
    git(repo, "config", "user.useConfigOnly", "true")
    git(repo, "config", "--unset", "user.email")
    git(repo, "config", "--unset", "user.name")

    with pytest.raises(GitError):
        rewrite_history(repo, planned)

    assert current_branch(repo) == "main"
    assert git(repo, "rev-parse", "HEAD") == original
    assert git(repo, "branch", "--list", TEMP_BRANCH) == ""
    assert not (repo / RANDOM_TEXT_FILE).exists()


def test_a_failed_import_leaves_head_and_the_working_tree_alone(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The import is the last thing that can fail, and it must fail safely.

    The previous implementation committed onto an orphan branch and rolled back
    with ``git checkout --force``, which discarded uncommitted work. Importing
    to a temporary ref means a failure here cannot touch HEAD or the tree.
    """
    original = git(repo, "rev-parse", "HEAD")
    planned = plan_for(repo, 10, 5, seed=0)

    def broken_stream(*_: object, **_kwargs: object) -> bytes:
        del _kwargs
        return b"this is not a fast-import stream\n"

    monkeypatch.setattr(rewrite, "_import_stream", broken_stream)

    with pytest.raises(GitError, match="fast-import"):
        rewrite_history(repo, planned)

    assert current_branch(repo) == "main"
    assert git(repo, "rev-parse", "HEAD") == original
    assert (repo / "README.md").read_text() == "# original\n"
    assert git(repo, "branch", "--list", TEMP_BRANCH) == ""


def test_naive_timestamps_are_rejected_before_anything_is_written(repo: Path) -> None:
    original = git(repo, "rev-parse", "HEAD")
    naive = PlannedCommit(datetime.now() - timedelta(days=1), "A sentence.")

    with pytest.raises(ValueError, match="timezone-aware"):
        rewrite_history(repo, [naive])

    assert current_branch(repo) == "main"
    assert git(repo, "rev-parse", "HEAD") == original
    assert not (repo / RANDOM_TEXT_FILE).exists()
