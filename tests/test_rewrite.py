from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from busy_profile.rewrite import (
    TEMP_BRANCH,
    GitError,
    assert_rewritable,
    current_branch,
    rewrite_history,
)
from busy_profile.schedule import generate_timestamps
from busy_profile.text import RANDOM_TEXT_FILE
from tests.conftest import git


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
    now = datetime.now().astimezone()
    timestamps = generate_timestamps(30, 25, now=now, rng=random.Random(0))

    rewrite_history(repo, timestamps)

    assert git(repo, "rev-list", "--count", "HEAD") == "25"
    assert current_branch(repo) == "main"


def test_initial_commit_is_dated_days_ago(repo: Path) -> None:
    now = datetime.now().astimezone()
    timestamps = generate_timestamps(30, 10, now=now, rng=random.Random(0))

    rewrite_history(repo, timestamps)

    author, committer = commit_dates(repo)[0]
    assert author == timestamps[0]
    assert committer == timestamps[0]
    assert author.utcoffset() == timestamps[0].utcoffset()


def test_every_commit_keeps_its_timestamp(repo: Path) -> None:
    now = datetime.now().astimezone()
    timestamps = generate_timestamps(60, 20, now=now, rng=random.Random(3))

    rewrite_history(repo, timestamps)

    assert author_dates(repo) == list(timestamps)


def test_timestamps_survive_a_zero_utc_offset(repo: Path) -> None:
    """Pin the case that only shows up on a UTC machine.

    At a zero offset git writes ``Z`` and Python writes ``+00:00``. Forcing UTC
    here rather than relying on the runner's timezone means this is exercised
    everywhere, not just in CI.
    """
    now = datetime.now(UTC)
    timestamps = generate_timestamps(30, 6, now=now, rng=random.Random(0))

    rewrite_history(repo, timestamps)

    assert author_dates(repo) == list(timestamps)
    assert author_dates(repo)[0].utcoffset() == timedelta(0)


def test_timestamps_survive_a_non_utc_offset(repo: Path) -> None:
    """The mirror of the above, for a machine that is not on UTC."""
    now = datetime.now(timezone(timedelta(hours=10)))
    timestamps = generate_timestamps(30, 6, now=now, rng=random.Random(0))

    rewrite_history(repo, timestamps)

    assert author_dates(repo) == list(timestamps)
    assert author_dates(repo)[0].utcoffset() == timedelta(hours=10)


def test_initial_commit_snapshots_the_working_tree(repo: Path) -> None:
    now = datetime.now().astimezone()
    timestamps = generate_timestamps(10, 5, now=now, rng=random.Random(0))

    rewrite_history(repo, timestamps)

    root = git(repo, "rev-list", "--max-parents=0", "HEAD")
    tracked = git(repo, "ls-tree", "--name-only", "-r", root).splitlines()
    assert sorted(tracked) == ["README.md", RANDOM_TEXT_FILE, "untracked.txt"]
    assert (repo / "README.md").read_text() == "# original\n"


def test_every_commit_contains_a_random_sentence(repo: Path) -> None:
    now = datetime.now().astimezone()
    timestamps = generate_timestamps(30, 15, now=now, rng=random.Random(0))

    rewrite_history(repo, timestamps, rng=random.Random(1))

    contents = random_text_at_each_commit(repo)
    assert len(contents) == 15
    for sentence in contents:
        assert sentence[0].isupper()
        assert sentence.endswith(".")


def test_each_commit_overwrites_rather_than_appends(repo: Path) -> None:
    now = datetime.now().astimezone()
    timestamps = generate_timestamps(30, 20, now=now, rng=random.Random(0))

    rewrite_history(repo, timestamps, rng=random.Random(1))

    for sentence in random_text_at_each_commit(repo):
        assert len(sentence.splitlines()) == 1


def test_sentences_change_between_commits(repo: Path) -> None:
    now = datetime.now().astimezone()
    timestamps = generate_timestamps(30, 40, now=now, rng=random.Random(0))

    rewrite_history(repo, timestamps, rng=random.Random(1))

    contents = random_text_at_each_commit(repo)
    assert len(set(contents)) > 30


def test_working_tree_matches_the_final_commit(repo: Path) -> None:
    now = datetime.now().astimezone()
    timestamps = generate_timestamps(30, 10, now=now, rng=random.Random(0))

    rewrite_history(repo, timestamps, rng=random.Random(1))

    assert (repo / RANDOM_TEXT_FILE).read_text() == git(
        repo, "show", f"HEAD:{RANDOM_TEXT_FILE}"
    ) + "\n"
    assert git(repo, "status", "--porcelain") == ""


