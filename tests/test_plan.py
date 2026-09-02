from __future__ import annotations

import os
import random
import time
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone

import pytest

from busy_profile.plan import PlannedCommit, plan_commits

NOW = datetime(2026, 8, 31, 12, 0, 0, tzinfo=UTC)


def plan(
    days: int, commits: int, *, seed: int = 0, now: datetime = NOW
) -> list[PlannedCommit]:
    return plan_commits(days, commits, now=now, rng=random.Random(seed))


def timestamps_of(commits: list[PlannedCommit]) -> list[datetime]:
    return [commit.timestamp for commit in commits]


def test_plans_the_requested_number_of_commits() -> None:
    assert len(plan(365, 2500)) == 2500


def test_first_commit_lands_exactly_days_ago() -> None:
    assert plan(30, 50)[0].timestamp == NOW - timedelta(days=30)


def test_timestamps_are_sorted_and_within_the_window() -> None:
    stamps = timestamps_of(plan(90, 400, seed=1))
    assert stamps == sorted(stamps)
    assert stamps[0] >= NOW - timedelta(days=90)
    assert stamps[-1] <= NOW


def test_single_commit_is_the_initial_commit() -> None:
    planned = plan(7, 1)

    assert len(planned) == 1
    assert planned[0].timestamp == NOW - timedelta(days=7)


def test_every_commit_gets_a_message() -> None:
    planned = plan(30, 200)

    assert all(commit.message for commit in planned)
    assert all(commit.message[0].isupper() for commit in planned)
    assert all(commit.message.endswith(".") for commit in planned)
    assert all("\n" not in commit.message for commit in planned)


def test_messages_vary_between_commits() -> None:
    assert len({commit.message for commit in plan(30, 200)}) > 150


def test_sub_second_precision_is_dropped() -> None:
    planned = plan(30, 20, now=NOW.replace(microsecond=123456))
    assert all(commit.timestamp.microsecond == 0 for commit in planned)


def test_same_seed_gives_the_same_history() -> None:
    assert plan(365, 100, seed=42) == plan(365, 100, seed=42)


def test_different_seeds_give_different_histories() -> None:
    assert plan(365, 100, seed=1) != plan(365, 100, seed=2)


def test_commits_spread_across_many_days() -> None:
    per_day = Counter(commit.timestamp.date() for commit in plan(365, 2500, seed=7))
    assert len(per_day) > 300


def test_an_aware_now_keeps_its_zone() -> None:
    ten_hours_east = timezone(timedelta(hours=10))
    planned = plan(30, 50, now=NOW.astimezone(ten_hours_east))

    assert all(commit.timestamp.tzinfo is ten_hours_east for commit in planned)


@pytest.fixture
def sydney_clock() -> Iterator[None]:
    """Run the process in a zone that observes daylight saving time.

    Sydney leaves standard time (+10:00) for daylight time (+11:00) on the
    first Sunday in October, which was 4 October in 2026.
    """
    original = os.environ.get("TZ")
    os.environ["TZ"] = "Australia/Sydney"
    time.tzset()
    yield
    if original is None:
        del os.environ["TZ"]
    else:
        os.environ["TZ"] = original
    time.tzset()


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="needs a POSIX clock")
def test_a_naive_now_stamps_each_commit_with_its_own_local_offset(
    sydney_clock: None,
) -> None:
    del sydney_clock
    planned = plan(90, 500, now=datetime(2026, 11, 1, 12, 0, 0))

    assert all(commit.timestamp.tzinfo is not None for commit in planned)
    assert planned[0].timestamp.utcoffset() == timedelta(hours=10)
    assert planned[-1].timestamp.utcoffset() == timedelta(hours=11)
    stamps = timestamps_of(planned)
    assert stamps == sorted(stamps)
