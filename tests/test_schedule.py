from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from busy_profile.schedule import (
    commits_per_day,
    format_git_date,
    generate_timestamps,
)

NOW = datetime(2026, 8, 31, 12, 0, 0, tzinfo=UTC)


def test_generates_requested_number_of_commits() -> None:
    assert len(generate_timestamps(365, 2500, now=NOW, rng=random.Random(0))) == 2500


def test_first_commit_lands_exactly_days_ago() -> None:
    timestamps = generate_timestamps(30, 50, now=NOW, rng=random.Random(0))
    assert timestamps[0] == NOW - timedelta(days=30)


def test_timestamps_are_sorted_and_within_the_window() -> None:
    timestamps = generate_timestamps(90, 400, now=NOW, rng=random.Random(1))
    assert timestamps == sorted(timestamps)
    assert timestamps[0] >= NOW - timedelta(days=90)
    assert timestamps[-1] <= NOW


def test_single_commit_is_the_initial_commit() -> None:
    assert generate_timestamps(7, 1, now=NOW, rng=random.Random(0)) == [
        NOW - timedelta(days=7)
    ]


def test_same_seed_gives_the_same_history() -> None:
    first = generate_timestamps(365, 100, now=NOW, rng=random.Random(42))
    second = generate_timestamps(365, 100, now=NOW, rng=random.Random(42))
    assert first == second


def test_different_seeds_give_different_histories() -> None:
    first = generate_timestamps(365, 100, now=NOW, rng=random.Random(1))
    second = generate_timestamps(365, 100, now=NOW, rng=random.Random(2))
    assert first != second


def test_commits_spread_across_many_days() -> None:
    timestamps = generate_timestamps(365, 2500, now=NOW, rng=random.Random(7))
    # A uniform draw of 2500 over 365 days should touch nearly every day.
    assert len(commits_per_day(timestamps)) > 300


@pytest.mark.parametrize(("days", "commits"), [(0, 10), (-1, 10), (10, 0), (10, -1)])
def test_rejects_non_positive_arguments(days: int, commits: int) -> None:
    with pytest.raises(ValueError):
        generate_timestamps(days, commits, now=NOW, rng=random.Random(0))


def test_format_git_date_is_iso_with_offset() -> None:
    assert format_git_date(NOW) == "2026-08-31T12:00:00+00:00"


def test_format_git_date_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        format_git_date(datetime(2026, 8, 31, 12, 0, 0))
