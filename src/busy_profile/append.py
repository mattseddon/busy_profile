"""Adding commits on top of a repository's existing history."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from busy_profile.git import (
    GitError,
    StageCallback,
    assert_writable,
    commit_count,
    identity,
    import_stream,
    land,
    run,
    run_raw,
    stager,
)
from busy_profile.plan import PlannedCommit


def last_commit_time(repo: Path) -> datetime:
    """When HEAD was committed, as a naive local time like ``datetime.now()``."""
    return datetime.fromtimestamp(int(run(repo, "log", "-1", "--format=%ct")))


def tracked_paths(repo: Path) -> list[str]:
    """Every path in the index, relative to the repository root.

    Decoded with ``surrogateescape`` so that a filename git stores as arbitrary
    bytes round-trips through the plan and back into the import stream.
    """
    records = run_raw(repo, "ls-files", "-z").split(b"\0")
    return [record.decode(errors="surrogateescape") for record in records if record]


def assert_appendable(repo: Path) -> str:
    """Return the checked out branch, or raise :class:`GitError`.

    Appending needs everything a rewrite needs, plus a commit to build on and
    a clean working tree: the new tip is checked out with ``reset --hard``,
    which would otherwise discard uncommitted work.
    """
    branch = assert_writable(repo)
    if not commit_count(repo):
        reason = (
            f"branch {branch!r} has no commits to append to; "
            "run without --append-commits to create a history"
        )
        raise GitError(reason)
    if run(repo, "status", "--porcelain"):
        reason = (
            "the working tree has uncommitted changes; "
            "commit or stash them before appending"
        )
        raise GitError(reason)
    return branch


def append_commits(
    repo: Path,
    branch: str,
    commits: Sequence[PlannedCommit],
    *,
    on_stage: StageCallback | None = None,
) -> None:
    """Add the planned ``commits`` on top of the current tip of ``branch``.

    ``branch`` is what :func:`assert_appendable` returned for ``repo``. The
    existing history is kept; the first new commit has the old tip as its
    parent, and the branch is only moved if the tip is still where it was when
    the import began.
    """
    stage = stager(on_stage)
    total = f"{len(commits):,}"

    stage("reading git identity")
    who = identity(repo)
    tip = run(repo, "rev-parse", "HEAD")

    stage(f"building {total} commits")
    stream = import_stream(commits, who, parent=tip)

    land(
        repo,
        branch,
        stream,
        stage,
        importing=f"importing {total} commits",
        pointing=f"advancing {branch} by {total} commits",
        old_tip=tip,
    )
