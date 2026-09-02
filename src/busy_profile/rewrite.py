"""Destructive rewriting of a repository's commit history."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from busy_profile.git import GitError, run, run_raw
from busy_profile.plan import PlannedCommit, WriteFile

TEMP_BRANCH = "busy-profile-rewrite"
TEMP_REF = f"refs/heads/{TEMP_BRANCH}"

StageCallback = Callable[[str], None]

_IDENT_DATE = re.compile(r" \d+ [+-]\d{4}$")


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
class Identity:
    """The ``Name <email>`` strings git stamps on a commit."""

    author: str
    committer: str


def current_branch(repo: Path) -> str:
    """Return the name of the checked out branch.

    Works on an unborn branch (a repository with no commits yet) but fails on a
    detached HEAD, which we cannot safely rewrite.
    """
    try:
        return run(repo, "symbolic-ref", "--short", "HEAD")
    except GitError as error:
        raise GitError(
            "HEAD is detached; check out a branch before rewriting history"
        ) from error


def commit_count(repo: Path) -> int:
    """How many commits are reachable from HEAD, or 0 on an unborn branch."""
    try:
        return int(run(repo, "rev-list", "--count", "HEAD"))
    except GitError:
        return 0


def assert_rewritable(repo: Path) -> str:
    """Return the checked out branch, or raise :class:`GitError`.

    A repository can be rewritten when it is a non-bare repository with a branch
    checked out and no leftover temporary branch from a previous run.
    """
    if not repo.is_dir():
        raise GitError(f"{repo} is not a directory")
    if run(repo, "rev-parse", "--is-bare-repository") == "true":
        raise GitError(f"{repo} is a bare repository")
    branch = current_branch(repo)
    if run(repo, "branch", "--list", TEMP_BRANCH):
        raise GitError(
            f"branch {TEMP_BRANCH!r} already exists; delete it and try again"
        )
    return branch


def rewrite_history(
    repo: Path,
    branch: str,
    commits: Sequence[PlannedCommit],
    *,
    on_stage: StageCallback | None = None,
) -> None:
    """Replace the history of ``branch`` in ``repo`` with the planned ``commits``.

    ``branch`` is what :func:`assert_rewritable` returned for ``repo``; the
    caller checks the repository once, before asking the user to confirm, and
    hands the answer on rather than having it checked again here.

    The working tree is staged and snapshotted into the first commit. Every
    commit then applies its planned changes on top of its parent's tree. The
    previous history is discarded.

    The commits are built by ``git fast-import``, which writes them all as one
    packfile in a single pass. Nothing touches HEAD or the working tree until
    that import has succeeded, so a failure part-way through leaves the
    repository as it was, apart from the now-staged index.
    """
    total = f"{len(commits):,}"

    def stage(message: str) -> None:
        if on_stage is not None:
            on_stage(message)

    stage("reading git identity")
    identity = _identity(repo)

    stage("staging the working tree")
    snapshot = _stage_entries(repo)

    stage(f"building {total} commits")
    stream = _import_stream(commits, snapshot, identity)

    try:
        stage(f"importing {total} commits")
        run_raw(repo, "fast-import", "--quiet", "--done", stdin=stream)

        stage(f"pointing {branch} at the new history")
        run(repo, "update-ref", f"refs/heads/{branch}", TEMP_REF)
        run(repo, "reset", "--hard", "--quiet", branch)
    finally:
        # Deleting a ref that was never created is a no-op, so this is safe
        # whether the import finished, failed, or was interrupted.
        run(repo, "update-ref", "-d", TEMP_REF)


def _identity(repo: Path) -> Identity:
    """The identity git itself would use, as author and committer.

    Asking git rather than reading config means the usual precedence (env vars,
    local, global, ``useConfigOnly``) applies, and that a repository with no
    configured identity fails here exactly as ``git commit`` would.
    """
    return Identity(
        author=_IDENT_DATE.sub("", run(repo, "var", "GIT_AUTHOR_IDENT")),
        committer=_IDENT_DATE.sub("", run(repo, "var", "GIT_COMMITTER_IDENT")),
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


def format_raw_date(timestamp: datetime) -> str:
    """Format a timestamp as ``git fast-import`` raw dates: epoch then offset.

    fast-import has no ISO 8601 date format, so the offset has to be carried
    separately from the instant to keep it out of the commit's stored timezone.
    """
    return f"{int(timestamp.timestamp())} {timestamp:%z}"


def _import_stream(
    commits: Sequence[PlannedCommit],
    snapshot: Sequence[IndexEntry],
    identity: Identity,
) -> bytes:
    """Build the fast-import stream for the whole history.

    Commits with no ``from`` chain onto the branch tip, so the first is a root
    commit and the rest are linear. Only the first carries ``snapshot``; every
    later commit inherits its parent's tree and applies its own changes.
    """
    chunks = _commit_chunks(commits[0], snapshot, identity)
    for commit in commits[1:]:
        chunks.extend(_commit_chunks(commit, (), identity))
    chunks.append(b"done\n")
    return b"".join(chunks)


def _commit_chunks(
    commit: PlannedCommit,
    entries: Sequence[IndexEntry],
    identity: Identity,
) -> list[bytes]:
    message = commit.message.encode()
    stamp = format_raw_date(commit.timestamp)

    chunks = [
        f"commit {TEMP_REF}\n".encode(),
        f"author {identity.author} {stamp}\n".encode(),
        f"committer {identity.committer} {stamp}\n".encode(),
        f"data {len(message)}\n".encode() + message + b"\n",
    ]
    chunks.extend(
        f"M {entry.mode} {entry.sha} ".encode() + entry.path + b"\n"
        for entry in entries
    )
    for change in commit.changes:
        if isinstance(change, WriteFile):
            blob = change.content.encode()
            chunks.append(f"M 100644 inline {change.path}\n".encode())
            chunks.append(f"data {len(blob)}\n".encode() + blob)
        else:
            # fast-import renames whole subdirectories as readily as files.
            chunks.append(f"R {change.source} {change.destination}\n".encode())
    return chunks
