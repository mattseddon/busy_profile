"""Destructive rewriting of a repository's commit history."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from busy_profile.git import (
    IndexEntry,
    StageCallback,
    identity,
    import_stream,
    land,
    run,
    run_raw,
    stager,
)
from busy_profile.plan import PlannedCommit


def rewrite_history(
    repo: Path,
    branch: str,
    commits: Sequence[PlannedCommit],
    *,
    on_stage: StageCallback | None = None,
) -> None:
    """Replace the history of ``branch`` in ``repo`` with the planned ``commits``.

    ``branch`` is what :func:`~busy_profile.git.assert_writable` returned for
    ``repo``; the caller checks the repository once, before asking the user to
    confirm, and hands the answer on rather than having it checked again.

    The working tree is staged and snapshotted into the first commit. Every
    commit then applies its planned changes on top of its parent's tree. The
    previous history is discarded. A failure part-way through leaves the
    repository as it was, apart from the now-staged index.
    """
    stage = stager(on_stage)
    total = f"{len(commits):,}"

    stage("reading git identity")
    who = identity(repo)

    stage("staging the working tree")
    snapshot = _stage_entries(repo)

    stage(f"building {total} commits")
    stream = import_stream(commits, who, snapshot=snapshot)

    land(
        repo,
        branch,
        stream,
        stage,
        importing=f"importing {total} commits",
        pointing=f"pointing {branch} at the new history",
        old_tip=None,
    )


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
