from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from busy_profile import rewrite
from busy_profile.git import TEMP_BRANCH, GitError, current_branch, run_raw
from busy_profile.gource import plan_gource_commits
from busy_profile.plan import RANDOM_TEXT_FILE
from tests.conftest import (
    author_dates,
    commit_dates,
    git,
    plan_for,
    replay,
    rewrite_repo,
)

NOW = datetime(2026, 8, 31, 12, 0, 0, tzinfo=UTC)


def random_text_at_each_commit(repo: Path) -> list[str]:
    """The full content of ``random_text`` in every commit, oldest first."""
    revisions = git(repo, "rev-list", "--reverse", "HEAD").splitlines()
    return [git(repo, "show", f"{rev}:{RANDOM_TEXT_FILE}") for rev in revisions]


def test_rewrite_produces_the_requested_commits(repo: Path) -> None:
    rewrite_repo(repo, plan_for(30, 25))

    assert git(repo, "rev-list", "--count", "HEAD") == "25"
    assert current_branch(repo) == "main"


def test_initial_commit_is_dated_days_ago(repo: Path) -> None:
    planned = plan_for(30, 10)

    rewrite_repo(repo, planned)

    author, committer = commit_dates(repo)[0]
    assert author == planned[0].timestamp
    assert committer == planned[0].timestamp
    assert author.utcoffset() == planned[0].timestamp.utcoffset()


def test_every_commit_keeps_its_timestamp(repo: Path) -> None:
    planned = plan_for(60, 20, seed=3)

    rewrite_repo(repo, planned)

    assert author_dates(repo) == [commit.timestamp for commit in planned]


def test_timestamps_survive_a_zero_utc_offset(repo: Path) -> None:
    """Pin the case that only shows up on a UTC machine.

    At a zero offset git writes ``Z`` and Python writes ``+00:00``. Forcing UTC
    here rather than relying on the runner's timezone means this is exercised
    everywhere, not just in CI.
    """
    planned = plan_for(30, 6, now=datetime.now(UTC))

    rewrite_repo(repo, planned)

    assert author_dates(repo) == [commit.timestamp for commit in planned]
    assert author_dates(repo)[0].utcoffset() == timedelta(0)


def test_timestamps_survive_a_non_utc_offset(repo: Path) -> None:
    """The mirror of the above, for a machine that is not on UTC."""
    planned = plan_for(30, 6, now=datetime.now(timezone(timedelta(hours=10))))

    rewrite_repo(repo, planned)

    assert author_dates(repo) == [commit.timestamp for commit in planned]
    assert author_dates(repo)[0].utcoffset() == timedelta(hours=10)


def test_initial_commit_snapshots_the_working_tree(repo: Path) -> None:
    rewrite_repo(repo, plan_for(10, 5))

    root = git(repo, "rev-list", "--max-parents=0", "HEAD")
    tracked = git(repo, "ls-tree", "--name-only", "-r", root).splitlines()
    assert sorted(tracked) == ["README.md", RANDOM_TEXT_FILE, "untracked.txt"]
    assert (repo / "README.md").read_text() == "# original\n"


def test_only_the_root_commit_adds_the_working_tree(repo: Path) -> None:
    rewrite_repo(repo, plan_for(10, 5))

    *later, _root = git(repo, "rev-list", "HEAD").splitlines()
    assert len(later) == 4
    for revision in later:
        changed = git(repo, "show", "--name-only", "--format=", revision)
        assert changed == RANDOM_TEXT_FILE


def test_every_commit_contains_a_random_sentence(repo: Path) -> None:
    rewrite_repo(repo, plan_for(30, 15))

    contents = random_text_at_each_commit(repo)
    assert len(contents) == 15
    for sentence in contents:
        assert sentence[0].isupper()
        assert sentence.endswith(".")


def test_each_commit_overwrites_rather_than_appends(repo: Path) -> None:
    rewrite_repo(repo, plan_for(30, 20))

    for sentence in random_text_at_each_commit(repo):
        assert len(sentence.splitlines()) == 1


def test_sentences_change_between_commits(repo: Path) -> None:
    rewrite_repo(repo, plan_for(30, 40))

    assert len(set(random_text_at_each_commit(repo))) > 30


def test_working_tree_matches_the_final_commit(repo: Path) -> None:
    rewrite_repo(repo, plan_for(30, 10))

    assert (repo / RANDOM_TEXT_FILE).read_text() == git(
        repo, "show", f"HEAD:{RANDOM_TEXT_FILE}"
    ) + "\n"
    assert git(repo, "status", "--porcelain") == ""


def test_one_plan_can_be_written_to_two_repos(repo: Path, tmp_path: Path) -> None:
    """A plan names no blobs, so it is not tied to the repo it was made for."""
    planned = plan_for(30, 10, seed=7)
    rewrite_repo(repo, planned)

    other = tmp_path / "other"
    git(tmp_path, "init", "--initial-branch", "main", str(other))
    git(other, "config", "user.name", "Test User")
    git(other, "config", "user.email", "test@example.com")
    rewrite_repo(other, planned)

    assert random_text_at_each_commit(other) == random_text_at_each_commit(repo)
    assert author_dates(other) == author_dates(repo)


def test_random_text_is_committed_even_when_gitignored(repo: Path) -> None:
    (repo / ".gitignore").write_text(f"{RANDOM_TEXT_FILE}\n")

    rewrite_repo(repo, plan_for(30, 5))

    assert len(random_text_at_each_commit(repo)) == 5


def test_commit_message_is_exactly_what_the_commit_wrote(repo: Path) -> None:
    planned = plan_for(30, 25)

    rewrite_repo(repo, planned)

    revisions = git(repo, "rev-list", "--reverse", "HEAD").splitlines()
    for revision, commit in zip(revisions, planned, strict=True):
        message = git(repo, "log", "-1", "--format=%B", revision).strip()
        assert message == git(repo, "show", f"{revision}:{RANDOM_TEXT_FILE}")
        assert message == commit.message