def test_same_rng_seed_reproduces_the_same_sentences(
    repo: Path, tmp_path: Path
) -> None:
    now = datetime.now().astimezone()
    timestamps = generate_timestamps(30, 10, now=now, rng=random.Random(0))

    rewrite_history(repo, timestamps, rng=random.Random(99))
    first = random_text_at_each_commit(repo)

    clone = tmp_path / "clone"
    git(tmp_path, "init", "--initial-branch", "main", str(clone))
    git(clone, "config", "user.name", "Test User")
    git(clone, "config", "user.email", "test@example.com")
    rewrite_history(clone, timestamps, rng=random.Random(99))

    assert random_text_at_each_commit(clone) == first


def test_random_text_is_committed_even_when_gitignored(repo: Path) -> None:
    (repo / ".gitignore").write_text(f"{RANDOM_TEXT_FILE}\n")
    now = datetime.now().astimezone()
    timestamps = generate_timestamps(30, 5, now=now, rng=random.Random(0))

    rewrite_history(repo, timestamps, rng=random.Random(1))

    assert len(random_text_at_each_commit(repo)) == 5


def test_commit_message_quotes_the_sentence_the_commit_wrote(repo: Path) -> None:
    now = datetime.now().astimezone()
    timestamps = generate_timestamps(30, 25, now=now, rng=random.Random(0))

    rewrite_history(repo, timestamps, rng=random.Random(1))

    revisions = git(repo, "rev-list", "--reverse", "HEAD").splitlines()
    for revision in revisions:
        message = git(repo, "log", "-1", "--format=%B", revision).strip()
        sentence = git(repo, "show", f"{revision}:{RANDOM_TEXT_FILE}")
        assert message == f"Update {RANDOM_TEXT_FILE} to be {sentence}"


def test_commit_messages_are_single_line_subjects(repo: Path) -> None:
    now = datetime.now().astimezone()
    timestamps = generate_timestamps(30, 20, now=now, rng=random.Random(0))

    rewrite_history(repo, timestamps, rng=random.Random(1))

    for revision in git(repo, "rev-list", "HEAD").splitlines():
        body = git(repo, "log", "-1", "--format=%B", revision).strip()
        assert len(body.splitlines()) == 1
        assert body == git(repo, "log", "-1", "--format=%s", revision)


def test_old_history_is_discarded(repo: Path) -> None:
    original = git(repo, "rev-parse", "HEAD")
    now = datetime.now().astimezone()
    timestamps = generate_timestamps(10, 5, now=now, rng=random.Random(0))

    rewrite_history(repo, timestamps)

    assert original not in git(repo, "rev-list", "HEAD")
    assert git(repo, "branch", "--list", TEMP_BRANCH) == ""


def test_progress_callback_reports_every_commit(repo: Path) -> None:
    now = datetime.now().astimezone()
    timestamps = generate_timestamps(10, 5, now=now, rng=random.Random(0))
    seen: list[tuple[int, int]] = []

    rewrite_history(
        repo, timestamps, on_commit=lambda done, total: seen.append((done, total))
    )

    assert seen == [(1, 5), (2, 5), (3, 5), (4, 5), (5, 5)]


def test_rewrite_works_on_a_repo_with_no_commits(tmp_path: Path) -> None:
    git(tmp_path, "init", "--initial-branch", "main")
    git(tmp_path, "config", "user.name", "Test User")
    git(tmp_path, "config", "user.email", "test@example.com")
    now = datetime.now().astimezone()

    rewrite_history(tmp_path, generate_timestamps(10, 3, now=now, rng=random.Random(0)))

    assert git(tmp_path, "rev-list", "--count", "HEAD") == "3"


def test_rejects_empty_timestamps(repo: Path) -> None:
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


def test_failed_rewrite_restores_the_original_branch(repo: Path) -> None:
    original = git(repo, "rev-parse", "HEAD")
    now = datetime.now().astimezone()
    timestamps = generate_timestamps(10, 5, now=now, rng=random.Random(0))
    git(repo, "config", "user.useConfigOnly", "true")
    git(repo, "config", "--unset", "user.email")
    git(repo, "config", "--unset", "user.name")

    with pytest.raises(GitError):
        rewrite_history(repo, timestamps)

    assert current_branch(repo) == "main"
    assert git(repo, "rev-parse", "HEAD") == original
    assert git(repo, "branch", "--list", TEMP_BRANCH) == ""
    assert not (repo / RANDOM_TEXT_FILE).exists()


def test_naive_timestamps_are_rejected_before_anything_is_written(repo: Path) -> None:
    original = git(repo, "rev-parse", "HEAD")

    with pytest.raises(ValueError, match="timezone-aware"):
        rewrite_history(repo, [datetime.now() - timedelta(days=1)])

    assert current_branch(repo) == "main"
    assert git(repo, "rev-parse", "HEAD") == original
    assert not (repo / RANDOM_TEXT_FILE).exists()
