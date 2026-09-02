"""Planning the commits that make up a rewritten history.

Planning is pure: it touches neither the repository nor the filesystem, so a
plan can be shown to the user before they agree to anything.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from busy_profile.text import random_sentence

DEFAULT_DAYS = 365
DEFAULT_COMMITS = 2500

SECONDS_PER_DAY = 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class PlannedCommit:
    """A single commit to write: when it is dated and what it says.

    ``message`` is both the commit message and the entire contents of
    ``random_text`` at that commit, so the two can never drift apart.
    """

    timestamp: datetime
    message: str


def plan_commits(
    days: int,
    commits: int,
    *,
    now: datetime,
    rng: random.Random,
) -> list[PlannedCommit]:
    """Plan ``commits`` commits in date order, spanning the last ``days`` days.

    The first is dated exactly ``now - days``, so the initial commit lands on
    the requested date. The rest are drawn uniformly at random from the open
    interval ``(start, now]``. Every random offset is at least one second, so
    sorting the rest is enough to keep the first commit first.

    Each commit also gets its message here, so ``rng`` is the only source of
    randomness in a rewrite and one seed reproduces a history exactly.

    Pass a naive ``now`` to plan in local time: each commit then carries the UTC
    offset that was in force at its own instant, so a history that spans a
    daylight-saving change is stamped correctly on both sides. An aware ``now``
    keeps its own zone throughout.

    Sub-second precision is dropped, because git records commit times to the
    second and would otherwise not round-trip what is planned here.
    """
    start = now.replace(microsecond=0) - timedelta(days=days)
    span = days * SECONDS_PER_DAY

    def at(seconds: int) -> datetime:
        return (start + timedelta(seconds=seconds)).astimezone(now.tzinfo)

    later = sorted(at(rng.randint(1, span)) for _ in range(commits - 1))

    planned = [PlannedCommit(at(0), random_sentence(rng))]
    planned.extend(PlannedCommit(stamp, random_sentence(rng)) for stamp in later)
    return planned