def test_commit_messages_are_single_line_subjects(repo: Path) -> None:
    rewrite_repo(repo, plan_for(30, 20))

    for revision in git(repo, "rev-list", "HEAD").splitlines():
        body = git(repo, "log", "-1", "--format=%B", revision).strip()
        assert len(body.splitlines()) == 1
        assert body == git(repo, "log", "-1", "--format=%s", revision)


def test_old_history_is_discarded(repo: Path) -> None:
    original = git(repo, "rev-parse", "HEAD")

    rewrite_repo(repo, plan_for(10, 5))

    assert original not in git(repo, "rev-list", "HEAD")
    assert git(repo, "branch", "--list", TEMP_BRANCH) == ""


def test_stage_callback_reports_each_stage_in_order(repo: Path) -> None:
    seen: list[str] = []

    rewrite_repo(repo, plan_for(10, 5), on_stage=seen.append)

    assert seen == [
        "reading git identity",
        "staging the working tree",
        "building 5 commits",
        "importing 5 commits",
        "pointing main at the new history",
    ]


def test_history_is_written_as_a_single_packfile(repo: Path) -> None:
    """fast-import packs everything, rather than leaving loose objects behind."""
    rewrite_repo(repo, plan_for(30, 50))

    counts = dict(
        line.split(": ", 1) for line in git(repo, "count-objects", "-v").splitlines()
    )
    assert int(counts["in-pack"]) > 100
    assert int(counts["packs"]) == 1


def test_history_is_linear_with_a_single_root(repo: Path) -> None:
    rewrite_repo(repo, plan_for(30, 20))

    assert len(git(repo, "rev-list", "--max-parents=0", "HEAD").splitlines()) == 1
    assert len(git(repo, "rev-list", "--merges", "HEAD").splitlines()) == 0
    assert git(repo, "rev-list", "--count", "HEAD") == "20"


def test_rewrite_works_on_a_repo_with_no_commits(tmp_path: Path) -> None:
    git(tmp_path, "init", "--initial-branch", "main")
    git(tmp_path, "config", "user.name", "Test User")
    git(tmp_path, "config", "user.email", "test@example.com")

    rewrite_repo(tmp_path, plan_for(10, 3))

    assert git(tmp_path, "rev-list", "--count", "HEAD") == "3"


def test_missing_git_identity_fails_before_anything_is_touched(repo: Path) -> None:
    original = git(repo, "rev-parse", "HEAD")
    git(repo, "config", "user.useConfigOnly", "true")
    git(repo, "config", "--unset", "user.email")
    git(repo, "config", "--unset", "user.name")

    with pytest.raises(GitError):
        rewrite_repo(repo, plan_for(10, 5))

    assert current_branch(repo) == "main"
    assert git(repo, "rev-parse", "HEAD") == original
    assert git(repo, "status", "--porcelain") == "?? untracked.txt"
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

    def broken_stream(*_: object, **_kwargs: object) -> bytes:
        del _kwargs
        return b"this is not a fast-import stream\n"

    monkeypatch.setattr(rewrite, "import_stream", broken_stream)

    with pytest.raises(GitError, match="fast-import"):
        rewrite_repo(repo, plan_for(10, 5))

    assert current_branch(repo) == "main"
    assert git(repo, "rev-parse", "HEAD") == original
    assert (repo / "README.md").read_text() == "# original\n"
    assert git(repo, "branch", "--list", TEMP_BRANCH) == ""


def test_an_interrupt_after_the_import_leaves_no_temp_branch(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ctrl-C between the import finishing and the branch moving must not
    leave the temporary branch behind, or the next run would refuse to start."""
    original = git(repo, "rev-parse", "HEAD")

    def interrupt_after_import(repo: Path, *args: str, **kwargs: bytes | None) -> bytes:
        output = run_raw(repo, *args, **kwargs)
        if args[0] == "fast-import":
            raise KeyboardInterrupt
        return output

    monkeypatch.setattr("busy_profile.git.run_raw", interrupt_after_import)

    with pytest.raises(KeyboardInterrupt):
        rewrite_repo(repo, plan_for(10, 5))

    assert git(repo, "rev-parse", "HEAD") == original
    assert git(repo, "branch", "--list", TEMP_BRANCH) == ""


def test_gource_plan_builds_the_planned_tree_around_the_snapshot(repo: Path) -> None:
    """Moving a folder of folders exercises fast-import's directory rename."""
    planned = plan_gource_commits(
        30, 14, now=NOW, rng=random.Random(0), reserved={"README.md", "untracked.txt"}
    )

    rewrite_repo(repo, planned)

    tracked = set(git(repo, "ls-tree", "-r", "--name-only", "HEAD").splitlines())
    expected = replay(planned)
    assert tracked == set(expected) | {"README.md", "untracked.txt"}
    assert RANDOM_TEXT_FILE not in tracked
    assert (repo / "README.md").read_text() == "# original\n"
    for path, content in expected.items():
        assert (repo / path).read_text() == content
    assert git(repo, "rev-list", "--count", "HEAD") == "14"
    assert git(repo, "log", "-1", "--format=%s") == planned[-1].message


def test_gource_moves_show_up_as_renames_in_the_log(repo: Path) -> None:
    planned = plan_gource_commits(30, 5, now=NOW, rng=random.Random(0))

    rewrite_repo(repo, planned)

    statuses = git(repo, "show", "--name-status", "--format=", "HEAD").splitlines()
    assert len(statuses) == 3
    assert all(line.startswith("R100") for line in statuses)
