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

RANDOM_TEXT_FILE = "random_text"


@dataclass(frozen=True, slots=True)
class WriteFile:
    """Create or overwrite ``path`` so that it holds exactly ``content``."""

    path: str
    content: str


@dataclass(frozen=True, slots=True)
class Move:
    """Rename a file, or a whole folder, from ``source`` to ``destination``."""

    source: str
    destination: str


Change = WriteFile | Move


@dataclass(frozen=True, slots=True)
class PlannedCommit:
    """A single commit to write: when it is dated, what it says, what it changes.

    Paths in ``changes`` are relative to the repository root and use ``/``.
    """

    timestamp: datetime
    message: str
    changes: tuple[Change, ...]


def plan_timestamps(
    days: int,
    commits: int,
    *,
    now: datetime,
    rng: random.Random,
) -> list[datetime]:
    """Date ``commits`` commits in order, spanning the last ``days`` days.

    The first is dated exactly ``now - days``, so the initial commit lands on
    the requested date. The rest are drawn uniformly at random from the open
    interval ``(start, now]``. Every random offset is at least one second, so
    sorting the rest is enough to keep the first commit first.

    Pass a naive ``now`` to plan in local time: each commit then carries the UTC
    offset that was in force at its own instant, so a history that spans a
    daylight-saving change is stamped correctly on both sides. An aware ``now``
    keeps its own zone throughout.

    Sub-second precision is dropped, because git records commit times to the
    second and would otherwise not round-trip what is planned here.
    """
    start = now.replace(microsecond=0) - timedelta(days=days)
    later = timestamps_after(start, commits - 1, now=now, rng=rng)
    return [start.astimezone(now.tzinfo), *later]


def timestamps_after(
    start: datetime,
    count: int,
    *,
    now: datetime,
    rng: random.Random,
) -> list[datetime]:
    """Draw ``count`` timestamps uniformly from ``(start, now]``, in order.

    ``start`` and ``now`` must either both be naive or both be aware; the
    results take ``now``'s zone, with the same local-time treatment as
    :func:`plan_timestamps`. ``start`` must be at least a second before ``now``.
    """
    start = start.replace(microsecond=0)
    span = int((now.replace(microsecond=0) - start).total_seconds())

    def at(seconds: int) -> datetime:
        return (start + timedelta(seconds=seconds)).astimezone(now.tzinfo)

    return sorted(at(rng.randint(1, span)) for _ in range(count))


def plan_commits(
    days: int,
    commits: int,
    *,
    now: datetime,
    rng: random.Random,
) -> list[PlannedCommit]:
    """Plan a history in which every commit writes a sentence to ``random_text``.

    Each message is also the entire content of the file at that commit, so the
    two can never drift apart. Dates come from :func:`plan_timestamps` and are
    drawn before any sentence, so ``rng`` is the only source of randomness and
    one seed reproduces a history exactly.
    """
    planned: list[PlannedCommit] = []
    for stamp in plan_timestamps(days, commits, now=now, rng=rng):
        sentence = random_sentence(rng)
        changes = (WriteFile(RANDOM_TEXT_FILE, f"{sentence}\n"),)
        planned.append(PlannedCommit(stamp, sentence, changes))
    return planned
