from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from busy_profile.plan import (
    PlannedCommit,
    commits_per_day,
    format_raw_date,
    plan_commits,
)
from tests.conftest import git

NOW = datetime(2026, 8, 31, 12, 0, 0, tzinfo=UTC)


def timestamps_of(commits: list[PlannedCommit]) -> list[datetime]:
    return [commit.timestamp for commit in commits]


def test_plans_the_requested_number_of_commits() -> None:
    assert len(plan_commits(None, 365, 2500, now=NOW, rng=random.Random(0))) == 2500


def test_first_commit_lands_exactly_days_ago() -> None:
    planned = plan_commits(None, 30, 50, now=NOW, rng=random.Random(0))
    assert planned[0].timestamp == NOW - timedelta(days=30)


def test_timestamps_are_sorted_and_within_the_window() -> None:
    stamps = timestamps_of(plan_commits(None, 90, 400, now=NOW, rng=random.Random(1)))
    assert stamps == sorted(stamps)
    assert stamps[0] >= NOW - timedelta(days=90)
    assert stamps[-1] <= NOW


def test_single_commit_is_the_initial_commit() -> None:
    planned = plan_commits(None, 7, 1, now=NOW, rng=random.Random(0))

    assert len(planned) == 1
    assert planned[0].timestamp == NOW - timedelta(days=7)


def test_every_commit_gets_a_message() -> None:
    planned = plan_commits(None, 30, 200, now=NOW, rng=random.Random(0))

    assert all(commit.message for commit in planned)
    assert all(commit.message[0].isupper() for commit in planned)
    assert all(commit.message.endswith(".") for commit in planned)
    assert all("\n" not in commit.message for commit in planned)


def test_messages_vary_between_commits() -> None:
    planned = plan_commits(None, 30, 200, now=NOW, rng=random.Random(0))

    assert len({commit.message for commit in planned}) > 150


def test_only_the_first_commit_carries_the_staged_tree(repo: Path) -> None:
    planned = plan_commits(repo, 30, 20, now=NOW, rng=random.Random(0))

    assert sorted(entry.path for entry in planned[0].entries) == [
        b"README.md",
        b"untracked.txt",
    ]
    assert all(entry.mode == "100644" for entry in planned[0].entries)
    assert all(len(entry.sha) == 40 for entry in planned[0].entries)
    assert all(commit.entries == () for commit in planned[1:])


def test_no_repo_means_nothing_is_staged(repo: Path) -> None:
    """A preview must not touch the index."""
    before = git(repo, "write-tree")

    planned = plan_commits(None, 30, 5, now=NOW, rng=random.Random(0))

    assert all(commit.entries == () for commit in planned)
    assert git(repo, "write-tree") == before


def test_sub_second_precision_is_dropped() -> None:
    now = NOW.replace(microsecond=123456)
    planned = plan_commits(None, 30, 20, now=now, rng=random.Random(0))
    assert all(commit.timestamp.microsecond == 0 for commit in planned)


def test_same_seed_gives_the_same_history() -> None:
    first = plan_commits(None, 365, 100, now=NOW, rng=random.Random(42))
    second = plan_commits(None, 365, 100, now=NOW, rng=random.Random(42))
    assert first == second


def test_different_seeds_give_different_histories() -> None:
    first = plan_commits(None, 365, 100, now=NOW, rng=random.Random(1))
    second = plan_commits(None, 365, 100, now=NOW, rng=random.Random(2))
    assert first != second


def test_commits_spread_across_many_days() -> None:
    planned = plan_commits(None, 365, 2500, now=NOW, rng=random.Random(7))
    assert len(commits_per_day(planned)) > 300


@pytest.mark.parametrize(("days", "commits"), [(0, 10), (-1, 10), (10, 0), (10, -1)])
def test_rejects_non_positive_arguments(repo: Path, days: int, commits: int) -> None:
    before = git(repo, "write-tree")

    with pytest.raises(ValueError):
        plan_commits(repo, days, commits, now=NOW, rng=random.Random(0))

    assert git(repo, "write-tree") == before


def test_format_raw_date_is_epoch_seconds_and_offset() -> None:
    assert format_raw_date(NOW) == "1788177600 +0000"


def test_format_raw_date_keeps_a_non_utc_offset() -> None:
    ten_hours_east = timezone(timedelta(hours=10))
    assert format_raw_date(NOW.astimezone(ten_hours_east)) == "1788177600 +1000"


def test_format_raw_date_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        format_raw_date(datetime(2026, 8, 31, 12, 0, 0))
