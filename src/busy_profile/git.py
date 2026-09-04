"""Talking to git: running it, checking a repository, and writing commits.

Both :mod:`busy_profile.rewrite` and :mod:`busy_profile.append` check the
repository the same way, build the same ``git fast-import`` stream from planned
commits, and land the imported commits on the branch through the same
temporary ref. That common ground lives here so that neither imports the other.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from busy_profile.plan import Move, PlannedCommit, WriteFile

TEMP_BRANCH = "busy-profile-rewrite"
TEMP_REF = f"refs/heads/{TEMP_BRANCH}"

StageCallback = Callable[[str], None]

_IDENT_DATE = re.compile(r" \d+ [+-]\d{4}$")
_NEEDS_QUOTING = re.compile(rb'[ "\\\n]')


class GitError(RuntimeError):
    """A git invocation failed, or the repository is not in a usable state."""


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


def run(repo: Path, *args: str) -> str:
    """Run git in ``repo`` and return its stdout, stripped and decoded."""
    return run_raw(repo, *args).decode().strip()


def run_raw(repo: Path, *args: str, stdin: bytes | None = None) -> bytes:
    """Run git in ``repo`` and return its stdout as bytes."""
    process = subprocess.run(
        ["git", *args],
        cwd=repo,
        input=stdin,
        capture_output=True,
        check=False,
    )
    if process.returncode != 0:
        detail = (process.stderr or process.stdout).decode(errors="replace").strip()
        raise GitError(f"`git {' '.join(args)}` failed: {detail}")
    return process.stdout


def current_branch(repo: Path) -> str:
    """Return the name of the checked out branch.

    Works on an unborn branch (a repository with no commits yet) but fails on a
    detached HEAD, which we cannot safely write to.
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


def assert_writable(repo: Path) -> str:
    """Return the checked out branch, or raise :class:`GitError`.

    Both rewriting and appending need a non-bare repository with a branch
    checked out and no leftover temporary branch from a previous run. Appending
    adds requirements of its own in :func:`busy_profile.append.assert_appendable`.
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


def stager(on_stage: StageCallback | None) -> StageCallback:
    """Wrap an optional progress callback so callers can report unconditionally."""

    def stage(message: str) -> None:
        if on_stage is not None:
            on_stage(message)

    return stage


def identity(repo: Path) -> Identity:
    """The identity git itself would use, as author and committer.

    Asking git rather than reading config means the usual precedence (env vars,
    local, global, ``useConfigOnly``) applies, and that a repository with no
    configured identity fails here exactly as ``git commit`` would.
    """
    return Identity(
        author=_IDENT_DATE.sub("", run(repo, "var", "GIT_AUTHOR_IDENT")),
        committer=_IDENT_DATE.sub("", run(repo, "var", "GIT_COMMITTER_IDENT")),
    )


def land(
    repo: Path,
    branch: str,
    stream: bytes,
    stage: StageCallback,
    *,
    importing: str,
    pointing: str,
    old_tip: str | None,
) -> None:
    """Import ``stream`` onto the temporary ref, then move ``branch`` to it.

    fast-import writes every commit as one packfile in a single pass. Nothing
    touches HEAD or the working tree until that import has succeeded, so a
    failure part-way through leaves the branch as it was.

    ``old_tip`` makes ``update-ref`` refuse to move a branch that has changed
    since it was read. Deleting a ref that was never created is a no-op, so the
    cleanup is safe whether the import finished, failed, or was interrupted.
    """
    try:
        stage(importing)
        run_raw(repo, "fast-import", "--quiet", "--done", stdin=stream)

        stage(pointing)
        expected = () if old_tip is None else (old_tip,)
        run(repo, "update-ref", f"refs/heads/{branch}", TEMP_REF, *expected)
        run(repo, "reset", "--hard", "--quiet", branch)
    finally:
        run(repo, "update-ref", "-d", TEMP_REF)


def format_raw_date(timestamp: datetime) -> str:
    """Format a timestamp as ``git fast-import`` raw dates: epoch then offset.

    fast-import has no ISO 8601 date format, so the offset has to be carried
    separately from the instant to keep it out of the commit's stored timezone.
    """
    return f"{int(timestamp.timestamp())} {timestamp:%z}"


def import_stream(
    commits: Sequence[PlannedCommit],
    who: Identity,
    *,
    snapshot: Sequence[IndexEntry] = (),
    parent: str | None = None,
) -> bytes:
    """Build the fast-import stream for a run of commits.

    Commits with no ``from`` chain onto the branch tip, so the first is a root
    commit unless ``parent`` names an existing commit for it to follow, and the
    rest are linear. Only the first carries ``snapshot``; every later commit
    inherits its parent's tree and applies its own changes.
    """
    chunks = _commit_chunks(commits[0], who, entries=snapshot, parent=parent)
    for commit in commits[1:]:
        chunks.extend(_commit_chunks(commit, who))
    chunks.append(b"done\n")
    return b"".join(chunks)


def _commit_chunks(
    commit: PlannedCommit,
    who: Identity,
    *,
    entries: Sequence[IndexEntry] = (),
    parent: str | None = None,
) -> list[bytes]:
    message = commit.message.encode()
    stamp = format_raw_date(commit.timestamp)

    chunks = [
        f"commit {TEMP_REF}\n".encode(),
        f"author {who.author} {stamp}\n".encode(),
        f"committer {who.committer} {stamp}\n".encode(),
        f"data {len(message)}\n".encode() + message + b"\n",
    ]
    if parent is not None:
        chunks.append(f"from {parent}\n".encode())
    chunks.extend(
        f"M {entry.mode} {entry.sha} ".encode() + entry.path + b"\n"
        for entry in entries
    )
    for change in commit.changes:
        if isinstance(change, WriteFile):
            blob = change.content.encode()
            chunks.append(b"M 100644 inline " + _path(change.path) + b"\n")
            chunks.append(f"data {len(blob)}\n".encode() + blob)
        elif isinstance(change, Move):
            # fast-import renames whole subdirectories as readily as files.
            source, destination = _path(change.source), _path(change.destination)
            chunks.append(b"R " + source + b" " + destination + b"\n")
        else:
            # Likewise, deleting a directory path removes everything beneath it.
            chunks.append(b"D " + _path(change.path) + b"\n")
    return chunks


def _path(path: str) -> bytes:
    """Encode a path for the import stream, quoting it when fast-import requires.

    A rename's source is terminated by a space, and any path may contain a
    quote, backslash or newline, so such paths go in C-style quotes.
    """
    raw = path.encode(errors="surrogateescape")
    if not _NEEDS_QUOTING.search(raw) and not raw.startswith(b'"'):
        return raw
    escaped = raw.replace(b"\\", b"\\\\").replace(b'"', b'\\"').replace(b"\n", b"\\n")
    return b'"' + escaped + b'"'
