"""Generation of the commit timestamps that make up a rewritten history."""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime, timedelta

DEFAULT_DAYS = 365
DEFAULT_COMMITS = 2500

SECONDS_PER_DAY = 24 * 60 * 60


def generate_timestamps(
    days: int,
    commits: int,
    *,
    now: datetime,
    rng: random.Random | None = None,
) -> list[datetime]:
    """Return ``commits`` timestamps ascending, spanning the last ``days`` days.

    The first timestamp is always exactly ``now - days`` so that the initial
    commit lands on the requested date. The remaining timestamps are drawn
    uniformly at random from the open interval ``(start, now]``.

    Sub-second precision is dropped, because git records commit times to the
    second and would otherwise not round-trip what we generate here.
    """
    if days < 1:
        raise ValueError(f"days must be >= 1, got {days}")
    if commits < 1:
        raise ValueError(f"commits must be >= 1, got {commits}")

    rng = random.Random() if rng is None else rng
    start = now.replace(microsecond=0) - timedelta(days=days)
    span = days * SECONDS_PER_DAY

    timestamps = [start]
    timestamps.extend(
        start + timedelta(seconds=rng.randint(1, span)) for _ in range(commits - 1)
    )
    timestamps.sort()
    return timestamps


def format_git_date(timestamp: datetime) -> str:
    """Format a timezone-aware datetime the way git's date parser expects."""
    if timestamp.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return timestamp.isoformat()


def commits_per_day(timestamps: Sequence[datetime]) -> Counter[date]:
    """Count how many of ``timestamps`` fall on each calendar day."""
    return Counter(timestamp.date() for timestamp in timestamps)
