"""Planning the commits that make up a rewritten history."""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from busy_profile.git import run, run_raw
from busy_profile.text import random_sentence

DEFAULT_DAYS = 365
DEFAULT_COMMITS = 2500

SECONDS_PER_DAY = 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class IndexEntry:
    """One staged file, as ``git ls-files --stage`` reports it.

    The path stays as bytes so that filenames git would otherwise quote survive
    into the import stream untouched.
    """

    mode: str
    sha: str
    path: bytes


@dataclass(frozen=True, slots=True)
class PlannedCommit:
    """A single commit to write: when it is dated, what it says, what it adds.

    ``message`` is both the commit message and the entire contents of
    ``random_text`` at that commit, so the two can never drift apart.

    Only the first commit of a rewrite carries the working tree; every later one
    inherits its parent's tree and replaces a single blob, so ``entries`` is
    empty for all but the first.
    """

    timestamp: datetime
    message: str
    entries: tuple[IndexEntry, ...] = ()


def plan_commits(
    repo: Path | None,
    days: int,
    commits: int,
    *,
    now: datetime,
    rng: random.Random | None = None,
) -> list[PlannedCommit]:
    """Plan ``commits`` commits in date order, spanning the last ``days`` days.

    The first is dated exactly ``now - days``, so the initial commit lands on
    the requested date, and carries a snapshot of ``repo``'s working tree. The
    rest are drawn uniformly at random from the open interval ``(start, now]``.
    Every random offset is at least one second, so sorting the offsets is enough
    to keep the first commit first.

    Each commit also gets its message here, so ``rng`` is the only source of
    randomness in a rewrite and one seed reproduces a history exactly.

    Pass ``repo=None`` to plan the dates without staging anything, which is what
    a preview such as ``--dry-run`` wants.

    Sub-second precision is dropped, because git records commit times to the
    second and would otherwise not round-trip what is planned here.
    """
    if days < 1:
        raise ValueError(f"days must be >= 1, got {days}")
    if commits < 1:
        raise ValueError(f"commits must be >= 1, got {commits}")

    rng = random.Random() if rng is None else rng
    start = now.replace(microsecond=0) - timedelta(days=days)
    span = days * SECONDS_PER_DAY

    snapshot: tuple[IndexEntry, ...] = ()
    if repo is not None:
        snapshot = _stage_entries(repo)

    offsets = sorted(rng.randint(1, span) for _ in range(commits - 1))

    planned = [PlannedCommit(start, random_sentence(rng), snapshot)]
    planned.extend(
        PlannedCommit(start + timedelta(seconds=offset), random_sentence(rng))
        for offset in offsets
    )
    return planned


def _stage_entries(repo: Path) -> tuple[IndexEntry, ...]:
    """Stage everything ``git add -A`` would, and return what is now indexed.

    Reusing the blobs git creates here keeps ``.gitignore`` handling as git's
    problem, and means the import stream never has to carry the contents of the
    working tree; it can reference these blobs by sha instead. ``-z`` avoids the
    quoting git would otherwise apply to unusual filenames.
    """
    run(repo, "add", "-A")
    entries: list[IndexEntry] = []
    for record in run_raw(repo, "ls-files", "--stage", "-z").split(b"\0"):
        if not record:
            continue
        meta, _, path = record.partition(b"\t")
        mode, sha, _stage = meta.split()
        entries.append(IndexEntry(mode.decode(), sha.decode(), path))
    return tuple(entries)


def format_raw_date(timestamp: datetime) -> str:
    """Format a timestamp as ``git fast-import`` raw dates: epoch then offset.

    fast-import has no ISO 8601 date format, so the offset has to be carried
    separately from the instant to keep it out of the commit's stored timezone.
    """
    if timestamp.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return f"{int(timestamp.timestamp())} {timestamp:%z}"


def commits_per_day(commits: Sequence[PlannedCommit]) -> Counter[date]:
    """Count how many of ``commits`` fall on each calendar day."""
    return Counter(commit.timestamp.date() for commit in commits)
