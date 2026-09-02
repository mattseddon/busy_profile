"""Destructive rewriting of a repository's commit history."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from pathlib import Path

from busy_profile.git import GitError, run, run_raw
from busy_profile.plan import PlannedCommit, format_raw_date
from busy_profile.text import RANDOM_TEXT_FILE

TEMP_BRANCH = "busy-profile-rewrite"
TEMP_REF = f"refs/heads/{TEMP_BRANCH}"

StageCallback = Callable[[str], None]

_IDENT_DATE = re.compile(r" \d+ [+-]\d{4}$")


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


def assert_rewritable(repo: Path) -> None:
    """Raise :class:`GitError` unless ``repo`` is a repo we can rewrite."""
    if not repo.is_dir():
        raise GitError(f"{repo} is not a directory")
    if run(repo, "rev-parse", "--is-bare-repository") == "true":
        raise GitError(f"{repo} is a bare repository")
    current_branch(repo)
    if run(repo, "branch", "--list", TEMP_BRANCH):
        raise GitError(
            f"branch {TEMP_BRANCH!r} already exists; delete it and try again"
        )


def rewrite_history(
    repo: Path,
    commits: Sequence[PlannedCommit],
    *,
    on_stage: StageCallback | None = None,
) -> None:
    """Replace the history of ``repo`` with the given planned ``commits``.

    Every commit writes its message into ``random_text``, overwriting whatever
    the previous commit put there. The first commit additionally carries whatever
    working tree snapshot the plan was built with. The previous history is
    discarded.

    The commits are built by ``git fast-import``, which writes them all as one
    packfile in a single pass. Nothing touches HEAD or the working tree until
    that import has succeeded, so a failure part-way through leaves the
    repository exactly as it was.
    """
    if not commits:
        raise ValueError("commits must not be empty")
    if any(commit.timestamp.tzinfo is None for commit in commits):
        raise ValueError("commit timestamps must be timezone-aware")

    assert_rewritable(repo)
    branch = current_branch(repo)
    total = f"{len(commits):,}"

    def stage(message: str) -> None:
        if on_stage is not None:
            on_stage(message)

    stage("reading git identity")
    author, committer = _identity(repo)

    stage(f"building {total} commits")
    stream = _import_stream(commits, author, committer)

    stage(f"importing {total} commits")
    run_raw(repo, "fast-import", "--quiet", "--done", stdin=stream)

    stage(f"pointing {branch} at the new history")
    try:
        run(repo, "update-ref", f"refs/heads/{branch}", TEMP_REF)
        run(repo, "reset", "--hard", "--quiet", branch)
    finally:
        run(repo, "update-ref", "-d", TEMP_REF)


def _identity(repo: Path) -> tuple[str, str]:
    """The ``Name <email>`` git itself would use, author and committer.

    Asking git rather than reading config means the usual precedence (env vars,
    local, global, ``useConfigOnly``) applies, and that a repository with no
    configured identity fails here exactly as ``git commit`` would.
    """
    return (
        _IDENT_DATE.sub("", run(repo, "var", "GIT_AUTHOR_IDENT")),
        _IDENT_DATE.sub("", run(repo, "var", "GIT_COMMITTER_IDENT")),
    )


def _import_stream(
    commits: Sequence[PlannedCommit],
    author: str,
    committer: str,
) -> bytes:
    """Build the fast-import stream for the whole history.

    Commits with no ``from`` chain onto the branch tip, so the first is a root
    commit and the rest are linear.
    """
    chunks: list[bytes] = []
    for commit in commits:
        message = commit.message.encode()
        blob = f"{commit.message}\n".encode()
        stamp = format_raw_date(commit.timestamp)

        chunks.append(f"commit {TEMP_REF}\n".encode())
        chunks.append(f"author {author} {stamp}\n".encode())
        chunks.append(f"committer {committer} {stamp}\n".encode())
        chunks.append(f"data {len(message)}\n".encode() + message + b"\n")
        for entry in commit.entries:
            chunks.append(f"M {entry.mode} {entry.sha} ".encode() + entry.path + b"\n")
        chunks.append(f"M 100644 inline {RANDOM_TEXT_FILE}\n".encode())
        chunks.append(f"data {len(blob)}\n".encode() + blob)
    chunks.append(b"done\n")
    return b"".join(chunks)
